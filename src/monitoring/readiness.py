"""Readiness trigger check against a live CMA forecast bulletin.

Readiness activates when a CMA forecast shows a qualifying landfall in a
target region at or above 177 kph (1-minute sustained). It is monitored from
the moment that forecast is released right up to landfall, so there is no
minimum lead time: a storm that only reaches threshold two days out still
activates readiness.

This is wider than the 4-7 day window used for the return periods in
``pa-aa-phl-storms`` notebooks 11.1 and 14. Activation here will therefore be
more frequent than the 3.1 year return period computed on that window.
"""

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from src.constants import (
    KNOTS_TO_KMH,
    LANDFALL_BUFFER_KM,
    ONE_MIN_TO_TEN_MIN,
    ONE_MIN_TO_TWO_MIN,
    READINESS_MAX_LEAD_H,
    READINESS_MIN_LEAD_H,
    READINESS_THRESHOLD_KPH_1MIN,
    READINESS_THRESHOLD_KT,
)
from src.datasources.cma import bulletin_to_frame
from src.utils.categories import category_from_kt


def _buffered_geometry(gdf: gpd.GeoDataFrame, buffer_km: float):
    """Return region geometries, optionally buffered by a distance in km."""
    if not buffer_km:
        return gdf.geometry
    # Buffer in a metric CRS, then return to lon/lat for the crossing test.
    return (
        gdf.to_crs(3857).geometry.buffer(buffer_km * 1000).to_crs(4326)
    )


def find_forecast_landfalls(
    bulletin: dict,
    regions: gpd.GeoDataFrame,
    threshold_kt: float = READINESS_THRESHOLD_KT,
    buffer_km: float = LANDFALL_BUFFER_KM,
) -> pd.DataFrame:
    """Find where the forecast track crosses each target region.

    A crossing counts as a qualifying landfall only when both endpoints of the
    track segment are at or above ``threshold_kt``, mirroring the historical
    analysis. The first qualifying crossing per region is returned.

    Returns
    -------
    pd.DataFrame
        One row per region with a qualifying crossing: ``region_pcode``,
        ``region_name``, ``lead_hours``, ``landfall_time`` and the
        intensity CMA expects at landfall (``landfall_wind_kt``,
        ``landfall_wind_kph_2min``, ``landfall_wind_kph_1min``).
    """
    track = bulletin_to_frame(bulletin).sort_values("fh").reset_index(
        drop=True
    )
    geometries = _buffered_geometry(regions, buffer_km)

    rows = []
    for (_, region), geom in zip(regions.iterrows(), geometries):
        for i in range(len(track) - 1):
            p0, p1 = track.iloc[i], track.iloc[i + 1]
            if min(p0["wind_kt"], p1["wind_kt"]) < threshold_kt:
                continue
            segment = LineString(
                [(p0["lon"], p0["lat"]), (p1["lon"], p1["lat"])]
            )
            if not segment.intersects(geom):
                continue
            landfall_kt = float(min(p0["wind_kt"], p1["wind_kt"]))
            rows.append(
                {
                    "region_pcode": region["region_pcode"],
                    "region_name": region["region_name"],
                    "lead_hours": int(p0["fh"]),
                    "landfall_time": p0["valid_time"],
                    # Intensity CMA expects at landfall, on the segment that
                    # crosses the region.
                    "landfall_wind_kt": landfall_kt,
                    "landfall_wind_kph_2min": landfall_kt * KNOTS_TO_KMH,
                    "landfall_wind_kph_1min": (
                        landfall_kt * KNOTS_TO_KMH / ONE_MIN_TO_TWO_MIN
                    ),
                    "landfall_wind_kph_10min": (
                        landfall_kt
                        * KNOTS_TO_KMH
                        / ONE_MIN_TO_TWO_MIN
                        * ONE_MIN_TO_TEN_MIN
                    ),
                    "landfall_category": category_from_kt(landfall_kt),
                }
            )
            break
    return pd.DataFrame(rows)


def expected_landfall(
    bulletin: dict,
    regions: gpd.GeoDataFrame,
    buffer_km: float = LANDFALL_BUFFER_KM,
) -> dict:
    """What CMA expects this storm to make landfall as, at any intensity.

    Unlike :func:`find_forecast_landfalls`, this applies no intensity
    threshold, so it answers the question that should always be reported:
    if this storm reaches a target region, what will it be when it does.
    """
    landfalls = find_forecast_landfalls(
        bulletin, regions, threshold_kt=0, buffer_km=buffer_km
    )
    result = {
        "makes_landfall": False,
        "landfall_category": None,
        "landfall_wind_kt": None,
        "landfall_wind_kph_1min": None,
        "landfall_wind_kph_10min": None,
        "landfall_time": None,
        "lead_hours": None,
        "regions": [],
        "detail": landfalls,
    }
    if landfalls.empty:
        return result

    strongest = landfalls.loc[landfalls["landfall_wind_kt"].idxmax()]
    result.update(
        {
            "makes_landfall": True,
            "landfall_category": strongest["landfall_category"],
            "landfall_wind_kt": float(strongest["landfall_wind_kt"]),
            "landfall_wind_kph_1min": float(
                strongest["landfall_wind_kph_1min"]
            ),
            "landfall_wind_kph_10min": float(
                strongest["landfall_wind_kph_10min"]
            ),
            "landfall_time": landfalls["landfall_time"].min(),
            "lead_hours": int(landfalls["lead_hours"].min()),
            "regions": sorted(landfalls["region_name"]),
        }
    )
    return result


def check_readiness(
    bulletin: dict,
    regions: gpd.GeoDataFrame,
    threshold_kt: float = READINESS_THRESHOLD_KT,
    min_lead_h: int = READINESS_MIN_LEAD_H,
    max_lead_h: int = READINESS_MAX_LEAD_H,
    buffer_km: float = LANDFALL_BUFFER_KM,
) -> dict:
    """Evaluate the readiness trigger for one bulletin.

    Returns
    -------
    dict
        ``triggered`` is the trigger decision. ``status`` is one of:

        ``no_forecast``
            Analysis-only bulletin, no forecast positions to assess.
        ``below_threshold``
            Storm is not forecast to reach threshold intensity anywhere.
        ``no_landfall``
            Forecast at or above threshold, but the track does not cross a
            target region.
        ``awaiting_window``
            Qualifying landfall forecast, but beyond the outer bound of the
            window. Keep watching.
        ``triggered``
            Qualifying landfall forecast, at any lead time from release up
            to landfall.

    ``phase`` places the storm on the timeline independently of the trigger
    decision: ``before_window``, ``in_window`` or ``no_landfall_forecast``.
    ``monitoring_active`` is True while a qualifying landfall is still
    ahead, which is the signal to keep reporting on this storm.
    """
    result = {
        "storm_name": bulletin["storm_name"],
        "storm_id": bulletin["storm_id"],
        "category": bulletin["category"],
        "issue_time": bulletin["issue_time"],
        "blob_name": bulletin.get("blob_name"),
        "threshold_kt": threshold_kt,
        "threshold_kph_1min": READINESS_THRESHOLD_KPH_1MIN,
        "max_forecast_wind_kt": None,
        "triggered": False,
        "status": "no_forecast",
        "landfall_regions": [],
        "lead_hours": None,
        "landfall_time": None,
        "landfall_wind_kt": None,
        "landfall_wind_kph_1min": None,
        "landfall_wind_kph_10min": None,
        "landfall_category": None,
        "expected_landfall": None,
        "phase": "no_landfall_forecast",
        "monitoring_active": False,
        "hours_to_landfall": None,
        "landfalls": pd.DataFrame(),
    }

    # Always report what CMA expects landfall to look like, whatever the
    # readiness decision turns out to be.
    result["expected_landfall"] = expected_landfall(
        bulletin, regions, buffer_km=buffer_km
    )
    result["landfall_category"] = result["expected_landfall"][
        "landfall_category"
    ]

    track = bulletin_to_frame(bulletin)
    result["max_forecast_wind_kt"] = float(track["wind_kt"].max())

    if len(track) < 2:
        return result

    if result["max_forecast_wind_kt"] < threshold_kt:
        result["status"] = "below_threshold"
        return result

    landfalls = find_forecast_landfalls(
        bulletin, regions, threshold_kt=threshold_kt, buffer_km=buffer_km
    )
    result["landfalls"] = landfalls

    if landfalls.empty:
        result["status"] = "no_landfall"
        return result

    # A qualifying landfall is forecast. Populate the landfall detail
    # whatever the lead time, so the storm keeps being reported all the way
    # in rather than going quiet once the readiness window closes.
    result["lead_hours"] = int(landfalls["lead_hours"].min())
    result["hours_to_landfall"] = result["lead_hours"]
    result["landfall_time"] = landfalls["landfall_time"].min()
    result["landfall_regions"] = sorted(landfalls["region_name"])
    result["landfall_wind_kt"] = float(landfalls["landfall_wind_kt"].max())
    result["landfall_wind_kph_1min"] = float(
        landfalls["landfall_wind_kph_1min"].max()
    )
    result["landfall_wind_kph_10min"] = float(
        landfalls["landfall_wind_kph_10min"].max()
    )
    result["monitoring_active"] = True

    in_window = landfalls[
        (landfalls["lead_hours"] >= min_lead_h)
        & (landfalls["lead_hours"] <= max_lead_h)
    ]

    if not in_window.empty:
        result["triggered"] = True
        result["status"] = "triggered"
        result["phase"] = "in_window"
        result["lead_hours"] = int(in_window["lead_hours"].min())
        result["hours_to_landfall"] = result["lead_hours"]
        result["landfall_time"] = in_window["landfall_time"].min()
        result["landfall_regions"] = sorted(in_window["region_name"])
        result["landfall_wind_kt"] = float(
            in_window["landfall_wind_kt"].max()
        )
        result["landfall_wind_kph_1min"] = float(
            in_window["landfall_wind_kph_1min"].max()
        )
        result["landfall_wind_kph_10min"] = float(
            in_window["landfall_wind_kph_10min"].max()
        )
        return result

    if result["lead_hours"] > max_lead_h:
        result["status"] = "awaiting_window"
        result["phase"] = "before_window"
        return result

    # Lead is shorter than the readiness window. The trigger cannot be
    # reached from here, but the storm is still inbound, so monitoring
    # continues to landfall.
    result["status"] = "monitoring_to_landfall"
    result["phase"] = "after_window"
    return result
