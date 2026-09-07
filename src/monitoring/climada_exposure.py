"""Wind footprint from a CMA bulletin using the CLIMADA Holland model.

This is the second of the two exposure sources the monitoring reports, so
that the CMA quadrant radii and a modelled wind field can be compared on the
same storm before one is settled on.

| Source | Footprint |
|---|---|
| ``exposure.build_wind_buffers`` | CMA's own 64 kt quadrant radii, observed at 00HR and carried along the forecast track |
| this module | Holland 2008 wind field (CLIMADA ``TropCyclone``) evaluated at every forecast step |

The output is deliberately shaped exactly like ``build_wind_buffers``: a
GeoDataFrame with ``speed_kt`` and ``geometry``. Everything downstream in
:mod:`src.monitoring.exposure` is then reused unchanged, so the two numbers
differ only because the footprint geometry differs, not because they were
computed by different code.

Two conversions matter and are made explicit rather than assumed:

* CMA reports **2-minute** sustained wind. CLIMADA's Holland implementation
  expects **1-minute**, so winds are divided by 0.93 on the way in.
* CLIMADA returns 1-minute sustained wind, so the 64 kt threshold is applied
  on a 1-minute basis. The CMA radii path instead uses CMA's own 2-minute
  64 kt radii. That difference is real and is part of what the comparison is
  meant to expose.

CMA does not publish a radius of maximum wind, so it is estimated from central
pressure with CLIMADA's own ``estimate_rmw``.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import shape
from shapely.ops import unary_union

from src.constants import (
    EXPOSURE_TRIGGER_SPEED_KT,
    KNOTS_TO_KMH,
    MS_TO_KT,
    ONE_MIN_TO_TWO_MIN,
)
from src.datasources.cma import bulletin_to_frame

# Grid the wind field is evaluated on, at the IBF pipeline's 0.05 degrees.
# Only The Philippines is covered: exposure is the only consumer of this
# field, so evaluating it over open ocean is wasted work. The box was once
# widened to follow the track, which kept the swath from being clipped when
# it was drawn on a map; there is no CLIMADA map any more, and the wider grid
# cost roughly 35 extra seconds per bulletin.
GRID_BOUNDS = (116.0, 4.0, 127.0, 22.0)  # lon_min, lat_min, lon_max, lat_max
GRID_RES = 0.05
GRID_MARGIN_DEG = 0.0

ENVIRONMENTAL_PRESSURE_MB = 1010.0

# CLIMADA discards wind below this, so a footprint threshold under it would be
# silently empty.
CLIMADA_INTENSITY_THRESHOLD_MS = 17.5


def _kt_1min(wind_ms_2min):
    """CMA 2-minute m/s to 1-minute knots, which is what Holland expects."""
    return np.asarray(wind_ms_2min, dtype=float) * MS_TO_KT / ONE_MIN_TO_TWO_MIN


def bulletin_to_tctracks(bulletin: dict):
    """Build a CLIMADA ``TCTracks`` holding this bulletin's forecast track."""
    import xarray as xr
    from climada.hazard import TCTracks
    from climada.hazard.tc_tracks import estimate_rmw

    track = bulletin_to_frame(bulletin).sort_values("fh").reset_index(
        drop=True
    )
    if len(track) < 2:
        return None

    times = pd.to_datetime(track["valid_time"]).dt.tz_localize(None)
    central_pressure = track["pressure_hpa"].astype(float).to_numpy()

    # CMA publishes no radius of maximum wind, so estimate it from pressure.
    radius_max_wind = estimate_rmw(
        np.full(len(track), np.nan), central_pressure
    )

    time_step = np.diff(times.to_numpy()).astype("timedelta64[m]")
    time_step = np.append(
        time_step, time_step[-1] if len(time_step) else np.timedelta64(6, "h")
    )
    time_step_h = time_step.astype(float) / 60.0

    ds = xr.Dataset(
        data_vars={
            "max_sustained_wind": (
                "time",
                _kt_1min(track["wind_ms"].to_numpy()),
            ),
            "central_pressure": ("time", central_pressure),
            "environmental_pressure": (
                "time",
                np.full(len(track), ENVIRONMENTAL_PRESSURE_MB),
            ),
            "radius_max_wind": ("time", np.asarray(radius_max_wind, float)),
            "time_step": ("time", time_step_h),
            # basin must be a time-varying variable, not an attribute:
            # CLIMADA resamples it, and an attribute silently resolves to a
            # bare string that has no resample method.
            "basin": ("time", np.full(len(track), "WP", dtype=object)),
        },
        coords={
            "time": times.to_numpy(),
            # lat/lon are coordinates in CLIMADA's own readers, and
            # _one_interp_data reassigns them as such after resampling.
            "lat": ("time", track["lat"].to_numpy(float)),
            "lon": ("time", track["lon"].to_numpy(float)),
        },
        attrs={
            "max_sustained_wind_unit": "kn",
            "central_pressure_unit": "mb",
            "name": bulletin["storm_name"],
            "sid": str(bulletin["storm_id"]),
            "orig_event_flag": True,
            "data_provider": "CMA",
            "id_no": int(bulletin["storm_id"]),
            "basin": "WP",
            "category": 0,
        },
    )

    tracks = TCTracks()
    tracks.data = [ds]
    # Sub-hourly steps so the swath is continuous rather than a string of
    # separate blobs at 12 hourly forecast positions.
    tracks.equal_timestep(time_step_h=0.5)
    return tracks


def _grid(track=None):
    """Regular lon/lat grid used as CLIMADA centroids.

    The base box covers The Philippines, which is all exposure needs. A track
    only widens the box if GRID_MARGIN_DEG is non-zero, which it is not by
    default: the swath is clipped to the box, and outside The Philippines
    there is nothing to expose.
    """
    lon_min, lat_min, lon_max, lat_max = GRID_BOUNDS
    if GRID_MARGIN_DEG and track is not None and len(track):
        lon_min = min(lon_min, float(track["lon"].min()) - GRID_MARGIN_DEG)
        lon_max = max(lon_max, float(track["lon"].max()) + GRID_MARGIN_DEG)
        lat_min = min(lat_min, float(track["lat"].min()) - GRID_MARGIN_DEG)
        lat_max = max(lat_max, float(track["lat"].max()) + GRID_MARGIN_DEG)
    lats = np.arange(lat_min, lat_max + GRID_RES, GRID_RES)
    lons = np.arange(lon_min, lon_max + GRID_RES, GRID_RES)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    return lats, lons, lat_grid, lon_grid


def wind_field(bulletin: dict):
    """Maximum 1-minute sustained wind (m/s) on the grid, as a 2D array.

    Returns ``(array, lats, lons)``, or ``(None, None, None)`` if the
    bulletin has no usable forecast track.
    """
    from climada.hazard import TropCyclone
    from climada.hazard.centroids import Centroids

    tracks = bulletin_to_tctracks(bulletin)
    if tracks is None:
        return None, None, None

    lats, lons, lat_grid, lon_grid = _grid(bulletin_to_frame(bulletin))
    centroids = Centroids.from_lat_lon(lat_grid.flatten(), lon_grid.flatten())

    tc = TropCyclone.from_tracks(tracks, centroids=centroids)
    if tc.intensity.shape[0] == 0:
        return None, None, None

    intensity = np.asarray(tc.intensity.max(axis=0).todense()).ravel()
    return intensity.reshape(lat_grid.shape), lats, lons


def build_climada_buffers(
    bulletin: dict, speeds=None
) -> gpd.GeoDataFrame:
    """Wind swath polygons from the CLIMADA wind field.

    Shaped like :func:`src.monitoring.exposure.build_wind_buffers` so the two
    sources can be run through the same exposure code.
    """
    from rasterio.features import shapes
    from rasterio.transform import from_origin

    if speeds is None:
        speeds = [EXPOSURE_TRIGGER_SPEED_KT]

    empty = gpd.GeoDataFrame({"speed_kt": []}, geometry=[], crs=4326)

    field, lats, lons = wind_field(bulletin)
    if field is None:
        return empty

    transform = from_origin(
        lons[0] - GRID_RES / 2,
        lats[-1] + GRID_RES / 2,
        GRID_RES,
        GRID_RES,
    )

    records, geoms = [], []
    for speed in speeds:
        threshold_ms = speed * KNOTS_TO_KMH / 3.6
        if threshold_ms < CLIMADA_INTENSITY_THRESHOLD_MS:
            raise ValueError(
                f"{speed} kt is below the CLIMADA intensity floor of "
                f"{CLIMADA_INTENSITY_THRESHOLD_MS} m/s, so the footprint "
                "would be empty for the wrong reason"
            )
        # Flip north-up to match the transform.
        mask = (np.flipud(field) >= threshold_ms).astype(np.uint8)
        if not mask.any():
            continue
        polys = [
            shape(geom)
            for geom, value in shapes(mask, mask=mask.astype(bool),
                                      transform=transform)
            if value == 1
        ]
        if not polys:
            continue
        records.append({"speed_kt": speed})
        geoms.append(unary_union(polys))

    if not records:
        return empty
    return gpd.GeoDataFrame(records, geometry=geoms, crs=4326)
