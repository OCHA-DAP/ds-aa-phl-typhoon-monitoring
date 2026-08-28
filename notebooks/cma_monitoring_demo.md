---
jupyter:
  jupytext:
    formats: ipynb,md
    text_representation:
      extension: .md
      format_name: markdown
      format_version: '1.3'
  kernelspec:
    display_name: ds-aa-phl-typhoon-monitoring
    language: python
    name: ds-aa-phl-typhoon-monitoring
---

# CMA monitoring demo

Walks through one live CMA bulletin: parse it, check the readiness trigger,
build the wind swaths and compute exposure as a headcount, as a share of each
target region, and as a share of the country.

```python
%load_ext autoreload
%autoreload 2
```

```python
import sys

sys.path.insert(0, "..")

import matplotlib.pyplot as plt
import pandas as pd

from src.constants import (
    EXPOSURE_SHARE_THRESHOLD,
    EXPOSURE_TRIGGER_SPEED_KT,
    READINESS_THRESHOLD_KPH_1MIN,
    READINESS_THRESHOLD_KT,
)
from src.datasources import cma, codab
from src.monitoring import exposure as exp
from src.monitoring import plotting, readiness
```

```python
print(f"Readiness threshold: {READINESS_THRESHOLD_KPH_1MIN} kph (1-min)")
print(f"  applied to CMA as: {READINESS_THRESHOLD_KT:.1f} kt (2-min)")
print(
    f"Exposure trigger: {EXPOSURE_SHARE_THRESHOLD:.0%} of a region "
    f"at {EXPOSURE_TRIGGER_SPEED_KT} kt"
)
```

## Target regions

```python
regions = codab.load_target_regions()
adm1 = codab.load_adm(1)
adm2 = codab.load_exposure_adm(2)
pcode_col, name_col = codab.adm_columns(2)
regions[["region_pcode", "region_name"]]
```

```python
fig, ax = plt.subplots(figsize=(7, 9))
ax.set_facecolor("#cde0f0")
adm1.plot(ax=ax, color="#f0ece3", edgecolor="#aaa", linewidth=0.5)
regions.plot(ax=ax, color="#f4a261", edgecolor="#333", linewidth=0.8)
for _, row in regions.iterrows():
    c = row.geometry.centroid
    ax.annotate(
        row["region_name"],
        xy=(c.x, c.y),
        ha="center",
        va="center",
        fontsize=7,
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#f4a261"),
    )
ax.set_xlim(116, 128)
ax.set_ylim(5, 22)
ax.set_title("Framework target regions", fontweight="bold")
plt.show()
```

## The live CMA feed

```python
blobs = cma.list_bulletin_blobs()
print(f"{len(blobs)} bulletins on the feed")
print("newest:", blobs[-1].split("/")[-1])
```

```python
# Raw text of the newest bulletin
print(cma.load_bulletin_text(blobs[-1]))
```

```python
# Bulletins carrying a forecast track, most recent per storm
bulletins = cma.load_latest_bulletins(
    since=pd.Timestamp.utcnow() - pd.Timedelta(days=7)
)
latest = cma.latest_bulletin_per_storm(bulletins)
[(b["category"], b["storm_name"], b["issue_time"]) for b in latest]
```

```python
bulletin = latest[-1]
cma.bulletin_to_frame(bulletin)
```

## Readiness trigger

```python
result = readiness.check_readiness(bulletin, regions)
{k: v for k, v in result.items() if k != "landfalls"}
```

```python
result["landfalls"]
```

## Wind swaths and exposure

```python
buffers = exp.build_wind_buffers(bulletin)
buffers
```

```python
da_pop = exp.load_population(adm2)
print(f"National population on this raster: {int(da_pop.sum()):,}")
```

```python
df_exposure = exp.calculate_exposure(
    buffers, da_pop, adm2, pcode_col, name_col
)
df_summary = exp.summarise_exposure(df_exposure, group_col=name_col)
df_summary.head(15)
```

### Share of each target region

```python
df_region = exp.region_exposure(buffers, da_pop, regions)
df_region
```

```python
exposure_trigger = exp.check_exposure_trigger(df_region)
exposure_trigger["triggered"], exposure_trigger["max_share"]
```

### Share of the country

```python
exp.national_exposure(buffers, da_pop, adm2, df_exposure=df_exposure)
```

## Figures as they appear in the alert

```python
plotting.plot_forecast_map(
    bulletin, regions, buffers=buffers, adm1=adm1, readiness_result=result
)
plt.show()
```

```python
plotting.plot_region_share(df_region)
plt.show()
```

```python
plotting.plot_exposure(df_summary, label_col=name_col)
plt.show()
```
