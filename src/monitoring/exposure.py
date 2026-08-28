"""Population exposure to the forecast CMA wind field.

CMA publishes quadrant wind radii only for the 00HR analysis, never for the
forecast steps. To build a forecast swath the analysis radii are carried
forward along the interpolated forecast track, and the swath for a given wind
speed is truncated at the point where the storm is no longer forecast to reach
that speed. So a storm forecast to weaken below 64 kt after 48 h contributes no
64-kt swath beyond 48 h, while its 30-kt swath continues.

This is a deliberate simplification: real wind fields expand as a storm
weakens and recurves. Treat the swaths as an indication of who is in the path,
not as a calibrated wind footprint.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from rioxarray.exceptions import NoDataInBounds

from src.constants import EXPOSURE_SPEEDS_KT
from src.datasources.cma import bulletin_to_frame
from src.utils.wind import QUADS, build_merged_wind_buffer, interpolate_track


def _wrap_180(lon):
    """Normalise longitude to [-180, 180)."""
    return ((np.asarray(lon, dtype=float) + 180.0) % 360.0) - 180.0


def build_wind_buffers(
    bulletin: dict,
    speeds=None,
    freq: str = "30min",
) -> gpd.GeoDataFrame:
    """Build a merged wind swath polygon for each wind speed threshold.

    Returns
    -------
    gpd.GeoDataFrame
        One row per speed that has radii and a track long enough to sweep,
        with columns ``speed_kt`` and ``geometry`` in EPSG:4326.
    """
    if speeds is None:
        speeds = EXPOSURE_SPEEDS_KT

    track = bulletin_to_frame(bulletin).sort_values("fh")
    if track.empty:
        return gpd.GeoDataFrame(
            {"speed_kt": []}, geometry=[], crs=4326
        )

    interp = interpolate_track(
        track, time_col="valid_time", lat_col="lat", lon_col="lon", freq=freq
    )
    if interp.empty:
        return gpd.GeoDataFrame({"speed_kt": []}, geometry=[], crs=4326)
    interp["lon"] = _wrap_180(interp["lon"])

    gdf = gpd.GeoDataFrame(
        interp,
        geometry=gpd.points_from_xy(interp["lon"], interp["lat"]),
        crs=4326,
    ).to_crs(3857)

    records, geoms = [], []
    for speed in speeds:
        cols = [f"radius_{speed}_{quad}_km" for quad in QUADS]
        if not set(cols).issubset(gdf.columns):
            continue
        work = gdf.copy()
        # Radii are constant along the track (CMA gives them only at 00HR),
        # but the swath stops where the storm is no longer forecast to reach
        # this speed.
        reaches_speed = work["wind_kt"] >= speed
        for col in cols:
            work[col] = np.where(reaches_speed, work[col] * 1000.0, np.nan)
        geom = build_merged_wind_buffer(work, tuple(cols))
        if geom is None:
            continue
        records.append({"speed_kt": speed})
        geoms.append(geom)

    if not records:
        return gpd.GeoDataFrame({"speed_kt": []}, geometry=[], crs=4326)
    return gpd.GeoDataFrame(records, geometry=geoms, crs=3857).to_crs(4326)


def load_population(adm: gpd.GeoDataFrame) -> xr.DataArray:
    """Load the WorldPop count raster clipped to the given admin extent."""
    import ocha_stratus as stratus

    from src.constants import WORLDPOP_BLOB, WORLDPOP_CONTAINER

    da = stratus.open_blob_cog(
        WORLDPOP_BLOB, container_name=WORLDPOP_CONTAINER
    )
    da = da.rio.clip(adm.to_crs(4326).geometry).squeeze(drop=True).compute()
    da.attrs["_FillValue"] = None
    return da.where(da > 0)


def _exposure_for_buffers(
    buffers: gpd.GeoDataFrame, da_pop: xr.DataArray
) -> pd.DataFrame:
    """Population inside each buffer of an already-clipped raster."""
    records = []
    for _, row in buffers.iterrows():
        data = row.drop(labels="geometry").to_dict()
        if row.geometry is None or row.geometry.is_empty:
            data["pop_exposed"] = 0
        else:
            try:
                data["pop_exposed"] = int(
                    da_pop.rio.clip([row.geometry]).sum()
                )
            except (NoDataInBounds, ValueError):
                data["pop_exposed"] = 0
        records.append(data)
    return pd.DataFrame(records)


def calculate_exposure(
    buffers: gpd.GeoDataFrame,
    da_pop: xr.DataArray,
    adm: gpd.GeoDataFrame,
    pcode_col: str,
    name_col: str = None,
) -> pd.DataFrame:
    """Population exposed to each wind swath, per admin unit.

    ``all_touched=True`` is used when clipping to the admin unit so that no
    edge pixel is dropped, and left at the default when clipping to the
    buffer so that pixels are not double counted across swaths.
    """
    if buffers.empty:
        return pd.DataFrame(
            columns=["speed_kt", "pop_exposed", pcode_col]
        )

    buffers = buffers.to_crs(4326)
    adm = adm.to_crs(4326)

    frames = []
    for _, adm_row in adm.iterrows():
        try:
            da_adm = da_pop.rio.clip([adm_row.geometry], all_touched=True)
        except (NoDataInBounds, ValueError):
            continue
        df = _exposure_for_buffers(buffers, da_adm)
        df[pcode_col] = adm_row[pcode_col]
        if name_col is not None and name_col in adm_row:
            df[name_col] = adm_row[name_col]
        frames.append(df)

    if not frames:
        return pd.DataFrame(
            columns=["speed_kt", "pop_exposed", pcode_col]
        )
    return pd.concat(frames, ignore_index=True)


def summarise_exposure(
    df_exposure: pd.DataFrame, group_col: str = None
) -> pd.DataFrame:
    """Total exposed population by wind speed, optionally by admin unit."""
    if df_exposure.empty:
        return df_exposure
    keys = ["speed_kt"] + ([group_col] if group_col else [])
    return (
        df_exposure.groupby(keys, as_index=False)["pop_exposed"]
        .sum()
        .sort_values(keys, ascending=[False] + [True] * (len(keys) - 1))
        .reset_index(drop=True)
    )


def region_population(
    da_pop: xr.DataArray, regions: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Total population of each target region.

    The denominator is taken from the same WorldPop raster that supplies the
    numerator, so the share is internally consistent. Taking the denominator
    from a different source inflates it and understates the share.
    """
    records = []
    for _, row in regions.to_crs(4326).iterrows():
        try:
            total = int(
                da_pop.rio.clip([row.geometry], all_touched=True).sum()
            )
        except (NoDataInBounds, ValueError):
            total = 0
        records.append(
            {
                "region_pcode": row["region_pcode"],
                "region_name": row["region_name"],
                "region_pop": total,
            }
        )
    return pd.DataFrame(records)


def region_exposure(
    buffers: gpd.GeoDataFrame,
    da_pop: xr.DataArray,
    regions: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Population exposed per target region, as a count and as a share.

    Returns one row per region and wind speed with ``pop_exposed``,
    ``region_pop`` and ``share_exposed``.
    """
    if buffers.empty:
        return pd.DataFrame(
            columns=[
                "region_pcode",
                "region_name",
                "speed_kt",
                "pop_exposed",
                "region_pop",
                "share_exposed",
            ]
        )

    df_exposed = calculate_exposure(
        buffers,
        da_pop,
        regions.rename(columns={"region_name": "region_label"}),
        pcode_col="region_pcode",
        name_col="region_label",
    ).rename(columns={"region_label": "region_name"})

    df_pop = region_population(da_pop, regions)
    df = df_exposed.merge(
        df_pop[["region_pcode", "region_pop"]], on="region_pcode", how="left"
    )
    df["share_exposed"] = np.where(
        df["region_pop"] > 0, df["pop_exposed"] / df["region_pop"], np.nan
    )
    return df.sort_values(
        ["speed_kt", "share_exposed"], ascending=[False, False]
    ).reset_index(drop=True)


def check_exposure_trigger(
    df_region_exposure: pd.DataFrame,
    expected_landfall: dict = None,
    speed_kt: int = None,
    share_threshold: float = None,
) -> dict:
    """Evaluate the observational exposure trigger.

    Both conditions must hold:

    1. CMA expects the storm to make landfall in a target region as a
       **super typhoon** (2-minute sustained wind at or above 51.0 m/s,
       99.1 kt);
    2. at least ``share_threshold`` of that region's population lies inside
       the wind field at ``speed_kt``.

    ``expected_landfall`` is the dict from
    :func:`src.monitoring.readiness.expected_landfall`. If it is not given,
    only the population condition is evaluated and ``super_typhoon`` is
    reported as None so the caller can see the check was not made.
    """
    from src.constants import (
        EXPOSURE_SHARE_THRESHOLD,
        EXPOSURE_TRIGGER_SPEED_KT,
    )
    from src.utils.categories import SUPER_TYPHOON, is_super_typhoon

    speed_kt = speed_kt or EXPOSURE_TRIGGER_SPEED_KT
    share_threshold = (
        EXPOSURE_SHARE_THRESHOLD
        if share_threshold is None
        else share_threshold
    )

    result = {
        "triggered": False,
        "speed_kt": speed_kt,
        "share_threshold": share_threshold,
        "regions": [],
        "share_regions": [],
        "max_share": None,
        "max_share_region": None,
        "super_typhoon": None,
        "landfall_category": None,
        "landfall_wind_kt": None,
        "landfall_wind_kph_1min": None,
        "detail": pd.DataFrame(),
    }

    if expected_landfall is not None:
        result["landfall_category"] = expected_landfall["landfall_category"]
        result["landfall_wind_kt"] = expected_landfall["landfall_wind_kt"]
        result["landfall_wind_kph_1min"] = expected_landfall[
            "landfall_wind_kph_1min"
        ]
        result["super_typhoon"] = bool(
            expected_landfall["makes_landfall"]
            and is_super_typhoon(expected_landfall["landfall_wind_kt"])
        )

    if df_region_exposure.empty:
        return result

    sub = df_region_exposure[
        df_region_exposure["speed_kt"] == speed_kt
    ].copy()
    if sub.empty:
        return result

    result["detail"] = sub
    best = sub.loc[sub["share_exposed"].idxmax()]
    result["max_share"] = float(best["share_exposed"])
    result["max_share_region"] = best["region_name"]

    hit = sub[sub["share_exposed"] >= share_threshold]
    result["share_regions"] = sorted(hit["region_name"])

    if hit.empty:
        return result

    # Population condition met. The trigger needs the super typhoon landfall
    # as well, and it must be in a region that also meets the share.
    if not result["super_typhoon"]:
        return result

    landfall_regions = set(
        (expected_landfall or {}).get("regions", [])
    )
    qualifying = sorted(set(result["share_regions"]) & landfall_regions)
    if not qualifying:
        return result

    result["triggered"] = True
    result["regions"] = qualifying
    _ = SUPER_TYPHOON
    return result
def national_exposure(
    buffers: gpd.GeoDataFrame,
    da_pop: xr.DataArray,
    adm: gpd.GeoDataFrame,
    df_exposure: pd.DataFrame = None,
) -> pd.DataFrame:
    """Population exposed nationally, as a count and as a share of the country.

    The denominator is the total of the same WorldPop raster clipped to the
    country, so it is consistent with the regional shares.

    Pass ``df_exposure`` to reuse an admin-level result already computed
    rather than clipping the raster a second time.
    """
    national_pop = int(da_pop.sum())

    if df_exposure is not None and not df_exposure.empty:
        df = summarise_exposure(df_exposure)
    elif not buffers.empty:
        df = _exposure_for_buffers(buffers, da_pop)[
            ["speed_kt", "pop_exposed"]
        ]
    else:
        return pd.DataFrame(
            columns=["speed_kt", "pop_exposed", "national_pop",
                     "share_exposed"]
        )

    df = df.copy()
    df["national_pop"] = national_pop
    df["share_exposed"] = np.where(
        national_pop > 0, df["pop_exposed"] / national_pop, np.nan
    )
    return df.sort_values("speed_kt", ascending=False).reset_index(drop=True)
