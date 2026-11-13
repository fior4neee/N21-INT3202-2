"""E5 - Replication and read scaling.

Part 1 - read preference routing: reader.py through mongos at 20 reads/s for 20 s per mode
         (primary, secondary, secondaryPreferred, nearest); every read is attributed to the node that
         executed it via the per-node profiler.
Part 2 - replication lag: every second a probe document is inserted (w:1, through mongos) into each shard
         (lab.probe, k<0 -> shard1RS, k>0 -> shard2RS) and both secondaries of that shard are polled
         directly until the document is visible -> lag in ms per secondary.
         Phases: idle 20 s -> write load 45 s on the loaded shard -> recovery 20 s.
         Write load = 4 threads of 500-doc UpdateOne batches (w:1) on lab.e05_load (20k docs x ~1 KB,
         unsharded, lives on the database's primary shard). Updates, not inserts: data does not grow.
         The other shard gets no load and is the control.

Output results/e05/: reads_<mode>.csv, reads_summary.csv, lag.csv, lag_summary.csv, load.csv,
img/e05_reads_<mode>.png, img/e05_storyboard_reads.png, img/e05_lag_diagram.png, img/e05_chart_*.png
"""
import csv
import os
import random
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymongo  # noqa: E402
from pymongo import UpdateOne  # noqa: E402

from lab import diagram, export, plots, reader  # noqa: E402
from lab.chaos import primary as primary_of  # noqa: E402
from lab.cluster import print_table  # noqa: E402
from lab.common import (DB_NAME, MONGOS_URI, REPLSETS, Tee, direct_client, log, mongos_client,  # noqa: E402
                        results_dir, save_json, section)
from lab.writer import ensure_probe  # noqa: E402

OUT = results_dir("e05")
MODES = ["primary", "secondary", "secondaryPreferred", "nearest"]
LOAD_DOCS = 20_000
PHASES = [("idle", 20), ("load", 45), ("recovery", 20)]


# ---------------------------------------------------------------- part 1
def part1(keys) -> list[dict]:
    summary = []
    pngs = []
    for mode in MODES:
        state = diagram.snapshot(inside_fallback=False)
        rows = reader.run(mode, rate=20, duration=20, out=OUT / f"reads_{mode}.csv", keys=keys)
        cnt = Counter(r["node"] for r in rows if r["node"])
        lat = sorted(r["latency_ms"] for r in rows if r["ok"])
        unattributed = sum(1 for r in rows if not r["node"])
        for n, rs in reader.shard_nodes():
            summary.append({"mode": mode, "node": n, "shard": rs, "role": state["nodes"][n]["role"],
                            "reads": cnt.get(n, 0), "pct": round(100 * cnt.get(n, 0) / max(1, len(rows)), 1)})
        by_role = Counter(state["nodes"][n]["role"] for n in cnt.elements())
        log(f"{mode:<18} {len(rows)} reads, errors {sum(1 for r in rows if not r['ok'])}, unattributed "
            f"{unattributed}; primary {by_role.get('PRIMARY', 0)}, secondary {by_role.get('SECONDARY', 0)}; "
            f"latency p50 {lat[len(lat) // 2]:.1f} ms")
        pngs.append(diagram.draw_reads(
            OUT / "img" / f"e05_reads_{mode}.png", f"E5 - readPreference: {mode}",
            f"{len(rows)} find_one reads through mongos (20/s for 20 s), node attributed with the profiler; "
            f"{by_role.get('PRIMARY', 0)} served by primaries, {by_role.get('SECONDARY', 0)} by secondaries",
            state, dict(cnt)))
    diagram.storyboard(pngs, OUT / "img" / "e05_storyboard_reads.png", cols=2)
    return summary


# ---------------------------------------------------------------- part 2
def ensure_load_coll(c) -> None:
    coll = c[DB_NAME].e05_load
    n = coll.estimated_document_count()
    if n == LOAD_DOCS:
        log(f"{DB_NAME}.e05_load already has {n:,} docs (reused)")
        return
    if n:
        raise SystemExit(f"{DB_NAME}.e05_load has {n} docs, expected {LOAD_DOCS}; not touching it")
    log(f"creating {DB_NAME}.e05_load: {LOAD_DOCS:,} docs x ~1 KB (~20 MB per replica)")
    for s in range(0, LOAD_DOCS, 5000):
        coll.insert_many([{"_id": i, "v": 0, "pad": os.urandom(450).hex()} for i in range(s, s + 5000)])


class Load:
    def __init__(self, threads=4, batch=500):
        self.threads, self.batch = threads, batch
        self.stop_ev = threading.Event()
        self.ops = 0
        self.lock = threading.Lock()
        self.ts = []

    def _worker(self):
        cli = pymongo.MongoClient(MONGOS_URI, w=1)
        coll = cli[DB_NAME].e05_load
        while not self.stop_ev.is_set():
            ops = [UpdateOne({"_id": random.randrange(LOAD_DOCS)},
                             {"$set": {"pad": os.urandom(450).hex()}, "$inc": {"v": 1}}) for _ in range(self.batch)]
            try:
                coll.bulk_write(ops, ordered=False)
                with self.lock:
                    self.ops += len(ops)
            except Exception as e:  # noqa: BLE001
                log(f"load error {e!r}")
        cli.close()

    def start(self):
        self.th = [threading.Thread(target=self._worker, daemon=True) for _ in range(self.threads)]
        for t in self.th:
            t.start()

    def stop(self):
        self.stop_ev.set()
        for t in self.th:
            t.join(timeout=30)


_CLIENTS: dict = {}
_PRIMARY: dict = {}


def _direct(n):
    """Persistent direct connections: opening one per probe added ~20-30 ms of connection setup to the
    measured lag in the first E5 run (idle lag 33-55 ms on an idle shard)."""
    if n not in _CLIENTS:
        _CLIENTS[n] = direct_client(n, socketTimeoutMS=2000)
        _CLIENTS[n][DB_NAME].probe.find_one({}, {"_id": 1})  # open the connection now
    return _CLIENTS[n]


def probe_round(c, seq, shard, sign, t0, phase):
    """insert one probe doc into `shard` and time its arrival on both secondaries."""
    if shard not in _PRIMARY:
        _PRIMARY[shard] = primary_of(shard)
    prim = _PRIMARY[shard]
    secs = [n for n in REPLSETS[shard] if n != prim]
    for n in secs:
        _direct(n)
    k = sign * (10**9 + seq)
    ts = time.time()
    c[DB_NAME].probe.insert_one({"k": k, "seq": seq, "t": datetime.now(timezone.utc), "pad": "p"})
    t_ack = time.time()
    seen = {}

    def poll(n):  # one thread per secondary: polling them in turn biased the 2nd one by a round trip
        cl = _direct(n)[DB_NAME].probe
        while time.time() - t_ack < 15:
            if cl.find_one({"k": k}, {"_id": 1}):
                seen[n] = time.time()
                return
            time.sleep(0.001)
    th = [threading.Thread(target=poll, args=(n,)) for n in secs]
    for t in th:
        t.start()
    for t in th:
        t.join()
    rows = []
    for n in secs:
        rows.append({"t_rel_s": round(ts - t0, 2), "phase": phase, "shard": shard, "primary": prim, "secondary": n,
                     "ack_ms": round((t_ack - ts) * 1000, 1),
                     "lag_ms": round((seen[n] - t_ack) * 1000, 1) if n in seen else None})
    return rows


def rs_lag_seconds(shard) -> dict:
    """optime lag from replSetGetStatus (1 s resolution), for comparison with the probe."""
    with direct_client(primary_of(shard)) as c:
        st = c.admin.command("replSetGetStatus")
    p = next(m for m in st["members"] if m["stateStr"] == "PRIMARY")
    return {m["name"].split(":")[0]: (p["optimeDate"] - m["optimeDate"]).total_seconds()
            for m in st["members"] if m["stateStr"] == "SECONDARY"}


def part2(c):
    loaded = c.config.databases.find_one({"_id": DB_NAME})["primary"]
    control = next(s for s in ("shard1RS", "shard2RS") if s != loaded)
    sign = {"shard1RS": -1, "shard2RS": 1}
    ensure_probe(c)
    ensure_load_coll(c)
    print(f"loaded shard: {loaded} (primary shard of db '{DB_NAME}', holds e05_load); control shard: {control}")
    lag_rows, load_rows, rs_rows = [], [], []
    load = Load()
    t0 = time.time()
    seq = 0
    for phase, dur in PHASES:
        log(f"phase {phase} ({dur} s)")
        if phase == "load":
            load.start()
        t_end = time.time() + dur
        last_ops, last_t = load.ops, time.time()
        while time.time() < t_end:
            t_round = time.time()
            for sh in (loaded, control):
                lag_rows += probe_round(c, seq, sh, sign[sh], t0, phase)
                seq += 1
            rl = rs_lag_seconds(loaded)
            rs_rows.append({"t_rel_s": round(t_round - t0, 2), "phase": phase, **{f"optime_lag_s_{k}": v
                                                                                    for k, v in rl.items()}})
            now = time.time()
            load_rows.append({"t_rel_s": round(now - t0, 2), "phase": phase,
                              "updates_per_s": round((load.ops - last_ops) / max(1e-6, now - last_t))})
            last_ops, last_t = load.ops, now
            time.sleep(max(0.0, 1.0 - (time.time() - t_round)))
        if phase == "load":
            load.stop()
            log(f"load stopped: {load.ops:,} updates in {dur} s ({load.ops / dur:,.0f}/s)")
    return loaded, control, lag_rows, load_rows, rs_rows, load.ops / PHASES[1][1]


def pct(vals, p):
    v = sorted(x for x in vals if x is not None)
    return round(v[min(len(v) - 1, int(len(v) * p))], 1) if v else None


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def charts(read_summary, lag_rows, load_rows, loaded, control):
    import matplotlib.pyplot as plt
    plots.setup()
    # reads per node, one panel per mode
    fig, axes = plt.subplots(1, len(MODES), figsize=(14, 4.2), sharey=True)
    for ax, mode in zip(axes, MODES):
        rows = [r for r in read_summary if r["mode"] == mode]
        xs = range(len(rows))
        bars = ax.bar(xs, [r["reads"] for r in rows], color=[plots.color(r["shard"]) for r in rows],
                      edgecolor=[plots.INK if r["role"] == "PRIMARY" else "none" for r in rows], linewidth=1.6)
        ax.set_xticks(list(xs))
        ax.set_xticklabels([f"{r['node']}\n{'P' if r['role'] == 'PRIMARY' else 'S'}" for r in rows], fontsize=8)
        ax.set_title(mode, fontsize=10)
        ax.grid(axis="x", visible=False)
        for b, r in zip(bars, rows):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 3, str(r["reads"]), ha="center", fontsize=7.5,
                    color=plots.INK2)
    axes[0].set_ylabel("reads served")
    fig.suptitle("E5 - reads served per node by read preference (P = primary, S = secondary; colour = shard)",
                 fontweight="bold", fontsize=11)
    plots.save(fig, OUT / "img" / "e05_chart_reads_per_node.png")

    # lag over time
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    i = 0
    for sh in (loaded, control):
        for sec in sorted({r["secondary"] for r in lag_rows if r["shard"] == sh}):
            pts = [(r["t_rel_s"], max(r["lag_ms"], 0.5)) for r in lag_rows
                   if r["secondary"] == sec and r["lag_ms"] is not None]
            ax_ls = "-" if sh == loaded else ":"
            a1.plot(*zip(*pts), color=plots.color(sh), ls=ax_ls, lw=1.6 if sec.endswith(("a", "c")) else 1.2,
                    alpha=0.95 if i % 2 == 0 else 0.6, label=f"{sec} ({sh}{', loaded' if sh == loaded else ', control'})")
            i += 1
    a1.set_yscale("log")
    a1.set_ylabel("time until visible on secondary (ms, log)")
    a1.legend(loc="upper left", ncol=2, fontsize=8)
    a2.plot([r["t_rel_s"] for r in load_rows], [r["updates_per_s"] for r in load_rows], color=plots.INK2)
    a2.set_ylabel("load: updates / s")
    a2.set_xlabel("seconds since start")
    t_load = [r["t_rel_s"] for r in lag_rows if r["phase"] == "load"]
    for ax in (a1, a2):
        if t_load:
            ax.axvspan(min(t_load), max(t_load), color="#fab219", alpha=0.12, lw=0)
    a1.text(min(t_load) if t_load else 0, a1.get_ylim()[1], " write load on " + loaded, va="top", fontsize=8.5,
            color=plots.INK2)
    a1.set_title("E5 - replication lag measured with probe documents (1 probe per shard per second)", loc="left")
    plots.save(fig, OUT / "img" / "e05_chart_lag.png")


def main() -> None:
    keys = reader.sample_keys(300)
    section("E5 part 1: read preference routing (20 reads/s x 20 s per mode, through mongos)")
    summary = part1(keys)
    write_csv(OUT / "reads_summary.csv", summary)
    piv = defaultdict(dict)
    for r in summary:
        piv[r["node"]].update(node=r["node"], shard=r["shard"], role=r["role"], **{r["mode"]: r["reads"]})
    print()
    print_table(list(piv.values()), ["node", "shard", "role"] + MODES)

    section("E5 part 2: replication lag, idle -> write load -> recovery")
    with mongos_client() as c:
        loaded, control, lag_rows, load_rows, rs_rows, ups = part2(c)
    write_csv(OUT / "lag.csv", lag_rows)
    write_csv(OUT / "load.csv", load_rows)
    write_csv(OUT / "optime_lag.csv", rs_rows)
    lsum = []
    for sh in (loaded, control):
        for sec in sorted({r["secondary"] for r in lag_rows if r["shard"] == sh}):
            for phase, _ in PHASES:
                v = [r["lag_ms"] for r in lag_rows if r["secondary"] == sec and r["phase"] == phase]
                ack = [r["ack_ms"] for r in lag_rows if r["secondary"] == sec and r["phase"] == phase]
                lsum.append({"shard": sh, "role": "loaded" if sh == loaded else "control", "secondary": sec,
                             "phase": phase, "probes": len(v), "not_seen_15s": sum(1 for x in v if x is None),
                             "lag_p50_ms": pct(v, 0.5), "lag_p95_ms": pct(v, 0.95), "lag_max_ms": pct(v, 1.0),
                             "write_ack_p50_ms": pct(ack, 0.5)})
    print()
    print_table(lsum)
    opt = [max(v for k, v in r.items() if k.startswith("optime_lag_s_")) for r in rs_rows if r["phase"] == "load"]
    print(f"\nwrite load: {ups:,.0f} updates/s (w:1, 4 threads x 500-doc batches) on {loaded}")
    idle_opt = [max(v for k, v in r.items() if k.startswith("optime_lag_s_")) for r in rs_rows if r["phase"] == "idle"]
    print(f"replSetGetStatus optime lag (1 s resolution): idle max {max(idle_opt) if idle_opt else 0:.0f} s, "
          f"load max {max(opt) if opt else 0:.0f} s  <- compare with the probe-measured lag above;\n"
          "  when idle the primary only writes a no-op every 10 s, so optimeDate differences are not real lag")
    write_csv(OUT / "lag_summary.csv", lsum)
    save_json(OUT / "e05_summary.json", {"reads": summary, "lag": lsum, "loaded": loaded, "control": control,
                                         "updates_per_s": ups})
    charts(summary, lag_rows, load_rows, loaded, control)
    caps = {}
    for r in lsum:
        if r["phase"] == "load":
            caps[r["secondary"]] = f"lag under load p95 {r['lag_p95_ms']} ms"
    state = diagram.snapshot(inside_fallback=False)
    diagram.draw_reads(OUT / "img" / "e05_lag_diagram.png", "E5 - replication lag under write load (observed)",
                       f"{ups:,.0f} updates/s (w:1) on {loaded}; {control} idle (control). "
                       "Caption = 95th-percentile time until a write is visible on that secondary.",
                       state, {}, captions=caps)
    log(f"E5 done -> {OUT}")


if __name__ == "__main__":
    with Tee(OUT / "console.txt"):
        main()
    export.export("e05")
