"""Philippines administrative boundaries used for triggers and exposure.

Boundaries are cached on blob. Fetching them from fieldmaps took about a
minute on every run, which was the single largest cost in the pipeline and
pure overhead: admin boundaries change a few times a year, not every 15
minutes. The cache is refreshed when it passes CACHE_MAX_AGE_DAYS, and any
cache failure falls back to fieldmaps so monitoring never breaks on it.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import ocha_stratus as stratus
import pandas as pd
from shapely import wkb

from src.constants import (
    BLOB_CONTAINER,
    BLOB_STAGE,
    ISO3,
    PROJECT_PREFIX,
    REGION_NAMES,
    TARGET_REGIONS,
)

CACHE_MAX_AGE_DAYS = 30

# The blob cache still costs a 26 MB download per admin level, because PHL
# coastline geometry is large. A local copy makes warm runs a file read; on
# CI it is kept between runs by actions/cache, so fieldmaps and blob are both
# hit only when the key rolls over.
LOCAL_CACHE_DIR = Path(os.getenv("CODAB_CACHE_DIR", ".codab_cache"))


def _cache_blob(admin_level: int) -> str:
    return f"{PROJECT_PREFIX}/cache/codab_adm{admin_level}.parquet"


def _local_path(admin_level: int) -> Path:
    return LOCAL_CACHE_DIR / f"codab_adm{admin_level}.parquet"


def _read_local(admin_level: int):
    """Return boundaries from the local cache, or None."""
    path = _local_path(admin_level)
    if not path.exists():
        return None
    try:
        age_days = (
            datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        ) / 86400
        if age_days > CACHE_MAX_AGE_DAYS:
            return None
        df = pd.read_parquet(path)
        geometry = df["_geometry_wkb"].apply(wkb.loads)
        df = df.drop(columns=["_geometry_wkb", "_cached_at"])
        return gpd.GeoDataFrame(df, geometry=geometry, crs=4326)
    except Exception:
        return None


def _write_local(df: pd.DataFrame, admin_level: int) -> None:
    """Write the WKB frame to the local cache. Never raises."""
    try:
        LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(_local_path(admin_level), index=False)
    except Exception as exc:  # noqa: BLE001
        print(f"    could not write local CODAB cache: {exc}")


def _to_wkb_frame(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["_geometry_wkb"] = gdf.geometry.apply(lambda g: g.wkb)
    df["_cached_at"] = datetime.now(timezone.utc).isoformat()
    return df


def _read_cache(admin_level: int):
    """Return the cached boundaries, or None if absent, stale or unreadable.

    Geometry is stored as WKB in an ordinary parquet rather than geoparquet,
    so the cache does not depend on the storage layer understanding geometry
    types.
    """
    try:
        df = stratus.load_parquet_from_blob(
            _cache_blob(admin_level),
            stage=BLOB_STAGE,
            container_name=BLOB_CONTAINER,
        )
    except Exception:
        return None

    try:
        cached_at = pd.to_datetime(df["_cached_at"].iloc[0], utc=True)
        age_days = (datetime.now(timezone.utc) - cached_at).days
        if age_days > CACHE_MAX_AGE_DAYS:
            return None
        geometry = df["_geometry_wkb"].apply(wkb.loads)
        df = df.drop(columns=["_geometry_wkb", "_cached_at"])
        return gpd.GeoDataFrame(df, geometry=geometry, crs=4326)
    except Exception:
        return None


def _write_cache(df: pd.DataFrame, admin_level: int) -> None:
    """Cache boundaries to blob. Never raises: caching is an optimisation."""
    try:
        stratus.upload_parquet_to_blob(
            df,
            _cache_blob(admin_level),
            stage=BLOB_STAGE,
            container_name=BLOB_CONTAINER,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    could not cache CODAB adm{admin_level}: {exc}")


def load_adm(admin_level: int = 1, use_cache: bool = True):
    """Load PHL CODAB at the requested admin level.

    Tried in order: local file, blob cache, fieldmaps. Each miss populates
    the caches above it, so a cold runner pays once and every later run in
    the same cache window is a local read.
    """
    if use_cache:
        local = _read_local(admin_level)
        if local is not None:
            return local

        cached = _read_cache(admin_level)
        if cached is not None:
            _write_local(_to_wkb_frame(cached), admin_level)
            return cached

    gdf = stratus.codab.load_codab_from_fieldmaps(
        iso3=ISO3, admin_level=admin_level
    ).to_crs(4326)
    if use_cache:
        frame = _to_wkb_frame(gdf)
        _write_cache(frame, admin_level)
        _write_local(frame, admin_level)
    return gdf


def _pcode_col(gdf: gpd.GeoDataFrame, admin_level: int) -> str:
    """Find the source pcode column, whose name varies between releases."""
    candidates = [
        c
        for c in gdf.columns
        if "src" in c.lower() and str(admin_level) in c
    ]
    if not candidates:
        candidates = [c for c in gdf.columns if "src" in c.lower()]
    if not candidates:
        raise KeyError(f"No pcode column found in {list(gdf.columns)}")
    return candidates[0]


def load_target_regions() -> gpd.GeoDataFrame:
    """Load the admin-1 regions covered by the framework.

    Region 13 (PH16, Caraga) is included alongside the four regions used in
    the CMA readiness return-period analysis.
    """
    adm1 = load_adm(admin_level=1)
    col = _pcode_col(adm1, 1)
    gdf = adm1[adm1[col].isin(TARGET_REGIONS)].copy()
    gdf["region_pcode"] = gdf[col]
    gdf["region_name"] = gdf["region_pcode"].map(REGION_NAMES)
    missing = set(TARGET_REGIONS) - set(gdf["region_pcode"])
    if missing:
        raise ValueError(f"Target regions missing from CODAB: {missing}")
    return gdf.to_crs(4326)


def adm_columns(admin_level: int):
    """Pcode and name column names for a CODAB admin level.

    Fieldmaps CODAB carries the pcode in ``adm{n}_src`` and the label in
    ``adm{n}_name``.
    """
    return f"adm{admin_level}_src", f"adm{admin_level}_name"


def load_exposure_adm(admin_level: int = 2) -> gpd.GeoDataFrame:
    """Load the admin units that exposure is aggregated to."""
    return load_adm(admin_level=admin_level).to_crs(4326)
