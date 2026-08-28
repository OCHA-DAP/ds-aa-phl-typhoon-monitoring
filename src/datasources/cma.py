"""Read and parse CMA tropical cyclone bulletins from blob storage.

Two products are handled:

* the **live feed** of WMO WTPQ/TCPQ bulletins issued by BABJ (Beijing),
  dropped one file per bulletin under :data:`~src.constants.CMA_LIVE_PREFIX`;
* the **2004-2025 WNP archive** parsed in ``pa-aa-phl-storms`` notebook 11.1
  and cached as a parquet, used to re-derive return periods.

A live subjective-forecast bulletin looks like this::

    ZCZC
    WTPQ20 BABJ 131200
    SUBJECTIVE FORECAST
    SuperTY SINLAKU 2604 (2604) INITIAL TIME 131200 UTC
    00HR 13.1N 147.4E 905HPA 68M/S
    30KTS WINDS 500KM NORTHEAST
                480KM SOUTHEAST
                450KM SOUTHWEST
                450KM NORTHWEST
    50KTS WINDS 160KM NORTHEAST
    ...
    MOVE NW 14KM/H
    P+12HR 14.3N 146.5E 910HPA 65M/S
    ...
    P+120HR 25.2N 150.4E 980HPA 30M/S=

Quadrant wind radii are only ever given for the 00HR analysis, never for the
forecast steps. See :mod:`src.monitoring.exposure` for how they are carried
along the forecast track.
"""

import re
from datetime import datetime, timezone

import ocha_stratus as stratus
import pandas as pd

from src.constants import (
    BLOB_CONTAINER,
    BLOB_STAGE,
    CMA_ARCHIVE_PARQUET,
    CMA_LIVE_PREFIX,
    MS_TO_KT,
)

QUADS = ["ne", "se", "sw", "nw"]

_QUAD_NAMES = {
    "NORTHEAST": "ne",
    "SOUTHEAST": "se",
    "SOUTHWEST": "sw",
    "NORTHWEST": "nw",
}

# SuperTY SINLAKU 2604 (2604) INITIAL TIME 131200 UTC
_HEADER_RE = re.compile(
    r"^(?P<category>[A-Za-z]+)\s+(?P<name>[A-Z\-]+)\s+(?P<storm_id>\d{4})"
    r"\s*\((?P<alt_id>[^)]*)\)\s*INITIAL\s+TIME\s+(?P<issue>\d{6})\s*UTC"
)

# 00HR 13.1N 147.4E 905HPA 68M/S  /  P+120HR 25.2N 150.4E 980HPA 30M/S=
_POSITION_RE = re.compile(
    r"^(?:P\+)?(?P<fh>\d{1,3})HR\s+"
    r"(?P<lat>\d+(?:\.\d+)?)(?P<ns>[NS])\s+"
    r"(?P<lon>\d+(?:\.\d+)?)(?P<ew>[EW])\s+"
    r"(?P<pres>\d+)HPA\s+"
    r"(?P<wind>\d+(?:\.\d+)?)M/S"
)

# 30KTS WINDS 500KM NORTHEAST
_RADII_HEAD_RE = re.compile(
    r"^(?P<speed>\d+)KTS?\s+WINDS\s+(?P<km>\d+)KM\s+(?P<quad>[A-Z]+)"
)

# continuation line: 480KM SOUTHEAST
_RADII_CONT_RE = re.compile(r"^(?P<km>\d+)KM\s+(?P<quad>[A-Z]+)")

# MOVE NNW 22KM/H
_MOVE_RE = re.compile(r"^MOVE\s+(?P<dir>[NSEW]{1,3})\s+(?P<speed>\d+)KM/H")

# WTPQ20 BABJ 280900
_WMO_RE = re.compile(r"^(?P<ttaaii>[A-Z]{4}\d{2})\s+BABJ\s+(?P<ddhhmm>\d{6})")

_SKIP_LINES = {"ZCZC", "NNNN", "SUBJECTIVE FORECAST"}


def _blob_timestamp(blob_name: str) -> datetime:
    """Pull the issue timestamp out of the bulletin filename.

    The bulletin body only carries a ddhhmm group with no year or month, so
    the filename is the only reliable source of the full timestamp.
    """
    stem = blob_name.rsplit("/", 1)[-1]
    match = re.search(r"_(\d{14})\d*\.TXT$", stem, flags=re.IGNORECASE)
    if match is None:
        raise ValueError(f"No timestamp in bulletin filename: {blob_name}")
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc
    )


def list_bulletin_blobs(
    since: datetime | None = None,
    stage: str = BLOB_STAGE,
    container_name: str = BLOB_CONTAINER,
) -> list:
    """List live CMA bulletin blobs, oldest first.

    Parameters
    ----------
    since
        If given, only bulletins issued strictly after this time are returned.
    """
    blobs = sorted(
        b
        for b in stratus.list_container_blobs(
            name_starts_with=CMA_LIVE_PREFIX,
            stage=stage,
            container_name=container_name,
        )
        if b.upper().endswith(".TXT")
    )
    if since is None:
        return blobs
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return [b for b in blobs if _blob_timestamp(b) > since]


def load_bulletin_text(
    blob_name: str,
    stage: str = BLOB_STAGE,
    container_name: str = BLOB_CONTAINER,
) -> str:
    """Download a single bulletin and decode it to text."""
    raw = stratus.load_blob_data(
        blob_name, stage=stage, container_name=container_name
    )
    return raw.decode("utf-8", errors="replace")


def parse_bulletin(text: str, issue_time: datetime):
    """Parse one subjective-forecast bulletin.

    Returns None for bulletins with no SUBJECTIVE FORECAST block (the TCPQ
    tabular products), which carry no forecast positions and so cannot be
    used for either trigger.

    Returns
    -------
    dict or None
        Keys: storm_name, storm_id, category, issue_time, wmo_header,
        move_dir, move_speed_kmh, radii ({speed_kt: {quad: km}}) and
        positions (list of dicts with fh, lat, lon, pressure_hpa, wind_ms,
        wind_kt, valid_time).
    """
    if "SUBJECTIVE FORECAST" not in text.upper():
        return None

    record = {
        "issue_time": issue_time,
        "wmo_header": None,
        "storm_name": None,
        "storm_id": None,
        "category": None,
        "move_dir": None,
        "move_speed_kmh": None,
        "radii": {},
        "positions": [],
    }
    current_speed = None

    for raw_line in text.splitlines():
        line = raw_line.strip().rstrip("=").strip()
        if not line or line in _SKIP_LINES:
            continue

        wmo = _WMO_RE.match(line)
        if wmo:
            record["wmo_header"] = wmo.group("ttaaii")
            continue

        header = _HEADER_RE.match(line)
        if header:
            record["category"] = header.group("category")
            record["storm_name"] = header.group("name")
            record["storm_id"] = header.group("storm_id")
            current_speed = None
            continue

        position = _POSITION_RE.match(line)
        if position:
            lat = float(position.group("lat"))
            lon = float(position.group("lon"))
            if position.group("ns") == "S":
                lat = -lat
            if position.group("ew") == "W":
                lon = -lon
            fh = int(position.group("fh"))
            wind_ms = float(position.group("wind"))
            record["positions"].append(
                {
                    "fh": fh,
                    "lat": lat,
                    "lon": lon,
                    "pressure_hpa": int(position.group("pres")),
                    "wind_ms": wind_ms,
                    "wind_kt": wind_ms * MS_TO_KT,
                    "valid_time": issue_time + pd.Timedelta(hours=fh),
                }
            )
            current_speed = None
            continue

        radii_head = _RADII_HEAD_RE.match(line)
        if radii_head:
            current_speed = int(radii_head.group("speed"))
            quad = _QUAD_NAMES.get(radii_head.group("quad"))
            record["radii"].setdefault(current_speed, {})
            if quad:
                record["radii"][current_speed][quad] = float(
                    radii_head.group("km")
                )
            continue

        if current_speed is not None:
            radii_cont = _RADII_CONT_RE.match(line)
            if radii_cont:
                quad = _QUAD_NAMES.get(radii_cont.group("quad"))
                if quad:
                    record["radii"][current_speed][quad] = float(
                        radii_cont.group("km")
                    )
                continue

        move = _MOVE_RE.match(line)
        if move:
            record["move_dir"] = move.group("dir")
            record["move_speed_kmh"] = int(move.group("speed"))
            current_speed = None

    if not record["positions"] or record["storm_name"] is None:
        return None
    return record


def bulletin_to_frame(bulletin: dict) -> pd.DataFrame:
    """Flatten a parsed bulletin into one row per forecast step.

    The 00HR quadrant radii are attached to every row as
    radius_{speed}_{quad}_km columns, since CMA does not forecast radii.
    """
    df = pd.DataFrame(bulletin["positions"])
    for key in (
        "storm_name",
        "storm_id",
        "category",
        "issue_time",
        "wmo_header",
        "move_dir",
        "move_speed_kmh",
    ):
        df[key] = bulletin[key]
    for speed, quads in bulletin["radii"].items():
        for quad in QUADS:
            df[f"radius_{speed}_{quad}_km"] = quads.get(quad)
    return df


def load_bulletin(
    blob_name: str,
    stage: str = BLOB_STAGE,
    container_name: str = BLOB_CONTAINER,
):
    """Download and parse a single bulletin blob."""
    text = load_bulletin_text(
        blob_name, stage=stage, container_name=container_name
    )
    bulletin = parse_bulletin(text, _blob_timestamp(blob_name))
    if bulletin is not None:
        bulletin["blob_name"] = blob_name
    return bulletin


def load_latest_bulletins(
    since: datetime | None = None,
    stage: str = BLOB_STAGE,
    container_name: str = BLOB_CONTAINER,
) -> list:
    """Load and parse every live bulletin issued after ``since``."""
    bulletins = []
    for blob_name in list_bulletin_blobs(
        since=since, stage=stage, container_name=container_name
    ):
        bulletin = load_bulletin(
            blob_name, stage=stage, container_name=container_name
        )
        if bulletin is not None:
            bulletins.append(bulletin)
    return bulletins


def latest_bulletin_per_storm(bulletins: list) -> list:
    """Keep only the most recent bulletin for each storm."""
    latest = {}
    for bulletin in bulletins:
        key = bulletin["storm_id"]
        if (
            key not in latest
            or bulletin["issue_time"] > latest[key]["issue_time"]
        ):
            latest[key] = bulletin
    return sorted(latest.values(), key=lambda b: b["issue_time"])


def load_archive_tracks(
    stage: str = BLOB_STAGE, container_name: str = BLOB_CONTAINER
) -> pd.DataFrame:
    """Load the parsed 2004-2025 WNP archive used for the trigger analysis."""
    df = stratus.load_parquet_from_blob(
        CMA_ARCHIVE_PARQUET, stage=stage, container_name=container_name
    )
    df["analysis_ts"] = pd.to_datetime(
        df["analysis_time"], format="%Y%m%d%H%M"
    )
    return df
