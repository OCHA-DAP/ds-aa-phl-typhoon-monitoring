"""Readiness and exposure monitoring from live CMA forecast bulletins.

Polls the CMA bulletin feed on blob, evaluates the readiness trigger and the
population exposed to the forecast wind field, writes the results back to
blob, and emails the distribution list for storms that approach the
Philippines.

Run the last unprocessed bulletins:

    python pipelines/monitor_cma.py

Re-run one specific bulletin without touching the log or sending mail:

    python pipelines/monitor_cma.py --blob <blob name> --dry-run
"""

import argparse
import sys
from datetime import datetime, timezone

import matplotlib
import pandas as pd

matplotlib.use("Agg")

from src.constants import (  # noqa: E402
    EXPOSURE_ADM_LEVEL,
    PAR_POLYGON,
    EXPOSURE_SHARE_THRESHOLD,
    EXPOSURE_SPEEDS_KT,
    RELEVANCE_BUFFER_KM,
)
from src.datasources import cma, codab  # noqa: E402
from src.utils.categories import expand_category  # noqa: E402
from src.monitoring import exposure as exp  # noqa: E402
from src.monitoring import plotting, readiness  # noqa: E402
from src.monitoring.emails import send_monitoring_email  # noqa: E402
from src.utils.blob_utils import (  # noqa: E402
    append_monitoring_log,
    last_processed_time,
    load_monitoring_log,
    save_bulletin_outputs,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Monitor CMA typhoon bulletins for readiness and exposure"
    )
    parser.add_argument(
        "--blob",
        type=str,
        default=None,
        help="Process one specific bulletin blob instead of polling the feed",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help=(
            "Process bulletins issued after this UTC time (YYYY-MM-DDTHH:MM). "
            "Defaults to the last bulletin in the monitoring log."
        ),
    )
    parser.add_argument(
        "--max-bulletins",
        type=int,
        default=20,
        help="Cap on bulletins processed in one run (default 20)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send to the test list with a [test] subject prefix",
    )
    parser.add_argument(
        "--force-email",
        action="store_true",
        help="Send an email regardless of relevance and cooldown checks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute everything but send no email and write nothing to blob",
    )
    return parser.parse_args()


def is_relevant(bulletin, regions, buffer_km=RELEVANCE_BUFFER_KM) -> bool:
    """Is this storm in play for The Philippines?

    True when any CMA forecast position falls inside the Philippine Area of
    Responsibility, or within ``buffer_km`` of a target region. Storms in
    play are reported on every new forecast, whether or not a trigger is
    reached; storms elsewhere in the Western North Pacific are not.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    track = cma.bulletin_to_frame(bulletin)
    points = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(track["lon"], track["lat"]), crs=4326
    )

    par = Polygon(PAR_POLYGON)
    if points.intersects(par).any():
        return True

    region_union = regions.to_crs(3857).geometry.union_all()
    return bool(
        (points.to_crs(3857).distance(region_union) <= buffer_km * 1000).any()
    )


def _national_share(df_national, speed_kt):
    """National share exposed at one wind speed, or None."""
    if df_national is None or df_national.empty:
        return None
    row = df_national[df_national["speed_kt"] == speed_kt]
    if row.empty:
        return None
    return float(row["share_exposed"].iloc[0])


def should_email(
    result, df_log, relevant: bool, force: bool, exposure_trigger=None
) -> bool:
    """Decide whether this bulletin warrants an email.

    Every new CMA forecast for a storm close to The Philippines is reported,
    whether or not a trigger is reached. Close means the forecast track
    enters the Philippine Area of Responsibility or comes within 500 km of a
    target region. Storms elsewhere in the Western North Pacific are logged
    but not emailed.

    There is no cooldown: the monitoring log deduplicates on bulletin blob
    name, so a bulletin is processed once and produces at most one email.
    """
    if force:
        return True
    return bool(relevant)


def _swath_reaches_country(buffers, adm) -> bool:
    """Can the forecast wind field touch The Philippines at all?

    A cheap exact check that avoids loading the population raster for storms
    whose wind field is nowhere near land. It is not an approximation: if no
    swath intersects any admin unit, the exposed population is zero.
    """
    if buffers is None or buffers.empty:
        return False
    country = adm.to_crs(4326).geometry.union_all()
    return bool(buffers.to_crs(4326).intersects(country).any())


def _previously_triggered(df_log, storm_id) -> bool:
    """Was readiness already reached for this storm on an earlier bulletin?"""
    if df_log.empty or "triggered" not in df_log.columns:
        return False
    prior = df_log[df_log["storm_id"] == storm_id]
    if prior.empty:
        return False
    return bool(prior["triggered"].fillna(False).astype(bool).any())


def _lazy_population(adm):
    """Return a callable that loads the WorldPop raster once, on demand."""
    cache = {}

    def loader():
        if "da" not in cache:
            print("    loading WorldPop population raster...")
            cache["da"] = exp.load_population(adm)
        return cache["da"]

    return loader


def process_bulletin(
    bulletin, regions, adm, adm1, pop_loader, pcode_col, name_col, args,
    df_log,
):
    """Evaluate one bulletin and return its monitoring log row."""
    label = (
        f"{expand_category(bulletin['category'])} {bulletin['storm_name']} "
        f"({bulletin['storm_id']}) issued "
        f"{bulletin['issue_time']:%Y-%m-%d %H:%M UTC}"
    )
    print(f"\n--- {label}")

    result = readiness.check_readiness(bulletin, regions)
    result["previously_triggered"] = _previously_triggered(
        df_log, result["storm_id"]
    )
    if result["previously_triggered"] and not result["triggered"]:
        print(
            "    readiness was already reached for this storm on an "
            "earlier CMA bulletin"
        )
    print(
        f"    readiness: {result['status']} "
        f"| peak forecast {result['max_forecast_wind_kt']:.0f} kt"
    )
    if result["landfall_wind_kph_1min"]:
        print(
            "    expected at landfall: "
            f"{result['landfall_wind_kph_1min']:.0f} kph (1-min) in "
            f"{', '.join(result['landfall_regions'])} "
            f"at {result['lead_hours']} h lead"
        )

    expected = result["expected_landfall"]
    if expected["makes_landfall"]:
        print(
            "    CMA expects landfall as a "
            f"{expected['landfall_category']} "
            f"({expected['landfall_wind_kph_1min']:.0f} kph 1-min, "
            f"{expected['landfall_wind_kph_10min']:.0f} kph 10-min, "
            f"{expected['landfall_wind_kt']:.0f} kt 2-min) in "
            f"{', '.join(expected['regions'])}"
        )
    else:
        print("    CMA forecast shows no landfall in a target region")

    relevant = is_relevant(bulletin, regions) or result["monitoring_active"]
    print(f"    in play for the framework: {relevant}")
    if result["monitoring_active"]:
        print(
            f"    readiness phase: {result['phase']} "
            f"| {result['hours_to_landfall']} h to forecast landfall"
        )

    totals, df_summary, df_exposure = {}, None, None
    df_region, exposure_trigger, df_national = None, None, None
    buffers = exp.build_wind_buffers(bulletin, speeds=EXPOSURE_SPEEDS_KT)

    reaches_country = _swath_reaches_country(buffers, adm)
    if not reaches_country and not buffers.empty:
        print("    wind field does not reach land, exposure is zero")

    if reaches_country:
        da_pop = pop_loader()
        df_exposure = exp.calculate_exposure(
            buffers, da_pop, adm, pcode_col, name_col
        )
        df_summary = exp.summarise_exposure(df_exposure, group_col=name_col)
        totals = (
            exp.summarise_exposure(df_exposure)
            .set_index("speed_kt")["pop_exposed"]
            .to_dict()
        )
        for speed, value in sorted(totals.items(), reverse=True):
            print(f"    exposure {int(speed)} kt: {int(value):,} people")

        # Exposure is reported both ways: absolute headcount above, and the
        # share of each target region, which is how the trigger is stated.
        df_region = exp.region_exposure(buffers, da_pop, regions)
        exposure_trigger = exp.check_exposure_trigger(
            df_region, expected_landfall=result["expected_landfall"]
        )
        for _, r in df_region[
            df_region["speed_kt"] == exposure_trigger["speed_kt"]
        ].iterrows():
            print(
                f"    {r['region_name']}: {r['share_exposed']:.0%} of region "
                f"({int(r['pop_exposed']):,} of {int(r['region_pop']):,})"
            )
        print(
            "    exposure trigger "
            f"({EXPOSURE_SHARE_THRESHOLD:.0%} of a region at "
            f"{exposure_trigger['speed_kt']} kt): "
            f"{'REACHED' if exposure_trigger['triggered'] else 'not reached'}"
        )
        print(
            "      super typhoon landfall: "
            f"{exposure_trigger['super_typhoon']} "
            f"| regions meeting the share: "
            f"{exposure_trigger['share_regions'] or 'none'}"
        )

        df_national = exp.national_exposure(
            buffers, da_pop, adm, df_exposure=df_exposure
        )
        for _, r in df_national.iterrows():
            print(
                f"    national {int(r['speed_kt'])} kt: "
                f"{r['share_exposed']:.1%} of The Philippines "
                f"({int(r['pop_exposed']):,} of {int(r['national_pop']):,})"
            )
    elif buffers.empty:
        print("    no wind radii in this bulletin, exposure not computed")

    row = {
        "issue_time": bulletin["issue_time"],
        "blob_name": bulletin.get("blob_name"),
        "storm_id": result["storm_id"],
        "storm_name": result["storm_name"],
        "category": expand_category(result["category"]),
        "status": result["status"],
        "phase": result["phase"],
        "monitoring_active": result["monitoring_active"],
        "triggered": result["triggered"],
        "previously_triggered": result["previously_triggered"],
        "hours_to_landfall": result["hours_to_landfall"],
        "lead_hours": result["lead_hours"],
        "landfall_time": result["landfall_time"],
        "landfall_regions": ", ".join(result["landfall_regions"]),
        "landfall_wind_kt": result["landfall_wind_kt"],
        "landfall_wind_kph_1min": result["landfall_wind_kph_1min"],
        "expected_landfall_category": expected["landfall_category"],
        "expected_landfall_wind_kph_1min": expected["landfall_wind_kph_1min"],
        "expected_landfall_wind_kph_10min": (
            expected["landfall_wind_kph_10min"]
        ),
        "expected_landfall_regions": ", ".join(expected["regions"]),
        "max_forecast_wind_kt": result["max_forecast_wind_kt"],
        "pop_exposed_64kt": totals.get(64),
        "exposure_triggered": (
            exposure_trigger["triggered"] if exposure_trigger else None
        ),
        "exposure_trigger_regions": (
            ", ".join(exposure_trigger["regions"]) if exposure_trigger else ""
        ),
        "max_region_share_exposed": (
            exposure_trigger["max_share"] if exposure_trigger else None
        ),
        "national_share_exposed_64kt": _national_share(df_national, 64),
        "email_campaign_id": None,
    }

    if not should_email(
        result, df_log, relevant, args.force_email, exposure_trigger
    ):
        print("    no email for this bulletin")
        return row, df_exposure

    fig_map = plotting.plot_forecast_map(
        bulletin, regions, buffers=buffers, adm1=adm1, readiness_result=result
    )
    fig_region_share = plotting.plot_region_share(df_region)

    if args.dry_run:
        print("    dry run, email not sent")
        return row, df_exposure

    campaign_id = send_monitoring_email(
        result,
        fig_map,
        fig_region_share=fig_region_share,
        exposure_trigger=exposure_trigger,
        df_national=df_national,
        test=args.test,
    )
    print(f"    email sent, Listmonk campaign {campaign_id}")
    row["email_campaign_id"] = campaign_id
    return row, df_exposure


def main():
    args = parse_args()

    print("Loading target regions and admin boundaries...")
    regions = codab.load_target_regions()
    adm1 = codab.load_adm(1)
    adm = codab.load_exposure_adm(admin_level=EXPOSURE_ADM_LEVEL)
    pcode_col, name_col = codab.adm_columns(EXPOSURE_ADM_LEVEL)
    print(f"  exposure aggregated to {pcode_col} ({len(adm)} units)")

    df_log = load_monitoring_log()

    if args.blob:
        blobs = [args.blob]
    else:
        if args.since:
            since = datetime.fromisoformat(args.since).replace(
                tzinfo=timezone.utc
            )
        else:
            since = last_processed_time(df_log)
        print(f"Polling CMA feed for bulletins issued after {since}...")
        blobs = cma.list_bulletin_blobs(since=since)
        if len(blobs) > args.max_bulletins:
            print(
                f"  {len(blobs)} new bulletins, keeping the most recent "
                f"{args.max_bulletins}"
            )
            blobs = blobs[-args.max_bulletins:]

    if not blobs:
        print("No new CMA bulletins to process.")
        return 0

    bulletins = []
    for blob_name in blobs:
        bulletin = cma.load_bulletin(blob_name)
        if bulletin is None:
            continue
        bulletins.append(bulletin)
    print(f"{len(bulletins)} subjective-forecast bulletins to assess")

    if not bulletins:
        return 0

    # The raster is only loaded if a storm actually approaches, so polls
    # during quiet weather stay cheap.
    pop_loader = _lazy_population(adm)

    rows = []
    for bulletin in bulletins:
        row, df_exposure = process_bulletin(
            bulletin,
            regions,
            adm,
            adm1,
            pop_loader,
            pcode_col,
            name_col,
            args,
            df_log,
        )
        rows.append(row)
        if df_exposure is not None and not args.dry_run:
            save_bulletin_outputs(bulletin, df_exposure)

    if args.dry_run:
        print("\nDry run, monitoring log not updated.")
    else:
        append_monitoring_log(rows)
        print(f"\nMonitoring log updated with {len(rows)} bulletins.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
