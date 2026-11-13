"""E2 - Compare the 4 shard keys while loading, with the balancer OFF and ON.

Phase A (balancer off): drop + shardCollection + load N docs per collection, final distribution.
Phase B (balancer on) : same, sampling every 5 s during the load and afterwards until the balancer
                        is idle for that collection (balancerCompliant and no new migration for
                        3 consecutive samples, max --idle-timeout s).
Phase B runs last so the cluster is left balanced for E3/E4.

    python experiments\\e02_shard_keys.py                 # 500k docs, both phases (~15-20 min)
    python experiments\\e02_shard_keys.py --docs 200000 --phases on

Output results/e02/:
  <phase>_<coll>/dist.csv, opcounters.csv, balancer.csv, docker.csv   raw 5 s samples
  summary.csv / summary.json         one row per (phase, collection, shard)
  img/e02_chart_*.png                MB per shard over time, insert rate per shard, off vs on
"""
import argparse
import csv
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import export, plots  # noqa: E402
from lab.cluster import print_table  # noqa: E402
from lab.chaos import primary as primary_of  # noqa: E402
from lab.common import (CHUNK_SIZE_MB, DB_NAME, Tee, direct_client, log, mongos_client, results_dir,  # noqa: E402
                        save_json, section, shard_distribution)
from lab.load import SHARD_KEYS, create_sharded, load_collection  # noqa: E402
from lab.metrics import Sampler  # noqa: E402

OUT = results_dir("e02")
COLLS = list(SHARD_KEYS)


def migrations_for(c, ns: str, since: datetime) -> int:
    return c.config.changelog.count_documents({"what": "moveChunk.commit", "ns": ns, "time": {"$gte": since}})


def write_share(dist: list[dict]) -> dict:
    """Client insert commands per shard for this collection ($collStats latencyStats.writes.ops).

    The loader sends 1,000-doc insertMany batches; mongos splits each batch per owning shard, so
    every shard that receives part of a batch counts one write op. Migration clones are not counted.
    """
    return {r["shard"]: r["write_ops"] for r in dist}


def client_docs_per_shard(c, phase: str, coll: str) -> dict:
    """Documents written by the CLIENT to each shard during this run.

    = growth of opcounters.insert on the shard (all members; only the primary counts inserts)
      minus documents cloned INTO that shard by migrations (config.changelog moveChunk.commit
      details.counts.cloned). Valid because nothing else writes while E2 runs one collection at a time.
    """
    ops = OUT / f"{phase}_{coll}" / "opcounters.csv"
    first, last = {}, {}
    with open(ops, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["replset"] == "cfgRS":
                continue
            first.setdefault(r["node"], (r["replset"], int(r["insert"])))
            last[r["node"]] = (r["replset"], int(r["insert"]))
    per = defaultdict(int)
    for n, (rs, v) in last.items():
        per[rs] += v - first[n][1]
    if phase == "on":
        ns = f"{DB_NAME}.{coll}"
        start = c.config.changelog.find_one({"what": "shardCollection.start", "ns": ns}, sort=[("time", -1)])
        q = {"what": "moveChunk.commit", "ns": ns, "time": {"$gte": start["time"]}} if start else None
        for d in c.config.changelog.find(q) if q else []:
            det = d.get("details", {})
            per[det.get("to")] -= int((det.get("counts") or {}).get("cloned", 0))
    return dict(per)


def run_one(c, phase: str, coll: str, docs: int, idle_timeout: float) -> dict:
    ns = f"{DB_NAME}.{coll}"
    sub = OUT / f"{phase}_{coll}"
    section(f"{phase.upper()} | {coll}  key={SHARD_KEYS[coll]}")
    create_sharded(c, coll)
    since = datetime.now(timezone.utc)
    t0 = time.time()
    sampler = Sampler(sub, [coll], interval=5.0, t0=t0, tag=phase).start()
    stats = load_collection(c, coll, docs, on_progress=lambda done, el: None)
    t_load = time.time() - t0
    t_idle = None
    if phase == "on":
        stable, last_mig, waited = 0, -1, 0.0
        while waited < idle_timeout:
            time.sleep(5)
            waited = time.time() - t0 - t_load
            mig = migrations_for(c, ns, since)
            comp = c.admin.command("balancerCollectionStatus", ns).get("balancerCompliant")
            stable = stable + 1 if (comp and mig == last_mig) else 0
            last_mig = mig
            if stable >= 3:
                # idle since the last change, i.e. 3 samples (~15 s) ago
                t_idle = time.time() - t0 - 15
                break
        if t_idle is None:
            log(f"balancer NOT idle after {idle_timeout}s for {coll}")
    time.sleep(1)
    sampler.sample_once()
    sampler.stop()
    dist = shard_distribution(c, coll)
    shares = write_share(dist)
    total_ins = sum(shares.values()) or 1
    mig = migrations_for(c, ns, since)
    print_table(dist, ["shard", "owned_docs", "owned_MB", "orphan_docs", "orphan_MB", "raw_MB", "chunks",
                       "jumbo", "write_ops"])
    print(f"\nload {t_load:.1f}s ({stats['docs_per_s']:,} docs/s); migrations committed: {mig}; "
          + (f"balancer idle {t_idle - t_load:.0f}s after load end (t={t_idle:.0f}s)" if t_idle else
             ("balancer off" if phase == "off" else "balancer NOT idle")))
    print("client insert commands per shard (latencyStats, 1,000-doc batches split by mongos): "
          + ", ".join(f"{k} {v:,} ({100 * v / total_ins:.0f}%)" for k, v in sorted(shares.items())))
    cdocs = client_docs_per_shard(c, phase, coll)
    tot_docs = sum(cdocs.values()) or 1
    print("client documents per shard (opcounters.insert minus migration clones): "
          + ", ".join(f"{k} {v:,} ({100 * v / tot_docs:.0f}%)" for k, v in sorted(cdocs.items())))
    if phase == "off":
        # leave nothing unbalanced behind: otherwise the balancer migrates these collections
        # during phase B and contaminates its measurements (observed in the first E2 run)
        c[DB_NAME][coll].drop()
    rows = []
    owned_total = sum(r["owned_MB"] for r in dist) or 1
    for r in dist:
        rows.append({"phase": phase, "coll": coll, "shard_key": str(SHARD_KEYS[coll]), **r,
                     "owned_pct": round(100 * r["owned_MB"] / owned_total, 1),
                     "insert_cmds": shares.get(r["shard"], 0),
                     "insert_pct": round(100 * shares.get(r["shard"], 0) / total_ins, 1),
                     "client_docs": cdocs.get(r["shard"], 0),
                     "client_docs_pct": round(100 * cdocs.get(r["shard"], 0) / tot_docs, 1),
                     "load_s": round(t_load, 1), "docs_per_s": stats["docs_per_s"], "migrations": mig,
                     "idle_after_load_s": round(t_idle - t_load, 1) if t_idle else "",
                     "imbalance_MB": round(max(x["owned_MB"] for x in dist) - min(x["owned_MB"] for x in dist), 1)})
    return {"rows": rows, "t_load": t_load, "t_idle": t_idle}


def _fmt_bound(v) -> str:
    from bson.max_key import MaxKey
    from bson.min_key import MinKey
    if isinstance(v, MinKey):
        return "MinKey"
    if isinstance(v, MaxKey):
        return "MaxKey"
    if isinstance(v, datetime):
        return v.strftime("%m-%d %H:%M")
    if isinstance(v, str):
        return (v.replace("Wrecks - ", "").replace("_", " ") or '""')[:32]
    return f"hash {v}" if isinstance(v, int) else str(v)


def chunk_map(c, runs: dict) -> None:
    """Key-space map: every chunk in shard-key order, width = MB (dataSize), colour = owning shard.
    Balancer ON rows come from config.chunks now; OFF rows are reconstructed from the OFF summary
    (1 chunk [MinKey, MaxKey) per range key; hashed keeps its initial 2 chunks, as ON had 0 migrations)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    plots.setup()
    fig, ax = plt.subplots(figsize=(13.5, 10))
    ax.set_xlim(-0.30, 1.16)
    ax.axis("off")
    y = 0
    rows_meta = []
    for coll in COLLS:
        key = SHARD_KEYS[coll]
        meta = c.config.collections.find_one({"_id": f"{DB_NAME}.{coll}"})
        if not meta:
            continue
        chunks = list(c.config.chunks.find({"uuid": meta["uuid"]}).sort("min", 1))
        on = []
        for ch in chunks:
            # ask the OWNING shard's primary directly: dataSize through mongos sums every shard and so
            # also counts orphans still waiting for the range deleter (observed: +27 MB on ship_compound)
            with direct_client(primary_of(ch["shard"]), socketTimeoutMS=120000) as d:
                r = d.admin.command("dataSize", f"{DB_NAME}.{coll}", keyPattern=key, min=ch["min"],
                                    max=ch["max"], estimate=False)
            on.append({"shard": ch["shard"], "MB": r["size"] / 2**20, "min": ch["min"], "jumbo": ch.get("jumbo")})
        off_rows = [r for r in runs.get(("off", coll), {}).get("rows", [])]
        if coll == "ship_hashed":
            off = [dict(x, jumbo=False) for x in on]
        elif off_rows:
            off = [{"shard": off_rows[0]["shard"], "MB": off_rows[0]["owned_MB"],
                    "min": {k: None for k in key}, "jumbo": False}]
        else:
            off = []
        rows_meta.append((coll, key, off, on))

    total = max(sum(x["MB"] for x in on) for _, _, _, on in rows_meta)
    labelled = 0
    for coll, key, off, on in rows_meta:
        for label, segs in (("balancer OFF", off), ("balancer ON", on)):
            if not segs:
                continue
            x = 0.0
            for i, s in enumerate(segs):
                w = s["MB"] / total
                ax.add_patch(Rectangle((x, y), w, 0.62, fc=plots.color(s["shard"]), ec="white", lw=1.5,
                                       hatch="xx" if s["jumbo"] else None))
                if w > 0.035:
                    ax.text(x + w / 2, y + 0.31, ("JUMBO " if s["jumbo"] else "") + f"{s['MB']:.0f} MB",
                            ha="center", va="center", fontsize=7.5, color="white", fontweight="bold",
                            bbox=dict(fc=plots.color(s["shard"]), ec="none", pad=1.5) if s["jumbo"] else None)
                if label == "balancer ON" and (w > 0.04 or i == 0 or s["jumbo"]):
                    txt = " / ".join(_fmt_bound(v) for v in s["min"].values())
                    lvl = labelled % 2
                    labelled += 1
                    ax.plot([x, x], [y + 0.62, y + 0.70 + 0.24 * lvl], color=plots.INK2, lw=0.7)
                    ax.text(x + 0.003, y + 0.74 + 0.24 * lvl, txt, fontsize=6.8, color=plots.INK2, va="center")
                x += w
            ax.text(-0.01, y + 0.31, label, ha="right", va="center", fontsize=8.5, color=plots.INK2)
            if coll == "ship_mono":
                ax.annotate("new inserts\n(max ts)", xy=(x, y + 0.31), xytext=(x + 0.05, y + 0.31), va="center",
                            fontsize=7.8, color=plots.INK, arrowprops=dict(arrowstyle="-|>", color=plots.INK, lw=1.2))
            if label == "balancer OFF":
                y += 0.85
                labelled = 0
        ax.text(-0.29, y - 0.12, f"{coll}\n{key}", fontsize=9.5, fontweight="bold", color=plots.INK,
                va="center")
        y += 1.75
    ax.set_ylim(y + 0.2, -0.6)
    for i, sh in enumerate(sorted({s["shard"] for _, _, off, on in rows_meta for s in off + on})):
        ax.add_patch(Rectangle((0.0 + i * 0.16, -0.55), 0.025, 0.28, fc=plots.color(sh)))
        ax.text(0.03 + i * 0.16, -0.41, sh, fontsize=8.5, va="center", color=plots.INK)
    ax.add_patch(Rectangle((0.34, -0.55), 0.025, 0.28, fc="white", ec=plots.INK2, hatch="xx"))
    ax.text(0.37, -0.41, "jumbo chunk (cannot be split or moved)", fontsize=8.5, va="center", color=plots.INK)
    ax.text(0.0, y - 0.35, "Each bar = the whole shard-key range in key order (MinKey → MaxKey). Each block = one chunk; "
            "width ∝ its data (dataSize on the owning shard), colour = owning shard;\nlabel = chunk lower bound. "
            "ON rows: config.chunks after E2. OFF rows: from the OFF-phase distribution (range keys keep a single "
            "chunk; hashed keeps its 2 initial chunks).", fontsize=8, color=plots.INK2, va="top")
    ax.set_title("E2 - chunk map per shard key after loading 500k documents", loc="left", fontsize=13)
    plots.save(fig, OUT / "img" / "e02_chart_chunk_map.png")


def charts(runs: dict) -> None:
    import matplotlib.pyplot as plt
    plots.setup()
    img = OUT / "img"
    img.mkdir(exist_ok=True)

    def read(path):
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    # 1. owned MB per shard over time (balancer on), small multiples with a shared y-axis
    if any(k[0] == "on" for k in runs):
        fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharey=True)
        for ax, coll in zip(axes.flat, COLLS):
            if ("on", coll) not in runs:
                ax.set_visible(False)
                continue
            rows = read(OUT / f"on_{coll}" / "dist.csv")
            for i, sh in enumerate(sorted({r["shard"] for r in rows})):
                pts = [(float(r["t_rel_s"]), float(r["owned_MB"])) for r in rows if r["shard"] == sh]
                ax.plot(*zip(*pts), color=plots.color(sh, i), label=sh)
                ax.annotate(f"{pts[-1][1]:.0f} MB", pts[-1], textcoords="offset points", xytext=(4, 0),
                            fontsize=8, color=plots.INK2, va="center")
            run = runs[("on", coll)]
            ax.axvline(run["t_load"], color=plots.INK2, lw=1, ls="--")
            ax.text(run["t_load"], ax.get_ylim()[1] * 0.97, " load end", fontsize=8, color=plots.INK2, va="top")
            if run["t_idle"]:
                ax.axvline(run["t_idle"], color=plots.INK2, lw=1, ls=":")
                ax.text(run["t_idle"], ax.get_ylim()[1] * 0.85, " balancer idle", fontsize=8, color=plots.INK2, va="top")
            ax.set_title(f"{coll}  {SHARD_KEYS[coll]}", fontsize=10)
            ax.set_xlabel("seconds since load start")
            ax.set_ylabel("owned data (MB)")
            ax.legend(loc="upper left")
        fig.suptitle("E2 - data owned per shard during load (balancer ON, 5 s samples)", fontweight="bold")
        plots.save(fig, img / "e02_chart_mb_over_time.png")

    # 2. insert rate per shard over time (hot shard), balancer on
    for phase in ("on", "off"):
        if not any(k[0] == phase for k in runs):
            continue
        fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharey=True)
        for ax, coll in zip(axes.flat, COLLS):
            if (phase, coll) not in runs:
                ax.set_visible(False)
                continue
            rows = read(OUT / f"{phase}_{coll}" / "dist.csv")
            per = defaultdict(dict)  # shard -> t -> cumulative write ops
            for r in rows:
                per[r["shard"]][float(r["t_rel_s"])] = int(r["write_ops"] or 0)
            for i, sh in enumerate(sorted(per)):
                ts = sorted(per[sh])
                rate = [(ts[j], (per[sh][ts[j]] - per[sh][ts[j - 1]]) / (ts[j] - ts[j - 1]))
                        for j in range(1, len(ts))]
                if rate:
                    ax.plot(*zip(*rate), color=plots.color(sh, i), label=sh)
            ax.axvline(runs[(phase, coll)]["t_load"], color=plots.INK2, lw=1, ls="--")
            ax.set_title(f"{coll}  {SHARD_KEYS[coll]}", fontsize=10)
            ax.set_xlabel("seconds since load start")
            ax.set_ylabel("insert commands / s")
            ax.legend(loc="upper right")
        fig.suptitle(f"E2 - insert commands received per shard (balancer {phase.upper()}): which shards each "
                     "1,000-doc batch touches", fontweight="bold", fontsize=11)
        plots.save(fig, img / f"e02_chart_insert_rate_{phase}.png")

    # 3. final owned MB per shard, balancer off vs on (grouped bars)
    phases = [p for p in ("off", "on") if any(k[0] == p for k in runs)]
    fig, axes = plt.subplots(1, len(phases), figsize=(5.5 * len(phases), 4.2), sharey=True, squeeze=False)
    for ax, phase in zip(axes[0], phases):
        shards = sorted({r["shard"] for k, v in runs.items() if k[0] == phase for r in v["rows"]})
        w = 0.8 / max(1, len(shards))
        for i, sh in enumerate(shards):
            xs, ys = [], []
            for j, coll in enumerate(COLLS):
                if (phase, coll) in runs:
                    r = next((x for x in runs[(phase, coll)]["rows"] if x["shard"] == sh), None)
                    xs.append(j + (i - (len(shards) - 1) / 2) * w)
                    ys.append(r["owned_MB"] if r else 0)
            bars = ax.bar(xs, ys, width=w * 0.92, color=plots.color(sh, i), label=sh)
            for b, y in zip(bars, ys):
                ax.text(b.get_x() + b.get_width() / 2, y + 2, f"{y:.0f}", ha="center", fontsize=8, color=plots.INK2)
        ax.set_xticks(range(len(COLLS)))
        ax.set_xticklabels([c.replace("ship_", "") for c in COLLS])
        ax.set_title(f"balancer {phase.upper()}")
        ax.set_ylabel("owned data after load (MB)")
        ax.legend(loc="upper right")
        ax.grid(axis="x", visible=False)
    fig.suptitle("E2 - final data per shard by shard key", fontweight="bold")
    plots.save(fig, img / "e02_chart_final_off_vs_on.png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=500_000)
    ap.add_argument("--phases", nargs="*", default=["off", "on"], choices=["off", "on"])
    ap.add_argument("--colls", nargs="*", default=COLLS, choices=COLLS)
    ap.add_argument("--idle-timeout", type=float, default=900)
    a = ap.parse_args()

    section("E2 configuration")
    print(f"documents per collection: {a.docs:,}   chunk size: {CHUNK_SIZE_MB} MB   "
          f"migration threshold (3 x chunk): {3 * CHUNK_SIZE_MB} MB   sample interval: 5 s")
    print("shard keys:", {k: SHARD_KEYS[k] for k in a.colls})
    runs, all_rows = {}, []
    with mongos_client() as c:
        for coll in a.colls:  # clean start: nothing left over for the balancer to work on
            c[DB_NAME][coll].drop()
        for phase in a.phases:
            if phase == "off":
                c.admin.command("balancerStop")
            else:
                c.admin.command("balancerStart")
            log(f"balancer {'stopped' if phase == 'off' else 'started'}: {c.admin.command('balancerStatus')['mode']}")
            for coll in a.colls:
                runs[(phase, coll)] = run_one(c, phase, coll, a.docs, a.idle_timeout)
                all_rows += runs[(phase, coll)]["rows"]
        c.admin.command("balancerStart")

    section("E2 summary (owned data excludes orphans awaiting the range deleter)")
    write_summary(all_rows, runs)
    log(f"E2 done -> {OUT}")


SUMMARY_COLS = ["phase", "coll", "shard", "owned_docs", "owned_MB", "owned_pct", "orphan_MB", "chunks", "jumbo",
                "client_docs_pct", "insert_cmds", "load_s", "migrations", "idle_after_load_s", "imbalance_MB"]


def write_summary(all_rows, runs) -> None:
    print_table(all_rows, SUMMARY_COLS)
    print("\nclient_docs_pct = share of the client's documents written to each shard (hot-shard indicator):\n"
          "  opcounters.insert of the shard minus documents cloned into it by migrations.\n"
          "insert_cmds = insert commands received (a 1,000-doc batch touching both shards counts on both).\n"
          "idle_after_load_s has 5 s resolution (sampling interval); the threshold for a migration is\n"
          f"  {3 * CHUNK_SIZE_MB} MB difference (3 x {CHUNK_SIZE_MB} MB chunk size).")
    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    save_json(OUT / "summary.json", all_rows)
    charts(runs)
    with mongos_client() as c:
        chunk_map(c, runs)


def replot() -> None:
    """Recompute derived columns + charts from the saved CSVs (no reload). Rewrites the summary section."""
    import json
    rows = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    runs = {}
    with mongos_client() as c:
        for key in dict.fromkeys((r["phase"], r["coll"]) for r in rows):
            cdocs = client_docs_per_shard(c, *key)
            tot = sum(cdocs.values()) or 1
            rr = [r for r in rows if (r["phase"], r["coll"]) == key]
            for r in rr:
                r["client_docs"] = cdocs.get(r["shard"], 0)
                r["client_docs_pct"] = round(100 * r["client_docs"] / tot, 1)
            idle = rr[0]["idle_after_load_s"]
            runs[key] = {"rows": rr, "t_load": rr[0]["load_s"],
                         "t_idle": rr[0]["load_s"] + idle if idle != "" else None}
    con = OUT / "console.txt"
    text = con.read_text(encoding="utf-8")
    cut = text.find("\n" + "=" * 78 + "\nE2 summary")
    con.write_text(text[:cut] if cut >= 0 else text, encoding="utf-8")
    with Tee(con, mode="a"):
        section("E2 summary (owned data excludes orphans awaiting the range deleter)")
        write_summary(rows, runs)


if __name__ == "__main__":
    if "--replot" in sys.argv:
        replot()
    else:
        with Tee(OUT / "console.txt"):
            main()
    export.export("e02")
