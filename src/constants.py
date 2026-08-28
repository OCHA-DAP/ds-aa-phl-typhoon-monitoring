"""Framework constants for PHL typhoon readiness and exposure monitoring."""

ISO3 = "PHL"
PROJECT_PREFIX = "ds-aa-phl-typhoon-monitoring"

# ---------------------------------------------------------------------------
# Blob locations
# ---------------------------------------------------------------------------
BLOB_CONTAINER = "projects"
BLOB_STAGE = "dev"

# Live CMA bulletin feed, dropped by the CMA FTP sync (one .TXT per bulletin)
CMA_LIVE_PREFIX = "ds-cma-datasharing/cma_ftp/data_out/typhoon/"

# Parsed 2004-2025 WNP archive used for the trigger return-period analysis
CMA_ARCHIVE_PARQUET = "ds-cma-datasharing/processed/cma_wnp_tc_tracks.parquet"

# Outputs written by this repo
MONITORING_LOG_BLOB = f"{PROJECT_PREFIX}/monitoring/monitoring_log.parquet"
OUTPUT_PREFIX = f"{PROJECT_PREFIX}/monitoring/bulletins/"

WORLDPOP_BLOB = (
    "worldpop/pop_count/global_pop_2026_CN_1km_R2025A_UA_v1.tif"
)
WORLDPOP_CONTAINER = "raster"

# ---------------------------------------------------------------------------
# Target regions
# ---------------------------------------------------------------------------
# Region 13 (PH16, Caraga) is monitored alongside the four regions used in the
# CMA readiness return-period analysis (analysis/11.1 in pa-aa-phl-storms).
TARGET_REGIONS = ["PH02", "PH03", "PH05", "PH08", "PH16"]

REGION_NAMES = {
    "PH02": "Region II (Cagayan Valley)",
    "PH03": "Region III (Central Luzon)",
    "PH05": "Region V (Bicol Region)",
    "PH08": "Region VIII (Eastern Visayas)",
    "PH16": "Region XIII (Caraga)",
}

# ---------------------------------------------------------------------------
# Wind speed conversions
# ---------------------------------------------------------------------------
# CMA reports 2-minute sustained winds in m/s. Framework thresholds are stated
# as 1-minute sustained kph, so they are converted down by 0.93 before being
# compared against CMA values.
MS_TO_KT = 1.944
KNOTS_TO_KMH = 1.852
ONE_MIN_TO_TWO_MIN = 0.93
# WMO conversion between averaging periods: a 10-minute sustained wind is
# about 0.88 of the 1-minute sustained wind for the same storm.
ONE_MIN_TO_TEN_MIN = 0.88
NAUTICAL_MILE_TO_KM = 1.852

# ---------------------------------------------------------------------------
# Readiness trigger
# ---------------------------------------------------------------------------
READINESS_THRESHOLD_KPH_1MIN = 177
READINESS_THRESHOLD_KPH_2MIN = (
    READINESS_THRESHOLD_KPH_1MIN * ONE_MIN_TO_TWO_MIN
)  # 164.6 kph
READINESS_THRESHOLD_KT = READINESS_THRESHOLD_KPH_2MIN / KNOTS_TO_KMH  # 88.9 kt

# Lead-time window on the forecast landfall, in hours. Readiness is monitored
# from the moment CMA releases a forecast showing a qualifying landfall right
# up to landfall itself, so there is no minimum lead. The maximum simply
# bounds the window; CMA forecasts only reach 120 h in any case.
#
# Note this is wider than the 4-7 day window used for the return periods in
# pa-aa-phl-storms notebook 11.1, so activations will be more frequent than
# the 3.1 year return period computed there.
READINESS_MIN_LEAD_H = 0
READINESS_MAX_LEAD_H = 168

# Forecast track is treated as making landfall if it crosses the region
# boundary. A non-zero buffer (in km) also counts near-misses.
LANDFALL_BUFFER_KM = 0

# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------
# Wind speeds (kt, 2-min) for which CMA publishes quadrant radii
# Exposure is assessed at 64 kt only, the wind speed the trigger is defined
# on. Lower wind bands are not reported.
EXPOSURE_SPEEDS_KT = [64]
EXPOSURE_ADM_LEVEL = 2
EXPOSURE_ADM_PCODE_COL = "adm2_src"

# Observational exposure trigger (pa-aa-phl-storms notebook 13): at least half
# of a target region's population inside the 64-kt wind field. Exposure is
# always reported both ways, as an absolute headcount and as a share of the
# region, since the share is what the trigger is stated in.
EXPOSURE_TRIGGER_SPEED_KT = 64
EXPOSURE_SHARE_THRESHOLD = 0.5

# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
# A storm is in play, and so reported on every new forecast whether or not a
# trigger is reached, when its CMA forecast track either enters the Philippine
# Area of Responsibility or comes within this distance of a target region.
RELEVANCE_BUFFER_KM = 500

# PAGASA's Philippine Area of Responsibility. Storms inside it are the ones
# The Philippines is actually watching, so this is the reporting boundary
# rather than a plain radius around the target regions.
PAR_POLYGON = [
    (115.0, 5.0),
    (115.0, 15.0),
    (120.0, 21.0),
    (120.0, 25.0),
    (135.0, 25.0),
    (135.0, 5.0),
    (115.0, 5.0),
]

# Every new CMA forecast for a storm in play produces an email. There is no
# cooldown: the monitoring log deduplicates on bulletin blob name, so one new
# forecast means exactly one email.

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
LISTMONK_LIST_ID = 121
LISTMONK_LIST_ID_TEST = 103

# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
CHD_GREEN = "#14805a"
CHD_BLUE = "#1a6faf"
CHD_RED = "#c25048"
