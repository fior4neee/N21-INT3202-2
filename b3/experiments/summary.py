"""Build one comparison table from results/ (expected behaviour vs what was measured), for report chapter 4.

    python experiments\\summary.py

Reads only files under results/ (no cluster access). Output results/summary/: summary.csv, summary.json,
console.txt, img/*.png, summary_report.pdf (one page per table).
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import export  # noqa: E402
from lab.cluster import print_table  # noqa: E402
from lab.common import RESULTS, Tee, results_dir, save_json, section  # noqa: E402

OUT = results_dir("summary")


def load(rel):
    p = RESULTS / rel
    if not p.exists():
        return None
    if p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def f1(x, unit=""):
    return "-" if x in (None, "") else f"{float(x):.1f}{unit}"


def main() -> None:
    rows = []

    def add(exp, case, expected, observed, metric=""):
        rows.append({"exp": exp, "case": case, "expected (theory)": expected, "observed": observed, "key numbers": metric})

    # E1
    nodes = load("e01/nodes.csv")
    if nodes:
        prim = [r["node"] for r in nodes if r["role"] == "PRIMARY"]
        add("E1", "cluster survey", "3 cfg + 2x3 shard members + mongos; 1 primary per replica set",
            f"{len(nodes)} mongod + 1 mongos, primaries {', '.join(prim)}; MongoDB {nodes[0]['version']}",
            f"WT cache {nodes[0]['wt_cache_GB']} GB per node")
    # E2
    e2 = load("e02/summary.json")
    if e2:
        for coll, exp in (("ship_mono", "hot shard: all inserts on 1 shard"), ("ship_lowcard", "jumbo chunks"),
                          ("ship_hashed", "even spread without the balancer"), ("ship_compound", "balanced, targeted")):
            on = [r for r in e2 if r["phase"] == "on" and r["coll"] == coll]
            off = [r for r in e2 if r["phase"] == "off" and r["coll"] == coll]
            if not on:
                continue
            mb = " / ".join(f"{r['owned_MB']:.0f}" for r in on)
            offmb = " / ".join(f"{r['owned_MB']:.0f}" for r in off)
            hot = max(on, key=lambda r: r.get("client_docs_pct", 0))
            add("E2", coll, exp,
                f"balancer on: {mb} MB per shard (off: {offmb}); jumbo {sum(r['jumbo'] for r in on)}; "
                f"{hot.get('client_docs_pct', '?')}% of new docs to {hot['shard']}",
                f"{on[0]['migrations']} migrations, gap {on[0]['imbalance_MB']} MB (threshold 24 MB)")
    # E3
    e3 = load("e03/queries.json")
    if e3:
        for r in e3:
            if r["id"] in ("A1", "A2", "B1", "B6", "B7"):
                add("E3", f"{r['id']} {r['collection']} {r['why']}",
                    "targeted if the filter has the shard key, else scatter-gather",
                    f"{r['plan_stage']}, {r['n_shards']} shard(s), {r['scan']}",
                    f"docs examined {r['docs_examined']:,} -> returned {r['returned']:,}")
    # E4
    e4 = load("e04/queries.json")
    if e4:
        g = {r["id"]: r for r in e4["rows"]}
        add("E4", "2dsphere index", "COLLSCAN before, IXSCAN after; $near needs the index",
            f"G1 {g['G1']['scan']} {g['G1']['docs']:,} docs -> G3 {g['G3']['scan']} {g['G3']['docs']:,} docs; "
            f"$near without index: error",
            f"{f1(g['G1']['client_ms'])} ms -> {f1(g['G3']['client_ms'])} ms; index "
            f"{min(e4['index_MB'].values())}-{max(e4['index_MB'].values())} MB per shard")
        add("E4", "$near / $geoNear routing", "scatter-gather unless the shard key is in the query",
            f"no key: {g['G5']['n_shards']} shards; with region: {g['G6']['n_shards']} shard",
            "geo index cannot be a shard key: shardCollection error 2")
    # E5
    e5 = load("e05/e05_summary.json")
    if e5:
        for mode in ("primary", "secondary", "secondaryPreferred", "nearest"):
            rs = [r for r in e5["reads"] if r["mode"] == mode]
            tot = sum(r["reads"] for r in rs) or 1
            p = sum(r["reads"] for r in rs if r["role"] == "PRIMARY")
            add("E5", f"readPreference {mode}", {"primary": "all reads on primaries",
                                                 "secondary": "all reads on secondaries",
                                                 "secondaryPreferred": "secondaries while available",
                                                 "nearest": "any member within the latency window"}[mode],
                f"{100 * p / tot:.0f}% primaries / {100 - 100 * p / tot:.0f}% secondaries",
                f"{tot} reads, {sum(1 for r in rs if r['reads'])} nodes served")
        lag = e5["lag"]
        ld = [r for r in lag if r["role"] == "loaded" and r["phase"] == "load"]
        ct = [r for r in lag if r["role"] == "control" and r["phase"] == "load"]
        add("E5", "replication lag under write load", "lag grows with write load on that shard only",
            f"loaded shard p50 {max(r['lag_p50_ms'] for r in ld)} ms, p95 {max(r['lag_p95_ms'] for r in ld)} ms, "
            f"max {max(r['lag_max_ms'] for r in ld)} ms; control p95 {max(r['lag_p95_ms'] for r in ct)} ms",
            f"{e5['updates_per_s']:,.0f} updates/s")
    # E6
    e6 = load("e06/summary.json")
    if e6:
        exp = {"kill": "~election timeout (10 s)", "stop": "fastest: clean step-down", "pause": "~election timeout (10 s)"}
        for r in e6:
            add("E6", f"docker {r['method']} of the shard1 primary", exp[r["method"]],
                f"election won {f1(r['log_election_won_s'], ' s')}, catch-up {f1(r['log_catchup_s'], ' s')}, "
                f"writable {f1(r['log_writable_s'], ' s')}, writes ok {f1(r['writes_resumed_s'], ' s')}",
                f"{r['failed_writes']} failed writes; shard2 {r['shard2_failed']} failed")
    # E7
    e7 = load("e07/summary.json")
    if e7:
        for r in e7:
            failed = [t["operation"] for t in r["table"] if not t["degraded_ok"].startswith(t["degraded_ok"].split("/")[1])]
            add("E7", f"2 of 3 {r['replset']} members down",
                "no majority -> no primary; shard read-only" if r["phase"] == "A"
                else "config read-only: data ops continue, metadata ops fail",
                f"{r['survivor']} stepped down at +{f1(r['stepdown_s'], ' s')} (incl. ~11 s docker stop); "
                f"failing: {', '.join(failed) or 'none'}",
                f"primary again {f1(r['primary_again_after_restore_s'], ' s')} after restart")
    # E8
    e8 = load("e08/summary.json")
    if e8:
        add("E8", "partition of the shard1 primary",
            "isolated primary keeps accepting w:1 writes until it steps down; they are rolled back; "
            "brief window with two primaries",
            f"{e8['w1_acked']} w:1 acked while isolated -> {e8['w1_acked_lost']} lost (rollback file: "
            f"{e8['rollback_docs']} docs); w:majority acked {e8['wmaj_acked']}, failed {e8['wmaj_failed']}; "
            f"two primaries: {'%s s' % e8['two_primaries_s'] if e8['two_primaries_s'] else 'not observed'}",
            f"old primary stepped down +{e8['isolated_stepdown_s']} s, new elected +{e8['elected_s']} s")
    # E9
    e9 = load("e09/summary.json")
    if e9:
        w = {r["phase"]: r for r in e9["writer"]}
        add("E9", "add a third shard", "balancer moves data until within 3 x chunk size; writes continue",
            f"{e9['migrations']} migrations, {e9['MB_moved']} MB moved, balanced {f1(e9['balanced_after_add_s'], ' s')} "
            f"after addShard; 0 failed writes" if all(r["failed"] == 0 for r in e9["writer"]) else "writes failed",
            f"insert p95 {w['before addShard']['p95_ms']} -> {w['during migrations']['p95_ms']} ms during migration")

    section("Summary: expected behaviour vs observation (all experiments)")
    print_table(rows, ["exp", "case", "observed", "key numbers"])
    section("Summary: what theory predicted for each case")
    print_table(rows, ["exp", "case", "expected (theory)"])
    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    save_json(OUT / "summary.json", rows)


if __name__ == "__main__":
    with Tee(OUT / "console.txt"):
        main()
    export.MAX_CSV_ROWS = 0  # the wide CSV is already rendered via console sections
    export.export("summary")
