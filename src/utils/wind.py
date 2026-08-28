"""Track interpolation and quadrant wind-buffer geometry.

Adapted from the Meteo-France wind-buffer code in ``ds-aa-mdg-monitoring`` so
that the two frameworks build swaths the same way. The differences here are
that CMA reports quadrant radii in kilometres rather than nautical miles, and
only for the 00HR analysis.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from shapely.geometry import Polygon

QUADS = ["ne", "se", "sw", "nw"]

# Bearing convention for the polar sweep: 0 deg = East, 90 deg = North.
_QUAD_BOUNDS = {
    "ne": (0, 90),
    "nw": (90, 180),
    "sw": (180, 270),
    "se": (270, 360),
}


def interpolate_track(
    df: pd.DataFrame,
    time_col: str = "valid_time",
    lat_col: str = "lat",
    lon_col: str = "lon",
    freq: str = "30min",
) -> pd.DataFrame:
    """Resample a track to a regular time grid.

    Latitude and longitude use a PCHIP spline (monotone, no overshoot at
    recurvature); every other numeric column is interpolated linearly.
    Longitude is kept in [0, 360) so tracks crossing the dateline stay
    contiguous.
    """
    work = df.copy()
    work[time_col] = pd.to_datetime(work[time_col], utc=True)
    work = work.sort_values(time_col).drop_duplicates(
        subset=[time_col], keep="first"
    )
    work = work.dropna(subset=[lat_col, lon_col])

    if work.empty:
        return work
    if len(work) == 1:
        return work.reset_index(drop=True)

    t0 = work[time_col].iloc[0]
    x = (work[time_col] - t0).dt.total_seconds().to_numpy()

    target = pd.date_range(
        work[time_col].min(), work[time_col].max(), freq=freq, tz="UTC"
    )
    if target.empty:
        target = pd.DatetimeIndex(
            [work[time_col].min(), work[time_col].max()]
        )
    x_new = (pd.Series(target) - t0).dt.total_seconds().to_numpy()

    lat = work[lat_col].to_numpy(float)
    lon = np.mod(work[lon_col].to_numpy(float), 360.0)

    out = pd.DataFrame(index=target)
    if len(work) >= 3:
        out[lat_col] = PchipInterpolator(x, lat)(x_new)
        out[lon_col] = np.mod(PchipInterpolator(x, lon)(x_new), 360.0)
    else:
        out[lat_col] = np.interp(x_new, x, lat)
        out[lon_col] = np.mod(np.interp(x_new, x, lon), 360.0)

    other = work.select_dtypes(include=[np.number]).columns.difference(
        [lat_col, lon_col]
    )
    for col in other:
        out[col] = np.interp(x_new, x, work[col].to_numpy(float))

    out.index.name = time_col
    return out.reset_index()


def _radius_from_quadrants(
    theta_deg: np.ndarray, ne: float, se: float, sw: float, nw: float
) -> np.ndarray:
    """Radius at each bearing, stepping between the four quadrant radii."""
    theta = np.mod(theta_deg, 360.0)
    radius = np.empty_like(theta, dtype=float)
    values = {"ne": ne, "se": se, "sw": sw, "nw": nw}
    for quad, (lo, hi) in _QUAD_BOUNDS.items():
        mask = (theta >= lo) & (theta < hi)
        radius[mask] = values[quad]
    return radius


def make_quadrant_disk(
    center_xy,
    ne: float,
    se: float,
    sw: float,
    nw: float,
    n_points: int = 360,
) -> Polygon:
    """Build a polygon around a centre point from four quadrant radii.

    All radii are in the units of the projected CRS of ``center_xy``
    (metres for EPSG:3857).
    """
    x0, y0 = center_xy
    theta = np.linspace(0, 360, n_points, endpoint=False)
    r = _radius_from_quadrants(theta, ne, se, sw, nw)
    th = np.deg2rad(theta)
    coords = np.column_stack([x0 + r * np.cos(th), y0 + r * np.sin(th)])
    return Polygon(coords)


def build_merged_wind_buffer(gdf: gpd.GeoDataFrame, quad_cols):
    """Union the per-point quadrant disks into a single swath polygon.

    ``quad_cols`` is the (ne, se, sw, nw) column names, in metres. Returns
    None if no point has any radius.
    """
    ne_col, se_col, sw_col, nw_col = quad_cols
    cols = [ne_col, se_col, sw_col, nw_col]
    if not set(cols).issubset(gdf.columns):
        return None
    radii = gdf[cols]
    if radii.isna().all().all() or (radii.fillna(0) <= 0).all().all():
        return None

    filled = radii.fillna(0)
    polys = [
        make_quadrant_disk(
            (point.x, point.y),
            row[ne_col],
            row[se_col],
            row[sw_col],
            row[nw_col],
        )
        for point, (_, row) in zip(gdf.geometry, filled.iterrows())
        if row.max() > 0
    ]
    if not polys:
        return None
    return gpd.GeoSeries(polys).union_all()
