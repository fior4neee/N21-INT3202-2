"""Shared matplotlib styling for report charts (static PNG, light background, print-friendly).

Colours follow a fixed entity mapping, never rank order:
shard1RS blue, shard2RS orange, shard3RS aqua (first three slots of a CVD-validated categorical
palette). Thin 2 px lines, recessive grid, legend for >= 2 series, one y-axis per chart.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SHARD_COLORS = {"shard1RS": "#2a78d6", "shard2RS": "#eb6834", "shard3RS": "#1baf7a"}
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#ffffff"


def setup() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.labelsize": 10,
        "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "lines.linewidth": 2.0, "legend.frameon": False, "legend.fontsize": 9,
        "font.family": "DejaVu Sans", "figure.dpi": 110,
    })


def color(name: str, i: int = 0) -> str:
    return SHARD_COLORS.get(name, SERIES[i % len(SERIES)])


def save(fig, path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
