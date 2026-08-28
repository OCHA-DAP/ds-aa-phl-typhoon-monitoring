# The Philippines typhoon anticipatory action: readiness and exposure monitoring

[![Generic badge](https://img.shields.io/badge/STATUS-DEVELOPMENT-%23007CE0)](https://shields.io/)

Operational monitoring for The Philippines typhoon anticipatory action
framework, driven by **China Meteorological Administration (CMA) forecast
bulletins**. Every number this repo produces comes from a CMA bulletin, and
every alert says so.

The framework analysis behind the thresholds lives in
[`pa-aa-phl-storms`](https://github.com/OCHA-DAP/pa-aa-phl-storms),
notebooks `11.1` (CMA readiness trigger) and `14` (trigger summary).

## What is monitored

### Readiness trigger

> Readiness activates when a CMA forecast of at least **177 kph (1-minute
> sustained)** is issued **4-7 days** before a qualifying landfall in a target
> region. Activation is counted once per season.

CMA reports 2-minute sustained winds, so the threshold is converted before
comparison: 177 kph (1-min) x 0.93 = 164.6 kph (2-min) = **88.9 kt**.

Intensities are always reported in all three averaging periods, since
different agencies quote different ones: 1-minute (framework and JTWC
convention), 10-minute (WMO and PAGASA convention, 0.88 of the 1-minute
value) and CMA's native 2-minute.

The historical analysis looked backwards from an observed landfall. In
monitoring there is no observed landfall yet, so the same statement is applied
forwards: a bulletin reaches the trigger when its own forecast track crosses a
target region at or above threshold intensity, at a lead time inside the
window.

**Readiness is monitored all the way to landfall**, not only while the window
is open. Each bulletin reports a phase alongside the trigger decision, so a
storm is never dropped from reporting just because the window has closed:

| Phase | Status | Meaning |
|---|---|---|
| `before_window` | `awaiting_window` | Qualifying landfall forecast, further out than the window |
| `in_window` | `triggered` | Lead time inside the window: the trigger is reached |
| `after_window` | `monitoring_to_landfall` | Window closed, storm still inbound and reported every bulletin |
| `no_landfall_forecast` | `no_landfall` / `below_threshold` / `no_forecast` | No qualifying landfall on this forecast |

Once readiness has been reached for a storm, later bulletins keep saying so.
Every alert also states the hours remaining to forecast landfall.

**Exposure is computed throughout**, on every bulletin for a storm in play,
independently of the readiness phase.

### Exposure trigger

> The exposure trigger is reached when CMA expects the storm to make landfall
> in a target region **as a Super Typhoon**, **and** at least **50% of that
> region's population** lies inside the **64 kt** CMA forecast wind field.

Both conditions must hold. A storm can meet the population share and still not
reach the trigger if it is not forecast to arrive as a super typhoon.

CMA classifies on 2-minute sustained wind (GB/T 19201-2006):

| Category | 2-min wind |
|---|---|
| Super Typhoon | >= 51.0 m/s (99.1 kt) |
| Severe Typhoon | 41.5 - 50.9 m/s |
| Typhoon | 32.7 - 41.4 m/s |
| Severe Tropical Storm | 24.5 - 32.6 m/s |
| Tropical Storm | 17.2 - 24.4 m/s |
| Tropical Depression | 10.8 - 17.1 m/s |

Note that the readiness threshold of 88.9 kt sits in the **Severe Typhoon**
band, one step below Super Typhoon. The two triggers therefore have different
intensity bars, by design.

**Every alert always states what CMA expects the storm to make landfall as**,
whether or not either trigger is reached.

Exposure is assessed at **64 kt only**, the wind speed the trigger is defined
on. Lower wind bands are not computed or reported. Within that, it is read
three ways, because the trigger is stated as a share but operations need
headcounts:

| Reading | Question it answers |
|---|---|
| People exposed, by admin 2 | Who is in the path, and where |
| Share of each target region | Is the exposure trigger reached |
| Share of the country | How national in scale this storm is |

The email carries the region share and the national figure. Per-province
detail goes to the monitoring log on blob.

Region and national populations are taken from the **same WorldPop raster**
that supplies the exposed counts, so numerator and denominator are consistent.
Taking the denominator from a different source inflates it and understates the
share.

## Target regions

| Code | Region |
|------|--------|
| PH02 | Region II (Cagayan Valley) |
| PH03 | Region III (Central Luzon) |
| PH05 | Region V (Bicol Region) |
| PH08 | Region VIII (Eastern Visayas) |
| PH16 | Region XIII (Caraga) |

PH02, PH03, PH05 and PH08 are the four regions used in the CMA readiness
return-period analysis. Region 13 (PH16) is monitored alongside them.

## Data

All CMA data is read from blob storage, container `projects`, stage `dev`.

| Product | Path | Use |
|---|---|---|
| Live bulletin feed | `ds-cma-datasharing/cma_ftp/data_out/typhoon/*.TXT` | Operational monitoring |
| 2004-2025 WNP archive | `ds-cma-datasharing/cma_ftp/data_out/2004-2025_WNP_TC/` | Trigger return periods |
| Parsed archive | `ds-cma-datasharing/processed/cma_wnp_tc_tracks.parquet` | Cached form of the above |
| WorldPop count raster | `worldpop/pop_count/...` (container `raster`) | Exposure |

The live feed drops one file per bulletin, updated several times an hour. Two
kinds appear:

* `WTPQ` **subjective forecast** bulletins, which carry the forecast track and
  wind radii. These are what the monitoring uses.
* `TCPQ` tabular products, which carry no forecast positions and are skipped.

A subjective forecast bulletin looks like this:

```text
ZCZC
WTPQ20 BABJ 131200
SUBJECTIVE FORECAST
SuperTY SINLAKU 2604 (2604) INITIAL TIME 131200 UTC
00HR 13.1N 147.4E 905HPA 68M/S
30KTS WINDS 500KM NORTHEAST
            480KM SOUTHEAST
            450KM SOUTHWEST
            450KM NORTHWEST
50KTS WINDS 160KM NORTHEAST
...
MOVE NW 14KM/H
P+12HR 14.3N 146.5E 910HPA 65M/S
...
P+120HR 25.2N 150.4E 980HPA 30M/S=
NNNN
```

## Known limitations

These matter for reading the outputs, so they are stated up front rather than
buried.

1. **CMA forecasts extend to 120 hours.** The readiness window is 4-7 days,
   so only its 4-5 day portion is reachable. A bulletin can never show a 6-7
   day lead. This matches the historical finding that only one season in
   2004-2025 had a qualifying signal at 7+ days.
2. **Rapid intensifiers are systematically missed.** In the historical CMA
   analysis, Region VIII had zero activations: Haiyan (2013) and Rai (2021)
   made catastrophic landfalls there, but the CMA forecast 4-7 days out did
   not show them at threshold. This is inherent to a forecast-based readiness
   trigger, not a defect in this code.
3. **Wind radii are analysis-only.** CMA publishes quadrant radii for the 00HR
   position, never for forecast steps. The radii are carried along the
   forecast track and each speed's swath is truncated where the storm is no
   longer forecast to reach that speed. Real wind fields expand as a storm
   weakens, so read the swaths as an indication of who is in the path, not as
   a calibrated wind footprint.
4. **Analysis-only bulletins exist.** Some bulletins carry only the 00HR
   position with no forecast. Readiness cannot be assessed from these and they
   are logged with status `no_forecast`.

## When it runs

**When CMA forecasts arrive.** Measured over 1,648 bulletins on the feed
(January to August 2026), CMA issues on a **3-hourly cycle at 02, 05, 08, 11,
14, 17, 20 and 23 UTC**. Those eight hours carry about 60% of all bulletins.
The remainder are corrections and re-issues that land at any hour. For a storm
under active watch the median gap between consecutive bulletins is **under 40
minutes**, and a busy day produces 14 bulletins at the median and up to 50.

The GitHub Actions workflow therefore polls **every 15 minutes** rather than
firing on the cycle hours: that catches the off-cycle re-issues too, and does
not depend on GitHub cron being punctual, which it is not. Polls during quiet
weather are cheap: the monitoring log records which bulletins have already
been processed, and the WorldPop raster is only loaded once a storm actually
approaches.

The workflow also accepts a `repository_dispatch` event of type
`cma-bulletin`, so the CMA FTP sync can notify the repo the moment a bulletin
lands rather than waiting for the next poll. A `concurrency` group prevents
two runs from processing the same bulletin and double-sending.

## Running it

```bash
pip install -e .
pip install -r requirements.txt
```

Process every bulletin issued since the last logged run:

```bash
python pipelines/monitor_cma.py
```

Re-run one bulletin, computing everything but sending nothing:

```bash
python pipelines/monitor_cma.py --blob <blob name> --dry-run --force-email
```

Send to the test distribution list with a `[test]` subject prefix:

```bash
python pipelines/monitor_cma.py --test
```

| Flag | Effect |
|---|---|
| `--blob` | Process one specific bulletin instead of polling |
| `--since` | Poll from a given UTC time rather than the log |
| `--max-bulletins` | Cap bulletins processed in one run (default 20) |
| `--test` | Send to the test list (103) instead of the live list (121) |
| `--force-email` | Skip the relevance and cooldown checks |
| `--dry-run` | Write nothing to blob, send no email |

### Alerting policy

**Every new CMA forecast for a storm in play produces an email.** There is no
cooldown and no sampling: the monitoring log deduplicates on bulletin blob
name, so one new forecast means exactly one email. Each email leads with the
time remaining to CMA's forecast landfall.

**Every new CMA forecast for a storm close to The Philippines produces an
email, whether or not a trigger is reached.** The trigger state is reported
in the email, it does not gate it.

Close means the forecast track enters the **Philippine Area of
Responsibility** (PAGASA's box, 5-25N and 115-135E) or comes within 500 km of
a target region. Storms elsewhere in the Western North Pacific are still
assessed and written to the monitoring log, but produce no email.

There is no cooldown: the log deduplicates on bulletin blob name, so one new
forecast means exactly one email. For storms whose wind field cannot reach
land the population raster is never loaded, since the exposed population is
exactly zero.

### Email format

The alert follows the IBF typhoon pipeline email format
(`IBF-Typhoon-model/src/typhoonmodel/utility_fun/emailstatushtml.py`): a
scoped `.phl-typhoon` wrapper, a title, a short intro paragraph, the forecast
map, green `#1BB580` summary tables and a Further Details link to the
framework document. Test sends are prefixed `[TEST]`.

### Outputs

| Output | Location |
|---|---|
| Monitoring log | `ds-aa-phl-typhoon-monitoring/monitoring/monitoring_log.parquet` |
| Per-bulletin exposure | `ds-aa-phl-typhoon-monitoring/monitoring/bulletins/` |
| Email | Listmonk list 121 (live), 103 (test) |

The monitoring log is also the deduplication state: it records which bulletins
have been processed and which produced an email, keyed on the bulletin blob
name.

## Environment

```text
DSCI_AZ_BLOB_DEV_SAS_WRITE
DSCI_AZ_BLOB_PROD_SAS
DSCI_LISTMONK_API_URL
DSCI_LISTMONK_API_USERNAME
DSCI_LISTMONK_API_KEY
```

The Listmonk client is built from `DSCI_LISTMONK_API_URL`, not the
`DSCI_LISTMONK_BASE_URL` that `ListmonkClient.from_env` would read. The two
differ only in the `/api` suffix, which is normalised in
`src/monitoring/emails.py:listmonk_client`.

Listmonk on this instance has been seen to accept a campaign and then drop the
send silently, reporting `finished` with zero recipients. Sends are therefore
read back and retried up to three times, and the campaign ID is written to the
monitoring log so a send can be checked against the Listmonk UI.

## Development

Code is formatted with black and flake8, line length 79. Install the hooks
before developing:

```bash
pre-commit install
```

Notebooks are paired to Markdown with jupytext. Commit the `.md`, and keep
data on blob rather than in the repo.
