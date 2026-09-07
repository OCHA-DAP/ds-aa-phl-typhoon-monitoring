"""Build and send The Philippines typhoon monitoring emails via Listmonk.

Formatting follows the IBF typhoon pipeline email
(``IBF-Typhoon-model/src/typhoonmodel/utility_fun/emailstatushtml.py``): a
scoped ``.phl-typhoon`` wrapper, a title, a short intro, the map, green
summary tables and a Further Details link. Keeping the two products visually
consistent matters more than any local preference.

Per-province figures stay in the monitoring log on blob rather than the email.
"""

import base64
import os
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

from ocha_relay.listmonk import ListmonkClient

from src.constants import (
    EXPOSURE_SHARE_THRESHOLD,
    EXPOSURE_TRIGGER_SPEED_KT,
    LISTMONK_LIST_ID,
    LISTMONK_LIST_ID_TEST,
    READINESS_THRESHOLD_KPH_1MIN,
)
from src.utils.categories import expand_category

PH_TZ = timezone(timedelta(hours=8))

FRAMEWORK_URL = (
    "https://reliefweb.int/report/philippines/anticipatory-action-framework-"
    "philippines-tropical-cyclones-03-october-2025"
)

IBF_GREEN = "#1BB580"


def listmonk_client() -> ListmonkClient:
    """Build the Listmonk client from DSCI_LISTMONK_API_URL.

    ``ListmonkClient.from_env`` reads DSCI_LISTMONK_BASE_URL, so the client is
    built explicitly here to use the API URL instead. The two variables differ
    only in whether they carry the ``/api`` suffix, so it is normalised rather
    than assumed: the client appends paths like ``/lists`` to whatever it is
    given.
    """
    url = os.getenv("DSCI_LISTMONK_API_URL") or os.getenv(
        "DSCI_LISTMONK_BASE_URL"
    )
    if not url:
        raise ValueError(
            "Set DSCI_LISTMONK_API_URL to reach Listmonk"
        )
    url = url.rstrip("/")
    if not url.endswith("/api"):
        url = f"{url}/api"
    return ListmonkClient(
        base_url=url,
        username=os.environ["DSCI_LISTMONK_API_USERNAME"],
        password=os.environ["DSCI_LISTMONK_API_KEY"],
    )


def _fig_to_b64(fig) -> str:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", dpi=110)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode()


def _embed(fig) -> str:
    return f"data:image/png;base64,{_fig_to_b64(fig)}"


def _status_word(triggered: bool) -> str:
    return "Reached" if triggered else "Not Reached"


def _intro_paragraph(readiness_result: dict) -> str:
    """One paragraph: what CMA expects, and when.

    Trigger states are deliberately not repeated here, they lead the email in
    their own section.
    """
    storm = readiness_result["storm_name"]
    expected = readiness_result.get("expected_landfall") or {}

    if expected.get("makes_landfall"):
        hours = readiness_result.get("hours_to_landfall")
        landfall_time = readiness_result.get("landfall_time")
        when = (
            f" around {landfall_time:%d %B %H:%M UTC}"
            if landfall_time is not None
            else ""
        )
        timing = (
            f" Landfall is <strong>{hours} hours</strong> away "
            f"({hours / 24:.1f} days),{when}."
            if hours is not None
            else ""
        )
        forecast = (
            f"The China Meteorological Administration forecast expects "
            f"<strong>{storm}</strong> to make landfall as a "
            f"<strong>{expected['landfall_category']}</strong> in "
            f"<strong>{', '.join(expected['regions'])}</strong>, at "
            f"<strong>{expected['landfall_wind_kph_1min']:.0f} kph</strong> "
            "1-minute sustained "
            f"({expected['landfall_wind_kph_10min']:.0f} kph 10-minute "
            f"sustained, {expected['landfall_wind_kt']:.0f} kt 2-minute as "
            f"reported by CMA).{timing}"
        )
    else:
        forecast = (
            "The China Meteorological Administration forecast track for "
            f"<strong>{storm}</strong> does not cross a target region. Peak "
            "forecast intensity is "
            f"{readiness_result['max_forecast_wind_kt']:.0f} kt "
            "2-minute sustained ("
            f"{readiness_result['max_forecast_wind_kt'] * 1.852 / 0.93:.0f}"
            " kph 1-minute, "
            f"{readiness_result['max_forecast_wind_kt'] * 1.852 / 0.93 * 0.88:.0f}"
            " kph 10-minute)."
        )

    return forecast


def _trigger_status(readiness_result: dict, exposure_trigger: dict) -> str:
    """Where each trigger stands, as two labelled lines."""
    if readiness_result["triggered"]:
        readiness = f'<span style="color:{IBF_GREEN};font-weight:bold;">Reached</span>'
    elif readiness_result.get("previously_triggered"):
        readiness = (
            f'<span style="color:{IBF_GREEN};font-weight:bold;">Reached'
            "</span> on an earlier forecast for this storm"
        )
    else:
        readiness = '<span style="color:#777;font-weight:bold;">Not reached</span>'

    hours = readiness_result.get("hours_to_landfall")
    if hours is not None:
        readiness += f", with {hours} hours to forecast landfall"

    if exposure_trigger and exposure_trigger.get("triggered"):
        exposure = (
            f'<span style="color:{IBF_GREEN};font-weight:bold;">Reached'
            f"</span> in {', '.join(exposure_trigger['regions'])}"
        )
    else:
        exposure = '<span style="color:#777;font-weight:bold;">Not reached</span>'

    if exposure_trigger and exposure_trigger.get("max_share") is not None:
        share = f"{exposure_trigger['max_share']:.0%}"
        # Do not repeat the region name when it is the one already named.
        if exposure_trigger.get("regions") == [
            exposure_trigger.get("max_share_region")
        ]:
            exposure += f", with {share} of its population exposed"
        else:
            exposure += (
                f", with {share} of "
                f"{exposure_trigger['max_share_region']} exposed"
            )

    return f"""
          <h2>Trigger status</h2>
          <p><strong>Readiness:</strong> {readiness}<br/>
          <strong>Exposure:</strong> {exposure}</p>"""


def _exposure_section(
    exposure_trigger: dict, df_national, fig_region_share
) -> str:
    """Exposure written out, with the per-region chart carrying the detail."""
    speed = EXPOSURE_TRIGGER_SPEED_KT
    detail = (exposure_trigger or {}).get("detail")

    if detail is None or detail.empty:
        return f"""
          <h2>Population exposure</h2>
          <p>No population is inside the {speed} kt forecast wind field.</p>"""

    lines = []
    if exposure_trigger.get("max_share") is not None:
        lines.append(
            f"The most exposed target region is "
            f"<strong>{exposure_trigger['max_share_region']}</strong>, with "
            f"<strong>{exposure_trigger['max_share']:.0%}</strong> of its "
            f"population inside the {speed} kt forecast wind field."
        )

    if df_national is not None and not df_national.empty:
        row = df_national[df_national["speed_kt"] == speed]
        if not row.empty:
            r = row.iloc[0]
            lines.append(
                f"Across The Philippines, "
                f"<strong>{int(r['pop_exposed']):,}</strong> people "
                f"({r['share_exposed']:.1%}) are inside it."
            )

    chart = ""
    if fig_region_share is not None:
        chart = f"""
          <div style="text-align:center;margin:16px 0 20px;">
            <img src="{_embed(fig_region_share)}"
                 alt="Population exposed by target region" width="500"
                 style="display:block;width:100%;max-width:600px;">
          </div>"""

    return f"""
          <h2>Population exposure</h2>
          <p>{' '.join(lines)}</p>
          {chart}"""


def _comparison_section(comparison) -> str:
    """Both exposure sources side by side, so they can be judged on live storms."""
    if not comparison:
        return ""
    rows = ""
    for label, figures in comparison:
        if figures is None:
            rows += (
                f"<li><strong>{label}:</strong> not available for this "
                "bulletin</li>"
            )
            continue
        rows += (
            f"<li><strong>{label}:</strong> {figures['people']} people "
            f"({figures['national_share']} of The Philippines), most exposed "
            f"region {figures['region_share']}, trigger "
            f"{figures['trigger']}</li>"
        )
    return f"""
          <h2>Exposure by source</h2>
          <p>The same storm measured two ways, while both are being
          compared. The trigger decision follows the CMA radii, which is the
          footprint the 50% threshold was calibrated on.</p>
          <ul>{rows}</ul>"""


def _build_body(
    readiness_result: dict,
    fig_map,
    fig_region_share=None,
    exposure_trigger: dict = None,
    df_national=None,
    comparison=None,
) -> str:
    """Compose the alert HTML in the IBF typhoon pipeline format."""
    storm = readiness_result["storm_name"]
    issued = readiness_result["issue_time"].strftime("%d %B %Y %H:%M UTC")
    category = expand_category(readiness_result.get("category"))

    return f"""<style>
  .phl-typhoon {{ font-family: Helvetica, Arial, sans-serif; font-size:14px;
                 color:#222; }}
  .phl-typhoon h1 {{ font-size:20px; }}
  .phl-typhoon h2 {{ font-size:16px; color:{IBF_GREEN};
                    margin-top:26px; margin-bottom:6px; }}
  .phl-typhoon p {{ line-height:1.5; }}
</style>
<div class="phl-typhoon">
          <h1>The Philippines Typhoon Monitoring for {storm}</h1>

          <p>{_intro_paragraph(readiness_result)}</p>

          {_trigger_status(readiness_result, exposure_trigger)}

          <p style="font-size:0.9em;color:#555;">
            Currently a {category}. CMA forecast issued {issued}. The map
            below shows the forecast track and the
            {EXPOSURE_TRIGGER_SPEED_KT} kt wind field.
          </p>

          <div style="text-align:center;margin-bottom:30px;">
            <img src="{_embed(fig_map)}" alt="CMA forecast track" width="500"
                 style="display:block;width:100%;max-width:600px;">
          </div>

          {_exposure_section(exposure_trigger, df_national,
                             fig_region_share)}

          {_comparison_section(comparison)}

          <h2>Further details</h2>
          <p>Readiness requires a CMA forecast showing a qualifying landfall
          in a target region at or above
          {READINESS_THRESHOLD_KPH_1MIN} kph (1-minute sustained), and is
          monitored from the release of that forecast until landfall.
          Exposure requires landfall as a Super Typhoon and at least
          {EXPOSURE_SHARE_THRESHOLD:.0%} of a target region inside the
          {EXPOSURE_TRIGGER_SPEED_KT} kt wind field. Per-province figures are
          saved to blob storage.</p>
          <p>For more details on the Anticipatory Action framework for
          typhoons in the Philippines, please refer to the
          <a href="{FRAMEWORK_URL}" target="_blank"
             style="color:{IBF_GREEN};text-decoration:underline;">Framework
             Document</a>.</p>
</div>"""


def _sent_count(client, campaign_id):
    """Read back how many recipients Listmonk actually sent to."""
    campaign = client.get_campaign(campaign_id)
    data = campaign if isinstance(campaign, dict) else campaign.__dict__
    return data.get("sent"), data.get("to_send"), data.get("status")


def _send_and_verify(client, campaign_id, attempts: int = 3) -> bool:
    """Send a campaign and confirm Listmonk really delivered it.

    This instance has been seen to mark a campaign ``finished`` while having
    sent to nobody, so the send is read back rather than assumed. Returns
    True once the sent count is non-zero.
    """
    for attempt in range(attempts):
        client.send_campaign(campaign_id, skip_confirmation=True)
        for _ in range(6):
            time.sleep(2)
            sent, to_send, status = _sent_count(client, campaign_id)
            if sent:
                return True
            if status == "finished":
                break
        print(
            f"  Listmonk campaign {campaign_id} finished with "
            f"{sent}/{to_send} sent, retrying "
            f"({attempt + 1}/{attempts})"
        )
    return False


def send_monitoring_email(
    readiness_result: dict,
    fig_map,
    fig_region_share=None,
    exposure_trigger: dict = None,
    df_national=None,
    comparison=None,
    test: bool = False,
) -> int:
    """Send the monitoring email. Returns the Listmonk campaign ID.

    Listmonk on this instance has been seen to accept a campaign and then
    drop the send silently, so the campaign ID is returned and written to the
    monitoring log for checking against the Listmonk UI.
    """
    client = listmonk_client()
    html_body = _build_body(
        readiness_result,
        fig_map,
        fig_region_share=fig_region_share,
        exposure_trigger=exposure_trigger,
        df_national=df_national,
        comparison=comparison,
    )

    prefix = "[TEST] " if test else ""
    subject = (
        f"{prefix}The Philippines Typhoon Monitoring for "
        f"{readiness_result['storm_name']}"
    )
    stamp = datetime.now(PH_TZ).strftime("%Y%m%dT%H%M")
    campaign_name = (
        f"{'[TEST]-' if test else ''}phl-typhoon-cma-"
        f"{readiness_result['storm_name'].lower()}-{stamp}"
    )
    list_id = LISTMONK_LIST_ID_TEST if test else LISTMONK_LIST_ID
    if list_id is None:
        raise ValueError("No Listmonk list configured for this mode")

    campaign_id = client.create_campaign(
        name=campaign_name,
        subject=subject,
        body=html_body,
        list_ids=[list_id],
    )
    if not _send_and_verify(client, campaign_id):
        print(
            f"  WARNING: Listmonk campaign {campaign_id} reported no "
            "recipients sent. Check the Listmonk UI."
        )
    return campaign_id
