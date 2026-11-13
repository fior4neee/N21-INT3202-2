"""Cluster topology diagrams drawn from OBSERVED state, one per time step (E6-E9).

    state = snapshot()                         # docker + hello of every node, right now
    draw(state, "img/e06_step1.png", title="t=+0.0s  docker kill s1a",
         shard_notes={"shard1RS": {"writes": "fail", "MB": 106}}, events=["s1a killed", ...])
    storyboard([...pngs], "img/e06_storyboard.png")   # all steps in one image

Visual encoding (text label always present, colour never alone):
  PRIMARY    solid blue box, white text
  SECONDARY  light-blue box
  DOWN       grey hatched box  "DOWN x"
  PAUSED     amber-outlined box "PAUSED ||"
  ISOLATED   red dashed outline: container running but disconnected from the Docker network
  other      (ROLLBACK, RECOVERING, STARTUP2, no primary...) amber text with the state name
Shard header: "writes OK ✓" (green) / "writes FAIL ✗" (red) / "read-only" (amber) when known.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from .common import REPLSETS, container_state, hello, mongosh, node_role  # noqa: E402

BLUE, BLUE_LT, INK, INK2, MUTED, GRID = "#2a78d6", "#dbe8f8", "#0b0b0b", "#52514e", "#898781", "#e4e3df"
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
GOOD_TXT = "#006300"


# ---------------------------------------------------------------- observe
def _hello_inside(name: str) -> dict | None:
    """hello via mongosh inside the container: works even when it is cut off from the network."""
    out = mongosh(name, "JSON.stringify((({isWritablePrimary,secondary,setName,primary})=>"
                        "({isWritablePrimary,secondary,setName,primary}))(db.hello()))", timeout=20)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:
        return None


def snapshot(include_shard3: bool | None = None, inside_fallback: bool = True) -> dict:
    """{'nodes': {name: {...}}, 'mongos': {...}} with docker state, network state and replica-set role."""
    from .chaos import is_connected
    if include_shard3 is None:
        include_shard3 = container_state("s3a") != "missing"
    nodes = {}
    for rs, members in REPLSETS.items():
        if rs == "shard3RS" and not include_shard3:
            continue
        for n in members:
            dk = container_state(n)
            st = {"rs": rs, "docker": dk, "connected": True, "role": "?"}
            if dk == "missing" or dk.startswith(("exited", "created", "dead")):
                st["role"] = "DOWN"
            elif dk.startswith("paused"):
                st["role"] = "PAUSED"
            else:
                st["connected"] = is_connected(n)
                h = hello(n, 1500) if st["connected"] else None
                if h is None and inside_fallback:
                    h = _hello_inside(n)
                st["role"] = node_role(h) if h else "UNREACHABLE"
                if h and not h.get("isWritablePrimary") and not h.get("secondary"):
                    st["role"] = "NO-ROLE"  # e.g. STARTUP2 / RECOVERING / ROLLBACK
            nodes[n] = st
    m = container_state("mongos")
    return {"nodes": nodes, "mongos": {"docker": m}}


# ---------------------------------------------------------------- draw
def _box(ax, x, y, w, h, fc, ec, lw=1.2, ls="-", hatch=None, r=0.8, z=2):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw, ls=ls,
                       hatch=hatch, zorder=z)
    ax.add_patch(p)
    return p


def _arrow(ax, a, b, color, ls="-", lw=1.6, rad=0.0, label=None, label_color=None, z=1, fs=8.5):
    ar = FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=12, color=color, lw=lw, ls=ls,
                         connectionstyle=f"arc3,rad={rad}", zorder=z, shrinkA=2, shrinkB=2)
    ax.add_patch(ar)
    if label:
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        ax.text(mx + 0.8, my, label, fontsize=fs, color=label_color or color, va="center", ha="left", zorder=5,
                bbox=dict(fc="white", ec="none", pad=1.0))


def _node(ax, x, y, w, h, name, st, highlight=None):
    role, conn = st["role"], st.get("connected", True)
    fc, ec, tc, ls, hatch, label = "white", MUTED, INK, "-", None, role
    if role == "PRIMARY":
        fc, ec, tc = BLUE, BLUE, "white"
    elif role == "SECONDARY":
        fc, ec = BLUE_LT, BLUE
    elif role == "DOWN":
        fc, ec, tc, hatch, label = "#f1f0ec", MUTED, INK2, "///", "DOWN ✗"
    elif role == "PAUSED":
        fc, ec, label = "#fff6e0", WARN, "PAUSED ||"
    elif role in ("UNREACHABLE", "?"):
        fc, ec, tc, label = "white", MUTED, INK2, "UNREACHABLE"
    else:
        fc, ec, label = "#fff6e0", WARN, role
    lw = 1.4
    if not conn:
        ec, ls, lw = CRIT, "--", 2.2
        label = f"{label}\nISOLATED"
    _box(ax, x, y, w, h, fc, ec, lw=lw, ls=ls, hatch=hatch)
    ax.text(x + w / 2, y + h * 0.70, name, ha="center", va="center", fontsize=10, fontweight="bold", color=tc, zorder=4)
    ax.text(x + w / 2, y + h * 0.32, label, ha="center", va="center", fontsize=7.6, color=tc, zorder=4,
            linespacing=1.1)
    if highlight:
        ax.text(x + w / 2, y - 1.6, highlight, ha="center", va="top", fontsize=7.6, color=CRIT, zorder=4,
                fontweight="bold")


def _status_text(s: str | None):
    return {"ok": ("writes OK ✓", GOOD_TXT), "fail": ("writes FAIL ✗", CRIT), "readonly": ("read-only ⚠", "#9a6a00"),
            "partial": ("writes partly failing ⚠", "#9a6a00")}.get(s or "", (None, None))


def draw(state: dict, path, title: str, subtitle: str = "", shard_notes: dict | None = None,
         events: list[str] | None = None, migrations: list[dict] | None = None,
         highlight: dict | None = None, cfg_note: dict | None = None, mongos_note: str | None = None,
         client_label: str = "client  (writer.py: 1 insert / 100 ms)") -> Path:
    """shard_notes: {rs: {"writes": ok|fail|readonly|partial, "MB": float, "note": str}}
    migrations: [{"from": "shard2RS", "to": "shard3RS", "label": "8 MB chunk"}]
    highlight: {node: "short red caption under the node"}"""
    shard_notes, highlight, cfg_note = shard_notes or {}, highlight or {}, cfg_note or {}
    nodes = state["nodes"]
    shards = [rs for rs in REPLSETS if rs != "cfgRS" and any(v["rs"] == rs for v in nodes.values())]
    n_mig = len(migrations or [])
    y_min = -1.0 - (3.6 + 1.6 * n_mig if n_mig else 0)
    fig = plt.figure(figsize=(13, 7.6 * (62 - y_min) / 63))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(y_min, 62)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    # title + subtitle
    ax.text(2, 60, title, fontsize=15, fontweight="bold", color=INK, va="top")
    if subtitle:
        ax.text(2, 56.6, subtitle, fontsize=10, color=INK2, va="top")

    # events box (top-left)
    if events:
        _box(ax, 2, 37, 40, 17, "#fafaf8", GRID, r=1.0)
        ax.text(3.2, 52.6, "what happened at this step", fontsize=9, fontweight="bold", color=INK2, va="top")
        ax.text(3.2, 50.0, "\n".join(f"• {e}" for e in events[:6]), fontsize=8.6, color=INK, va="top",
                linespacing=1.35)
    else:
        _box(ax, 2, 37, 40, 5.5, "#fafaf8", GRID, r=1.0)

    # client + mongos (centre)
    cx = 55
    _box(ax, cx - 9, 50, 18, 5, "white", INK2)
    ax.text(cx, 52.5, client_label, ha="center", va="center", fontsize=8, color=INK)
    m_up = state["mongos"]["docker"].startswith("running")
    _box(ax, cx - 7, 40, 14, 5.5, "#eeeeea" if m_up else "#f1f0ec", INK if m_up else MUTED,
         hatch=None if m_up else "///")
    ax.text(cx, 43.6, "mongos", ha="center", va="center", fontsize=11, fontweight="bold", color=INK)
    ax.text(cx, 41.4, mongos_note or "query router", ha="center", va="center", fontsize=7.6, color=INK2)
    _arrow(ax, (cx, 50), (cx, 45.6), INK2)

    # config replica set (top-right)
    gx, gy, gw, gh = 72, 36.5, 26, 18
    _box(ax, gx, gy, gw, gh, "white", GRID, lw=1.2, r=1.2, z=1)
    ax.text(gx + 1, gy + gh - 1.2, "cfgRS  (config servers)", fontsize=9.5, fontweight="bold", color=INK, va="top")
    t, col = _status_text(cfg_note.get("writes"))
    if t:
        ax.text(gx + gw - 1, gy + gh - 1.2, t.replace("writes", "metadata"), fontsize=8.2, color=col, va="top",
                ha="right", fontweight="bold")
    nw, nh = 7.4, 8
    for i, n in enumerate(REPLSETS["cfgRS"]):
        _node(ax, gx + 1 + i * (nw + 0.9), gy + 4, nw, nh, n, nodes[n], highlight.get(n))
    if cfg_note.get("note"):
        ax.text(gx + 1, gy + 1.2, cfg_note["note"], fontsize=7.8, color=INK2)
    _arrow(ax, (cx + 7, 42.7), (gx, 45), MUTED, lw=1.1, label="routing table", label_color=INK2)

    # shards (bottom row)
    k = len(shards)
    left, right, gap = 2, 98, 2.5
    sw = (right - left - gap * (k - 1)) / k
    sy, sh = 3, 24
    centers = {}
    for j, rs in enumerate(shards):
        x0 = left + j * (sw + gap)
        centers[rs] = (x0 + sw / 2, sy + sh)
        note = shard_notes.get(rs, {})
        _box(ax, x0, sy, sw, sh, "white", GRID, lw=1.2, r=1.2, z=1)
        ax.text(x0 + 1.2, sy + sh - 1.3, rs, fontsize=11, fontweight="bold", color=INK, va="top")
        t, col = _status_text(note.get("writes"))
        if t:
            ax.text(x0 + sw - 1.2, sy + sh - 1.3, t, fontsize=9, color=col, va="top", ha="right", fontweight="bold")
        members = REPLSETS[rs]
        nw = min(9.5, (sw - 2 - 2 * 1.2) / 3)
        tot = 3 * nw + 2 * 1.2
        for i, n in enumerate(members):
            _node(ax, x0 + (sw - tot) / 2 + i * (nw + 1.2), sy + 8.5, nw, 9, n, nodes[n], highlight.get(n))
        prim = [n for n in members if nodes[n]["role"] == "PRIMARY" and nodes[n].get("connected", True)]
        info = []
        if "MB" in note:
            info.append(f"data owned: {note['MB']:.0f} MB")
        info.append(f"primary: {prim[0] if prim else 'NONE'}")
        ax.text(x0 + 1.2, sy + 4.6, "   ".join(info), fontsize=8.6, color=INK if prim else CRIT, va="center",
                fontweight="normal" if prim else "bold")
        if note.get("note"):
            ax.text(x0 + 1.2, sy + 1.9, note["note"], fontsize=7.8, color=INK2, va="center")
        # mongos -> shard
        w = note.get("writes")
        colr, ls = {"ok": (GOOD, "-"), "fail": (CRIT, "--"), "readonly": (WARN, "--"),
                    "partial": (WARN, "--")}.get(w or "", (MUTED, "-"))
        _arrow(ax, (cx, 40), (x0 + sw / 2, sy + sh), colr, ls=ls, lw=1.8 if w else 1.2)

    # migrations between shards: bracket below the shard row (down, across, up into the recipient)
    for k_, mg in enumerate(migrations or []):
        if mg["from"] in centers and mg["to"] in centers:
            xa, xb = centers[mg["from"]][0] + 2 * k_, centers[mg["to"]][0] - 2 * k_
            yb = sy - 2.6 - 1.6 * k_
            ax.plot([xa, xa, xb], [sy, yb, yb], color=BLUE, lw=2.2, zorder=6, solid_capstyle="round")
            _arrow(ax, (xb, yb), (xb, sy), BLUE, lw=2.2, z=6)
            ax.text((xa + xb) / 2, yb, f"chunk migration {mg['from']} → {mg['to']}: {mg.get('label', '')}",
                    ha="center", va="center", fontsize=8.6, color=BLUE, fontweight="bold", zorder=7,
                    bbox=dict(fc="white", ec="none", pad=1.0))

    # legend (inside the lower part of the events box)
    lx, ly = 3.2, 40.4
    for txt, fc, ec, hatch, ls in (("PRIMARY", BLUE, BLUE, None, "-"), ("SECONDARY", BLUE_LT, BLUE, None, "-"),
                                   ("DOWN", "#f1f0ec", MUTED, "///", "-"), ("PAUSED", "#fff6e0", WARN, None, "-"),
                                   ("ISOLATED", "white", CRIT, None, "--")):
        _box(ax, lx, ly - 0.8, 2.0, 1.6, fc, ec, hatch=hatch, ls=ls, r=0.3)
        ax.text(lx + 2.5, ly, txt, fontsize=7.6, color=INK2, va="center")
        lx += 3.6 + len(txt) * 0.78
    ax.text(3.2, 38.2, "mongos→shard arrow: green = writes OK, red dashed = writes failing, amber = degraded",
            fontsize=7.6, color=INK2, va="center")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


def draw_routing(path, title: str, query: str, collection: str, shard_key: str, plan: str,
                 all_shards: list[str], per_shard: dict, totals: dict) -> Path:
    """Query-routing diagram from explain('executionStats') through mongos.

    per_shard: {shard: {"scan": "IXSCAN", "keys": int, "docs": int, "returned": int, "ms": int}} for
    the shards mongos actually contacted; other shards are drawn as 'not contacted'.
    totals: {"returned": int, "docs": int, "keys": int, "server_ms": int, "client_ms": float}
    """
    title, query = title.replace("$", r"\$"), query.replace("$", r"\$")  # '$' would start mathtext
    fig = plt.figure(figsize=(11, 6.2))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 56)
    ax.axis("off")
    ax.text(2, 54.5, title, fontsize=14, fontweight="bold", color=INK, va="top")
    ax.text(2, 50.8, f"db.{collection}.find({query})", fontsize=9.5, color=INK, va="top", family="monospace")
    ax.text(2, 47.8, f"shard key {shard_key}", fontsize=9, color=INK2, va="top")
    targeted = len(per_shard) < len(all_shards)
    verdict = ("TARGETED: " if targeted else "SCATTER-GATHER: ") + \
        f"{len(per_shard)} of {len(all_shards)} shards contacted  ·  explain stage {plan}"
    ax.text(2, 44.6, verdict, fontsize=10.5, fontweight="bold", color=GOOD_TXT if targeted else "#9a6a00", va="top")

    cx = 50
    _box(ax, cx - 10, 36, 20, 5.2, "white", INK2)
    ax.text(cx, 38.6, "client (pymongo)", ha="center", va="center", fontsize=9, color=INK)
    _box(ax, cx - 8, 25.5, 16, 6.2, "#eeeeea", INK)
    ax.text(cx, 29.6, "mongos", ha="center", va="center", fontsize=12, fontweight="bold", color=INK)
    ax.text(cx, 27.2, "routes by chunk ranges", ha="center", va="center", fontsize=7.8, color=INK2)
    _arrow(ax, (cx, 36), (cx, 31.8), INK2)
    ax.text(cx + 11, 38.6, f"returned {totals['returned']:,} docs · client {totals['client_ms']:.1f} ms (median)",
            fontsize=8.6, color=INK2, va="center")

    k = len(all_shards)
    sw, gap = 30, 4
    left = (100 - k * sw - (k - 1) * gap) / 2
    for j, sh in enumerate(all_shards):
        x0 = left + j * (sw + gap)
        st = per_shard.get(sh)
        col = {"shard1RS": "#2a78d6", "shard2RS": "#eb6834", "shard3RS": "#1baf7a"}.get(sh, BLUE)
        if st:
            _box(ax, x0, 2, sw, 15.5, "white", col, lw=2.0)
            ax.text(x0 + 1.2, 16.2, sh, fontsize=11, fontweight="bold", color=INK, va="top")
            ax.text(x0 + sw - 1.2, 16.2, st["scan"], fontsize=9.5, fontweight="bold", va="top", ha="right",
                    color=CRIT if st["scan"] == "COLLSCAN" else GOOD_TXT)
            ax.text(x0 + 1.2, 12.6, f"keys examined   {st['keys']:>9,}\ndocs examined   {st['docs']:>9,}\n"
                    f"docs returned   {st['returned']:>9,}\nshard time      {st['ms']:>7} ms", fontsize=8.8,
                    family="monospace", va="top", color=INK, linespacing=1.45)
            _arrow(ax, (cx, 25.5), (x0 + sw / 2, 17.5), col, lw=2.2)
        else:
            _box(ax, x0, 2, sw, 15.5, "#f5f5f2", MUTED, lw=1.2, ls="--")
            ax.text(x0 + 1.2, 16.2, sh, fontsize=11, fontweight="bold", color=MUTED, va="top")
            ax.text(x0 + sw / 2, 9, "not contacted", ha="center", fontsize=10, color=MUTED)
            ax.plot([cx, x0 + sw / 2], [25.5, 17.5], color=MUTED, lw=1, ls=":")
    ax.text(2, 0.6, f"total: keys examined {totals['keys']:,} · docs examined {totals['docs']:,} · server "
            f"{totals['server_ms']} ms (explain executionStats)", fontsize=8.2, color=INK2)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


def storyboard(paths: list, out, cols: int = 2) -> Path:
    """Stitch step diagrams into one image (rows of `cols`)."""
    from PIL import Image
    ims = [Image.open(p).convert("RGB") for p in paths]
    if not ims:
        return Path(out)
    w, h = ims[0].size
    scale = 0.5
    tw, th = int(w * scale), int(h * scale)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 10, rows * th + (rows + 1) * 10), "white")
    for i, im in enumerate(ims):
        r, c = divmod(i, cols)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (10 + c * (tw + 10), 10 + r * (th + 10)))
    out = Path(out)
    sheet.save(out)
    return out
