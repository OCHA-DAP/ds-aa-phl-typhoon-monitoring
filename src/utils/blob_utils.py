"""Read and write the monitoring state and outputs on blob storage."""

from datetime import datetime, timezone

import ocha_stratus as stratus
import pandas as pd

from src.constants import (
    BLOB_CONTAINER,
    BLOB_STAGE,
    MONITORING_LOG_BLOB,
    OUTPUT_PREFIX,
)

LOG_COLUMNS = [
    "issue_time",
    "processed_time",
    "blob_name",
    "storm_id",
    "storm_name",
    "category",
    "status",
    "phase",
    "monitoring_active",
    "triggered",
    "previously_triggered",
    "hours_to_landfall",
    "lead_hours",
    "landfall_time",
    "landfall_regions",
    "landfall_wind_kt",
    "landfall_wind_kph_1min",
    "landfall_wind_kph_10min",
    "max_forecast_wind_kt",
    "expected_landfall_category",
    "expected_landfall_wind_kph_1min",
    "expected_landfall_wind_kph_10min",
    "expected_landfall_regions",
    "pop_exposed_64kt",
    "exposure_triggered",
    "exposure_trigger_regions",
    "max_region_share_exposed",
    "national_share_exposed_64kt",
    "exposure_source",
    "climada_pop_exposed_64kt",
    "climada_national_share_64kt",
    "climada_max_region_share",
    "climada_triggered",
    "email_campaign_id",
]


def load_monitoring_log(
    stage: str = BLOB_STAGE, container_name: str = BLOB_CONTAINER
) -> pd.DataFrame:
    """Load the log of bulletins already processed.

    Returns an empty frame with the expected columns if the log does not
    exist yet, so the first run works without any bootstrapping.
    """
    try:
        df = stratus.load_parquet_from_blob(
            MONITORING_LOG_BLOB, stage=stage, container_name=container_name
        )
    except Exception:
        return pd.DataFrame(columns=LOG_COLUMNS)
    for col in LOG_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df


def last_processed_time(df_log: pd.DataFrame):
    """Issue time of the most recent bulletin already processed."""
    if df_log.empty or df_log["issue_time"].isna().all():
        return None
    last = pd.to_datetime(df_log["issue_time"], utc=True).max()
    return last.to_pydatetime()


def append_monitoring_log(
    rows,
    stage: str = BLOB_STAGE,
    container_name: str = BLOB_CONTAINER,
) -> pd.DataFrame:
    """Append rows to the monitoring log and write it back to blob."""
    df_log = load_monitoring_log(stage=stage, container_name=container_name)
    df_new = pd.DataFrame(rows)
    if df_new.empty:
        return df_log
    df_new["processed_time"] = datetime.now(timezone.utc)
    df_out = pd.concat([df_log, df_new], ignore_index=True)
    df_out = df_out.drop_duplicates(subset=["blob_name"], keep="last")
    stratus.upload_parquet_to_blob(
        df_out,
        MONITORING_LOG_BLOB,
        stage=stage,
        container_name=container_name,
    )
    return df_out


def save_bulletin_outputs(
    bulletin: dict,
    df_exposure: pd.DataFrame,
    stage: str = BLOB_STAGE,
    container_name: str = BLOB_CONTAINER,
) -> str:
    """Write the per-admin exposure table for one bulletin to blob."""
    stamp = bulletin["issue_time"].strftime("%Y%m%d_%H%M")
    blob_name = (
        f"{OUTPUT_PREFIX}{bulletin['storm_id']}_{bulletin['storm_name']}"
        f"_{stamp}_exposure.parquet"
    )
    stratus.upload_parquet_to_blob(
        df_exposure, blob_name, stage=stage, container_name=container_name
    )
    return blob_name
