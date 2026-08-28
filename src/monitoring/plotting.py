"""Figures for the CMA typhoon monitoring alerts."""

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from src.constants import CHD_BLUE, CHD_GREEN, CHD_RED
from src.datasources.cma import bulletin_to_frame
from src.utils.categories import expand_category

# Wind swath shading, weakest to strongest
_SPEED_COLOURS = {
    30: "#fee6ce",
    50: "#fdae6b",
    64: "#e6550d",
}


def plot_forecast_map(
    bulletin: dict,
    regions,
    buffers=None,
    adm1=None,
    readiness_result: dict = None,
    figsize=(8, 9),
):
    """Map the CMA forecast track, wind swaths and target regions."""
    track = bulletin_to_frame(bulletin).sort_values("fh")

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_facecolor("#cde0f0")

    if adm1 is not None:
        adm1.plot(ax=ax, color="#f0ece3", edgecolor="#aaa", linewidth=0.4)
    regions.plot(
        ax=ax, color="#f4a261", edgecolor="#333", linewidth=0.8, alpha=0.9
    )

    if buffers is not None and not buffers.empty:
        for _, row in buffers.sort_values(
            "speed_kt", ascending=True
        ).iterrows():
            colour = _SPEED_COLOURS.get(int(row["speed_kt"]), CHD_BLUE)
            buffers.loc[[row.name]].plot(
                ax=ax, color=colour, alpha=0.45, edgecolor=colour,
                linewidth=0.6, zorder=2,
            )

    ax.plot(
        track["lon"], track["lat"], color=CHD_RED, linewidth=1.8,
        zorder=4, marker="o", markersize=4,
    )
    for _, row in track.iterrows():
        ax.annotate(
            f"{int(row['fh'])}h\n{row['wind_kt']:.0f}kt",
            xy=(row["lon"], row["lat"]),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=6.5,
            color="#333",
            zorder=5,
        )

    lon_pad, lat_pad = 6, 5
    ax.set_xlim(
        min(track["lon"].min() - lon_pad, 116),
        max(track["lon"].max() + lon_pad, 128),
    )
    ax.set_ylim(
        min(track["lat"].min() - lat_pad, 5),
        max(track["lat"].max() + lat_pad, 22),
    )

    handles = [
        Line2D([], [], color=CHD_RED, marker="o", markersize=4,
               label="CMA forecast track"),
        mpatches.Patch(color="#f4a261", label="Framework target regions"),
    ]
    if buffers is not None and not buffers.empty:
        for speed in sorted(buffers["speed_kt"]):
            handles.append(
                mpatches.Patch(
                    color=_SPEED_COLOURS.get(int(speed), CHD_BLUE),
                    alpha=0.6,
                    label=f"{int(speed)} kt wind swath",
                )
            )
    ax.legend(handles=handles, loc="lower left", fontsize=8)

    issued = bulletin["issue_time"].strftime("%Y-%m-%d %H:%M UTC")
    title = (
        f"{expand_category(bulletin['category'])} {bulletin['storm_name']} "
        f"({bulletin['storm_id']})"
    )
    subtitle = f"CMA forecast issued {issued}"
    expected = (readiness_result or {}).get("expected_landfall")
    if expected and expected.get("makes_landfall"):
        subtitle += (
            "\nExpected landfall as a "
            f"{expected['landfall_category']}: "
            f"{expected['landfall_wind_kph_1min']:.0f} kph "
            "(1-min sustained)"
        )
    elif readiness_result:
        subtitle += "\nNo landfall in a target region on this forecast"
    ax.set_title(f"{title}\n{subtitle}", fontweight="bold", fontsize=10)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    fig.tight_layout()
    return fig


def plot_exposure(
    df_summary,
    label_col: str,
    top_n: int = 15,
    figsize=(8, 5.5),
):
    """Horizontal bar chart of population exposed by admin unit and speed.

    ``df_summary`` is the output of
    :func:`src.monitoring.exposure.summarise_exposure` grouped by
    ``label_col``.
    """
    if df_summary.empty:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(
            0.5, 0.5, "No population exposed to the forecast wind field",
            ha="center", va="center", fontsize=11, color="#666",
        )
        ax.axis("off")
        return fig

    speeds = sorted(df_summary["speed_kt"].unique())
    lowest = min(speeds)
    order = (
        df_summary[df_summary["speed_kt"] == lowest]
        .sort_values("pop_exposed", ascending=False)[label_col]
        .head(top_n)
        .tolist()
    )

    fig, ax = plt.subplots(figsize=figsize)
    y = np.arange(len(order))
    height = 0.8 / max(len(speeds), 1)

    for i, speed in enumerate(sorted(speeds, reverse=True)):
        sub = df_summary[df_summary["speed_kt"] == speed].set_index(
            label_col
        )
        values = [sub["pop_exposed"].get(name, 0) for name in order]
        ax.barh(
            y + i * height,
            values,
            height=height,
            color=_SPEED_COLOURS.get(int(speed), CHD_GREEN),
            edgecolor="white",
            linewidth=0.5,
            label=f"{int(speed)} kt",
        )

    ax.set_yticks(y + 0.4 - height / 2)
    ax.set_yticklabels(order, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("People exposed")
    ax.xaxis.set_major_formatter(
        plt.FuncFormatter(
            lambda v, _: f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v:,.0f}"
        )
    )
    ax.set_title(
        "Population inside the CMA forecast wind field",
        fontweight="bold",
        fontsize=10,
    )
    ax.legend(title="Wind speed", fontsize=8, title_fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", linestyle=":", alpha=0.5)
    fig.tight_layout()
    return fig


def plot_region_share(
    df_region_exposure,
    speed_kt: int = None,
    share_threshold: float = None,
    figsize=(8, 4.5),
):
    """Share of each target region's population inside the wind field.

    This is the form the observational exposure trigger is stated in: at
    least half of a region's population inside the wind field at
    ``speed_kt``. The absolute headcount is annotated on each bar so both
    readings are available from one figure.
    """
    from src.constants import (
        EXPOSURE_SHARE_THRESHOLD,
        EXPOSURE_TRIGGER_SPEED_KT,
    )

    speed_kt = speed_kt or EXPOSURE_TRIGGER_SPEED_KT
    share_threshold = (
        EXPOSURE_SHARE_THRESHOLD
        if share_threshold is None
        else share_threshold
    )

    fig, ax = plt.subplots(figsize=figsize)
    if df_region_exposure is None or df_region_exposure.empty:
        ax.text(
            0.5, 0.5, "No population exposed in the target regions",
            ha="center", va="center", fontsize=11, color="#666",
        )
        ax.axis("off")
        return fig

    sub = df_region_exposure[
        df_region_exposure["speed_kt"] == speed_kt
    ].sort_values("share_exposed", ascending=True)
    if sub.empty:
        ax.text(
            0.5, 0.5, f"No {int(speed_kt)} kt wind field over target regions",
            ha="center", va="center", fontsize=11, color="#666",
        )
        ax.axis("off")
        return fig

    shares = sub["share_exposed"].fillna(0) * 100
    colours = [
        CHD_RED if share >= share_threshold * 100 else CHD_BLUE
        for share in shares
    ]
    bars = ax.barh(sub["region_name"], shares, color=colours, height=0.6)

    for bar, (_, row) in zip(bars, sub.iterrows()):
        ax.annotate(
            f"{int(row['pop_exposed']):,} of {int(row['region_pop']):,}",
            xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
            xytext=(5, 0),
            textcoords="offset points",
            va="center",
            fontsize=7.5,
            color="#444",
        )

    ax.axvline(
        share_threshold * 100,
        color=CHD_GREEN,
        linestyle="--",
        linewidth=1.4,
        label=f"Trigger: {share_threshold:.0%} of region",
    )
    ax.set_xlim(0, max(105, shares.max() * 1.35))
    ax.set_xlabel(f"Share of region population inside the {int(speed_kt)} kt wind field")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_title(
        "Population exposed by target region, CMA forecast",
        fontweight="bold",
        fontsize=10,
    )
    ax.legend(fontsize=8, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", linestyle=":", alpha=0.5)
    fig.tight_layout()
    return fig
