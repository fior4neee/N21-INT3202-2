"""Timeline chart for failure experiments: node role bands (from RolePoller rows) above the writer's
per-write outcome (ok latency / failure) for each shard. Shared by E6-E8."""
from __future__ import annotations

from . import plots

ROLE_COL = {"PRIMARY": "#2a78d6", "SECONDARY": "#b9d3f2", "UNREACHABLE": "#d6d4ce", "OTHER": "#fab219"}


def draw(path, title: str, poll_rows: list[dict], writer_rows: list[dict], t0: float, nodes: list[str],
         marks: list[tuple[float, str]] | None = None, shards: list[str] | None = None) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    plots.setup()
    shards = shards or sorted({r["target"] for r in writer_rows})
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 6.2), sharex=True, gridspec_kw={"height_ratios": [1.1, 1.6]})
    tmax = max([r["t_epoch"] for r in poll_rows] + [float(r["t_epoch"]) for r in writer_rows]) - t0
    for i, n in enumerate(nodes):
        rs = sorted((r for r in poll_rows if r["node"] == n), key=lambda r: r["t_epoch"])
        for a, b in zip(rs, rs[1:] + [None]):
            x0 = a["t_epoch"] - t0
            x1 = (b["t_epoch"] - t0) if b else tmax
            a1.barh(i, x1 - x0, left=x0, height=0.62, color=ROLE_COL.get(a["role"], "#eeeeee"), lw=0)
    a1.set_yticks(range(len(nodes)))
    a1.set_yticklabels(nodes)
    a1.invert_yaxis()
    a1.grid(axis="y", visible=False)
    a1.legend(handles=[Patch(color=c, label=k) for k, c in ROLE_COL.items()], loc="upper left",
              bbox_to_anchor=(1.0, 1.0), fontsize=8)
    a1.set_title(title.replace("$", r"\$"), loc="left")
    for i, sh in enumerate(shards):
        ok = [(float(r["t_epoch"]) - t0, float(r["latency_ms"])) for r in writer_rows
              if r["target"] == sh and str(r["ok"]) == "1"]
        bad = [(float(r["t_epoch"]) - t0, float(r["latency_ms"])) for r in writer_rows
               if r["target"] == sh and str(r["ok"]) != "1"]
        if ok:
            a2.scatter(*zip(*ok), s=7, color=plots.color(sh, i), label=f"{sh}: write ok", zorder=3)
        if bad:
            a2.scatter(*zip(*bad), s=26, marker="x", color="#d03b3b" if sh == shards[0] else "#9a6a00",
                       label=f"{sh}: write FAILED ({len(bad)})", zorder=4)
    a2.set_yscale("log")
    a2.set_ylabel("write latency (ms, log)")
    a2.set_xlabel("seconds since fault")
    a2.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8)
    for ax in (a1, a2):
        ax.axvline(0, color="#d03b3b", lw=1.2)
        for t, lab in marks or []:
            ax.axvline(t, color=plots.INK2, lw=0.9, ls="--")
    for t, lab in marks or []:
        a1.text(t, -0.75, " " + lab, fontsize=7.6, color=plots.INK2, rotation=0, va="bottom")
    plots.save(fig, path)
