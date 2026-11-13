"""E9 - Scale-out: add a third shard and watch the balancer move data onto it.

To limit disk use (~0.5 GB) only ship_hashed and ship_compound are balanced onto the new shard;
balancing is switched off for ship_mono and ship_lowcard (noBalance, same as sh.disableBalancing) and left off.

Timeline: baseline 20 s -> start s3a/s3b/s3c (docker-compose.shard3.yml), initiate shard3RS, addShard ->
balancer migrates chunks (8.0: ranges of ~chunk size, split during migration) until each collection is
balanced (balancerCompliant + no new migration for 3 samples of 5 s; max 20 min).
writer.py inserts generated documents into ship_hashed (a migrating collection) every 100 ms the whole time.

Output results/e09/: metrics/ (dist, opcounters, balancer, docker CSV), writer.csv, migrations.csv, summary.json,
img/e09_stepN.png, storyboard, e09_chart_mb.png, e09_chart_writer.png
"""
import csv
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import cluster, diagram, export, plots  # noqa: E402
from lab.cluster import print_table, wait_healthy_cluster  # noqa: E402
from lab.common import (DB_NAME, Tee, docker, log, mongos_client, results_dir, save_json, section,  # noqa: E402
                        shard_distribution)
from lab.metrics import Sampler  # noqa: E402
from lab.writer import Writer  # noqa: E402

OUT = results_dir("e09")
BAL = ["ship_hashed", "ship_compound"]
NOBAL = ["ship_mono", "ship_lowcard"]
SHARDS3 = ["shard1RS", "shard2RS", "shard3RS"]


def migrations_since(c, since: datetime) -> list[dict]:
    out = []
    for d in c.config.changelog.find({"what": "moveChunk.commit", "ns": {"$in": [f"{DB_NAME}.{x}" for x in BAL]},
                                      "time": {"$gte": since}}).sort("time", 1):
        det = d.get("details", {})
        out.append({"time": d["time"].replace(tzinfo=timezone.utc).timestamp(), "ns": d["ns"], "from": det.get("from"),
                    "to": det.get("to"), "docs": (det.get("counts") or {}).get("cloned"),
                    "MB": round((det.get("counts") or {}).get("clonedBytes", 0) / 2**20, 2)})
    return out


def owned_by_shard(c) -> dict:
    tot = {}
    per = {}
    for coll in BAL:
        for r in shard_distribution(c, coll):
            tot[r["shard"]] = tot.get(r["shard"], 0) + r["owned_MB"]
            per[(coll, r["shard"])] = r["owned_MB"]
    return {"total": tot, "per": per}


def capture(c, t0, note):
    st = diagram.snapshot(inside_fallback=False)
    return {"t": time.time(), "state": st, "owned": owned_by_shard(c), "note": note}


def main() -> None:
    wait_healthy_cluster(120)
    section("E9 setup")
    print("disk estimate: 3 new containers + volumes for shard3RS, ~0.5 GB in total inside Docker")
    with mongos_client() as c:
        for coll in NOBAL:
            prev = (c.config.collections.find_one({"_id": f"{DB_NAME}.{coll}"}) or {}).get("noBalance", False)
            c.config.collections.update_one({"_id": f"{DB_NAME}.{coll}"}, {"$set": {"noBalance": True}})
            print(f"noBalance {DB_NAME}.{coll}: {prev} -> True (not moved to shard3, to limit disk use)")
        c.admin.command("balancerStart")
        before = {coll: shard_distribution(c, coll) for coll in BAL}
        for coll in BAL:
            print(f"\n{coll} before:")
            print_table(before[coll], ["shard", "owned_docs", "owned_MB", "chunks", "jumbo"])
        t0 = time.time()
        since = datetime.now(timezone.utc)
        sampler = Sampler(OUT / "metrics", BAL, interval=5.0, t0=t0, tag="e09").start()
        writer = Writer(OUT / "writer.csv", coll="ship_hashed").start()
        frames = [capture(c, t0, "before")]
        time.sleep(20)

        section("E9 add shard3RS")
        t_start = time.time()
        cluster.shard3()
        t_added = time.time()
        log(f"shard3RS started + initiated + added in {t_added - t_start:.1f} s")
        frames.append(capture(c, t0, "added"))

        section("E9 balancing")
        stable, last_n, t_idle, t_first = 0, -1, None, None
        next_cap = time.time() + 30
        while time.time() - t_added < 1200:
            time.sleep(5)
            mig = migrations_since(c, since)
            if mig and t_first is None:
                t_first = mig[0]["time"]
                frames.append(capture(c, t0, "first migration"))
            comp = all(c.admin.command("balancerCollectionStatus", f"{DB_NAME}.{x}").get("balancerCompliant") for x in BAL)
            stable = stable + 1 if comp and len(mig) == last_n else 0
            last_n = len(mig)
            if time.time() > next_cap:
                frames.append(capture(c, t0, "progress"))
                next_cap = time.time() + 30
                tot = frames[-1]["owned"]["total"]
                log(f"migrations so far {len(mig)}; owned MB (balanced colls): "
                    + ", ".join(f"{k} {v:.0f}" for k, v in sorted(tot.items())))
            if stable >= 3 and len(mig) > 0:
                t_idle = time.time() - 15
                break
        frames.append(capture(c, t0, "balanced"))
        log(f"balanced: {last_n} migrations, idle at +{(t_idle or time.time()) - t_added:.0f} s after addShard")
        time.sleep(10)
        wrows = writer.stop()
        sampler.stop()
        mig = migrations_since(c, since)
        after = {coll: shard_distribution(c, coll) for coll in BAL}

    # ---- results
    section("E9 results")
    with open(OUT / "migrations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["time", "ns", "from", "to", "docs", "MB"])
        w.writeheader()
        w.writerows(mig)
    for coll in BAL:
        print(f"\n{coll} after:")
        print_table(after[coll], ["shard", "owned_docs", "owned_MB", "orphan_MB", "chunks", "jumbo"])
    by = {}
    for m in mig:
        k = (m["ns"].split(".")[1], m["from"], m["to"])
        by.setdefault(k, [0, 0.0])
        by[k][0] += 1
        by[k][1] += m["MB"]
    print("\nmigrations committed:")
    print_table([{"collection": k[0], "from": k[1], "to": k[2], "count": v[0], "MB_moved": round(v[1], 1)}
                 for k, v in sorted(by.items())])

    def lat(rows):
        v = sorted(r["latency_ms"] for r in rows if r["ok"])
        return {"writes": len(rows), "failed": sum(1 for r in rows if not r["ok"]),
                "p50_ms": v[len(v) // 2] if v else None, "p95_ms": v[int(len(v) * 0.95)] if v else None,
                "max_ms": v[-1] if v else None}
    t_mig_end = mig[-1]["time"] if mig else t_added
    phases = {"before addShard": [r for r in wrows if r["t_epoch"] < t_start],
              "during migrations": [r for r in wrows if (t_first or t_added) <= r["t_epoch"] <= t_mig_end],
              "after balancing": [r for r in wrows if r["t_epoch"] > t_mig_end]}
    lrows = [{"phase": k, **lat(v)} for k, v in phases.items()]
    print("\nwriter.py inserts into ship_hashed (through mongos):")
    print_table(lrows)
    total_mb = sum(m["MB"] for m in mig)
    vols = docker("system", "df", "-v", "--format", "{{range .Volumes}}{{.Name}} {{.Size}}\n{{end}}", check=False)
    s3vols = [l for l in vols.splitlines() if "s3" in l and l.startswith("mongolab_")]
    print(f"\nshard3 volumes: {', '.join(s3vols)}")
    res = {"t_add_s": round(t_added - t_start, 1),
           "first_migration_after_add_s": round(t_first - t_added, 1) if t_first else None,
           "balanced_after_add_s": round(t_idle - t_added, 1) if t_idle else None,
           "migrations": len(mig), "MB_moved": round(total_mb, 1), "by_route": {f"{k[0]} {k[1]}->{k[2]}": v for k, v in by.items()},
           "writer": lrows, "before": before, "after": after, "s3_volumes": s3vols}
    print_table([{k: v for k, v in res.items() if k in ("t_add_s", "first_migration_after_add_s",
                                                         "balanced_after_add_s", "migrations", "MB_moved")}])
    res.update(t0=t0, t_start=t_start, t_added=t_added, t_first=t_first, t_idle=t_idle)
    save_json(OUT / "summary.json", res)
    save_json(OUT / "frames.json", [{k: v for k, v in f.items()} for f in frames])
    charts(t0, t_start, t_added, t_first, t_idle, mig, wrows)
    draw_steps(frames, mig, t0, t_added)
    print()
    cluster.healthy()
    log(f"E9 done -> {OUT}")


def charts(t0, t_start, t_added, t_first, t_idle, mig, wrows):
    import matplotlib.pyplot as plt
    plots.setup()
    (OUT / "img").mkdir(exist_ok=True)
    with open(OUT / "metrics" / "dist.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fig, axes = plt.subplots(1, len(BAL), figsize=(13, 4.6), sharey=True)
    for ax, coll in zip(axes, BAL):
        for i, sh in enumerate(SHARDS3):
            pts = [(float(r["t_rel_s"]), float(r["owned_MB"])) for r in rows if r["coll"] == coll and r["shard"] == sh]
            if pts:
                ax.plot(*zip(*pts), color=plots.color(sh), label=sh)
                ax.annotate(f"{pts[-1][1]:.0f} MB", pts[-1], textcoords="offset points", xytext=(4, 0), fontsize=8,
                            color=plots.INK2, va="center")
        ax.axvline(t_added - t0, color=plots.INK2, lw=1, ls="--")
        ax.text(t_added - t0, ax.get_ylim()[1] * 0.97, " addShard", fontsize=8, color=plots.INK2, va="top")
        if t_idle:
            ax.axvline(t_idle - t0, color=plots.INK2, lw=1, ls=":")
            ax.text(t_idle - t0, ax.get_ylim()[1] * 0.85, " balanced", fontsize=8, color=plots.INK2, va="top")
        ax.set_title(coll, fontsize=10)
        ax.set_xlabel("seconds since start")
        ax.legend(loc="center left", fontsize=8)
    axes[0].set_ylabel("data owned (MB)")
    fig.suptitle("E9 - data per shard after adding shard3RS (5 s samples)", fontweight="bold")
    plots.save(fig, OUT / "img" / "e09_chart_mb.png")

    fig, ax = plt.subplots(figsize=(13, 4.2))
    ok = [(r["t_epoch"] - t0, r["latency_ms"]) for r in wrows if r["ok"]]
    bad = [(r["t_epoch"] - t0, max(r["latency_ms"], 1)) for r in wrows if not r["ok"]]
    ax.scatter(*zip(*ok), s=6, color="#2a78d6", label=f"insert ok ({len(ok)})")
    if bad:
        ax.scatter(*zip(*bad), s=26, marker="x", color="#d03b3b", label=f"insert failed ({len(bad)})")
    for m in mig:
        ax.axvline(m["time"] - t0, color="#eb6834", lw=0.6, alpha=0.6)
    ax.plot([], [], color="#eb6834", lw=0.8, label="migration commit")
    ax.axvline(t_added - t0, color=plots.INK2, lw=1, ls="--")
    ax.set_yscale("log")
    ax.set_ylabel("insert latency (ms, log)")
    ax.set_xlabel("seconds since start")
    ax.legend(loc="upper left", bbox_to_anchor=(1, 1), fontsize=8)
    ax.set_title("E9 - writer.py inserts into ship_hashed while its chunks migrate", loc="left")
    plots.save(fig, OUT / "img" / "e09_chart_writer.png")


def draw_steps(frames, mig, t0, t_added):
    prog = frames[2:-1]
    mid = [prog[len(prog) // 2]] if len(prog) >= 2 else []
    chosen = [frames[0], frames[1]] + prog[:1] + mid + [frames[-1]]
    pngs = []
    titles = {"before": "two shards (before scale-out)", "added": "shard3RS added (empty)",
              "first migration": "first chunk migration", "progress": "balancing in progress",
              "balanced": "balanced across three shards"}
    for i, f in enumerate(chosen, 1):
        tot = f["owned"]["total"]
        recent = [m for m in mig if f["t"] - 30 <= m["time"] <= f["t"]]
        arrows = {}
        for m in recent:
            arrows.setdefault((m["from"], m["to"]), [0, 0.0])
            arrows[(m["from"], m["to"])][0] += 1
            arrows[(m["from"], m["to"])][1] += m["MB"]
        mlist = [{"from": a, "to": b, "label": f"{v[0]} chunk(s), {v[1]:.0f} MB in last 30 s"} for (a, b), v in arrows.items()]
        done = [m for m in mig if m["time"] <= f["t"]]
        ev = [f"t = {f['t'] - t0:.0f} s since start" + (f" ({f['t'] - t_added:+.0f} s vs addShard)" if i > 1 else ""),
              f"migrations committed so far: {len(done)} ({sum(m['MB'] for m in done):.0f} MB)",
              "owned MB (ship_hashed + ship_compound): " + ", ".join(f"{k[:6]} {v:.0f}" for k, v in sorted(tot.items())),
              "writer.py keeps inserting into ship_hashed"]
        notes = {sh: {"writes": "ok", "MB": tot.get(sh, 0)} for sh in SHARDS3}
        pngs.append(diagram.draw(f["state"], OUT / "img" / f"e09_step{i}.png", f"E9 - step {i}: {titles[f['note']]}",
                                 "observed state; data owned counts only the two balanced collections",
                                 shard_notes=notes, events=ev, migrations=mlist,
                                 client_label="client  (writer.py into ship_hashed)"))
    diagram.storyboard(pngs, OUT / "img" / "e09_storyboard.png", cols=2)


def replot() -> None:
    """Rebuild charts and step diagrams from the saved files (first E9 run crashed before drawing them).
    Times come from summary.json + migrations.csv + writer.csv; per-step MB per shard from metrics/dist.csv;
    node states from a current snapshot (no role changed during E9), with shard3 hidden before addShard."""
    import copy
    import json
    res = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    with open(OUT / "migrations.csv", encoding="utf-8") as f:
        mig = [dict(r, time=float(r["time"]), MB=float(r["MB"])) for r in csv.DictReader(f)]
    with open(OUT / "writer.csv", encoding="utf-8") as f:
        wrows = [dict(r, ok=int(r["ok"]), t_epoch=float(r["t_epoch"]), latency_ms=float(r["latency_ms"]))
                 for r in csv.DictReader(f)]
    with open(OUT / "metrics" / "dist.csv", encoding="utf-8") as f:
        dist = list(csv.DictReader(f))
    if "t0" in res:
        t0, t_start, t_added, t_first, t_idle = (res[k] for k in ("t0", "t_start", "t_added", "t_first", "t_idle"))
    else:
        t0 = wrows[0]["t_epoch"] - float(wrows[0]["t_rel_s"])
        t_first = mig[0]["time"]
        t_added = t_first - res["first_migration_after_add_s"]
        t_start = t_added - res["t_add_s"]
        t_idle = t_added + res["balanced_after_add_s"] if res["balanced_after_add_s"] else None
    charts(t0, t_start, t_added, t_first, t_idle, mig, wrows)
    now = diagram.snapshot(inside_fallback=False)
    no3 = copy.deepcopy(now)
    no3["nodes"] = {n: v for n, v in now["nodes"].items() if v["rs"] != "shard3RS"}

    def owned_at(t_rel):
        ts = sorted({float(r["t_rel_s"]) for r in dist})
        tt = max([x for x in ts if x <= t_rel] or [ts[0]])
        tot = {}
        for r in dist:
            if float(r["t_rel_s"]) == tt:
                tot[r["shard"]] = tot.get(r["shard"], 0) + float(r["owned_MB"])
        return {"total": tot}
    frames = [{"t": t0 + 15, "state": no3, "owned": owned_at(15), "note": "before"},
              {"t": t_added + 2, "state": now, "owned": owned_at(t_added + 2 - t0), "note": "added"}]
    if t_first:
        frames.append({"t": t_first + 3, "state": now, "owned": owned_at(t_first + 3 - t0), "note": "first migration"})
        mid = t_first + ((t_idle or mig[-1]["time"]) - t_first) / 2
        frames.append({"t": mid, "state": now, "owned": owned_at(mid - t0), "note": "progress"})
    end = (t_idle or mig[-1]["time"]) + 5
    frames.append({"t": end, "state": now, "owned": owned_at(end - t0), "note": "balanced"})
    draw_steps(frames, mig, t0, t_added)
    log("E9 replot done")


if __name__ == "__main__":
    if "--replot" in sys.argv:
        replot()
    else:
        with Tee(OUT / "console.txt"):
            main()
    export.export("e09")
