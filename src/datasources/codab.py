"""Philippines administrative boundaries used for triggers and exposure."""

import geopandas as gpd
import ocha_stratus as stratus

from src.constants import ISO3, REGION_NAMES, TARGET_REGIONS


def load_adm(admin_level: int = 1) -> gpd.GeoDataFrame:
    """Load PHL CODAB at the requested admin level."""
    return stratus.codab.load_codab_from_fieldmaps(
        iso3=ISO3, admin_level=admin_level
    )


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
