"""E3 - Query routing: the same query with and without the shard key.

Group A: the SAME filter {region: "great_lakes"} on all four collections: targeted only where
         region is (a prefix of) the shard key.
Group B: the same collection queried with its shard key vs without it (and, for the hashed key,
         equality vs range on the key itself).

For each query: explain("executionStats") through mongos -> plan stage (SINGLE_SHARD / SHARD_MERGE),
shards contacted, per-shard scan stage, keys/docs examined, server time; plus the client-side median
of 7 timed executions (projection {_id: 1} so network transfer does not dominate).

Output results/e03/: queries.csv, explain/<id>.json, img/e03_<id>.png (routing diagrams),
img/e03_storyboard_A.png / _B.png, img/e03_chart_docs_examined.png
"""
import csv
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import diagram, export, plots  # noqa: E402
from lab.cluster import print_table  # noqa: E402
from lab.common import DB_NAME, Tee, log, mongos_client, results_dir, save_json, section  # noqa: E402
from lab.datagen import START_TS  # noqa: E402
from lab.load import SHARD_KEYS  # noqa: E402

OUT = results_dir("e03")
PROJ = {"_id": 1}
SCANS = ("COLLSCAN", "IXSCAN", "IDHACK", "EXPRESS_IXSCAN", "EXPRESS_CLUSTERED_IXSCAN", "CLUSTERED_IXSCAN",
         "COUNT_SCAN", "DISTINCT_SCAN")


def find_stages(node) -> list[str]:
    out = []
    if isinstance(node, dict):
        st = node.get("stage")
        if isinstance(st, str):
            out.append(st)
        for v in node.values():
            out += find_stages(v)
    elif isinstance(node, list):
        for v in node:
            out += find_stages(v)
    return out


def queries(c) -> list[dict]:
    db = c[DB_NAME]
    one = db.ship_hashed.find_one({"region": "great_lakes"}, {"_id": 1})["_id"]
    ids = [d["_id"] for d in db.ship_hashed.find({}, {"_id": 1}).sort("_id", 1).skip(100000).limit(2000)]
    chart = db.ship_mono.find_one({"region": "great_lakes"}, {"chart": 1})["chart"]
    t0, t1 = START_TS, START_TS + timedelta(hours=1)
    gl = {"region": "great_lakes"}
    return [
        {"id": "A1", "group": "A", "coll": "ship_compound", "filter": gl, "why": "region = prefix of shard key"},
        {"id": "A2", "group": "A", "coll": "ship_mono", "filter": gl, "why": "region not in shard key {ts}"},
        {"id": "A3", "group": "A", "coll": "ship_hashed", "filter": gl, "why": "region not in shard key {_id hashed}"},
        {"id": "A4", "group": "A", "coll": "ship_lowcard", "filter": gl, "why": "region not in shard key {feature_type}"},
        {"id": "B1", "group": "B", "coll": "ship_compound", "filter": {"region": "gulf_of_mexico", "recrd": "19950101"},
         "why": "full shard key (equality)"},
        {"id": "B2", "group": "B", "coll": "ship_compound", "filter": {"region": "gulf_of_mexico"},
         "why": "key prefix whose range spans 2 shards"},
        {"id": "B3", "group": "B", "coll": "ship_compound",
         "filter": {"feature_type": "Wrecks - Submerged, dangerous", "depth": {"$gt": 20}}, "why": "no shard key"},
        {"id": "B4", "group": "B", "coll": "ship_mono", "filter": {"ts": {"$gte": t0, "$lt": t1}},
         "why": "range on shard key (first hour)"},
        {"id": "B5", "group": "B", "coll": "ship_mono", "filter": {"chart": chart}, "why": "no shard key"},
        {"id": "B6", "group": "B", "coll": "ship_hashed", "filter": {"_id": one}, "why": "equality on hashed key"},
        {"id": "B7", "group": "B", "coll": "ship_hashed", "filter": {"_id": {"$gte": ids[0], "$lte": ids[-1]}},
         "why": "RANGE on hashed key (hash destroys order)"},
        {"id": "B8", "group": "B", "coll": "ship_lowcard", "filter": {"feature_type": "Wrecks - Visible"},
         "why": "equality on shard key"},
        {"id": "B9", "group": "B", "coll": "ship_lowcard", "filter": {"watlev": "awash"}, "why": "no shard key"},
    ]


def fmt_filter(f: dict) -> str:
    def v(x):
        if hasattr(x, "isoformat"):
            return f'ISODate("{x.strftime("%Y-%m-%dT%H:%M")}")'
        if type(x).__name__ == "ObjectId":
            return f'ObjectId("…{str(x)[-6:]}")'
        if isinstance(x, dict):
            return "{" + ", ".join(f"{k}: {v(y)}" for k, y in x.items()) + "}"
        return repr(x).replace("'", '"')
    return "{" + ", ".join(f"{k}: {v(x)}" for k, x in f.items()) + "}"


def run(c, q: dict) -> dict:
    db = c[DB_NAME]
    coll = db[q["coll"]]
    list(coll.find(q["filter"], PROJ))  # warm-up BEFORE explain, so explain times are not cold-cache times
    ex = db.command("explain", {"find": q["coll"], "filter": q["filter"], "projection": PROJ},
                    verbosity="executionStats")
    save_json(OUT / "explain" / f"{q['id']}.json", ex)
    plan = ex["queryPlanner"]["winningPlan"]["stage"]
    es = ex["executionStats"]
    per = {}
    for s in es["executionStages"].get("shards", []):
        stages = find_stages(s.get("executionStages", {}))
        scan = next((x for x in stages if x in SCANS), stages[-1] if stages else "?")
        per[s["shardName"]] = {"scan": scan, "keys": s.get("totalKeysExamined", 0),
                               "docs": s.get("totalDocsExamined", 0), "returned": s.get("nReturned", 0),
                               "ms": s.get("executionTimeMillis", 0)}
    times = []
    for _ in range(7):
        t = time.perf_counter()
        n = len(list(coll.find(q["filter"], PROJ)))
        times.append((time.perf_counter() - t) * 1000)
    return {"id": q["id"], "group": q["group"], "collection": q["coll"], "shard_key": str(SHARD_KEYS[q["coll"]]),
            "filter": fmt_filter(q["filter"]), "why": q["why"], "plan_stage": plan,
            "shards_contacted": "+".join(sorted(per)), "n_shards": len(per),
            "scan": "/".join(sorted({p["scan"] for p in per.values()})),
            "keys_examined": es.get("totalKeysExamined", 0), "docs_examined": es.get("totalDocsExamined", 0),
            "returned": es.get("nReturned", 0), "server_ms": es.get("executionTimeMillis", 0),
            "client_median_ms": round(statistics.median(times), 1), "client_n": n, "_per": per}


def chart(rows) -> None:
    import matplotlib.pyplot as plt
    plots.setup()
    fig, ax = plt.subplots(figsize=(11, 5))
    xs = range(len(rows))
    w = 0.38
    ax.bar([x - w / 2 for x in xs], [r["docs_examined"] or float("nan") for r in rows], w * 0.92, color="#2a78d6",
           label="documents examined (all shards)")
    ax.bar([x + w / 2 for x in xs], [r["returned"] or float("nan") for r in rows], w * 0.92, color="#eb6834",
           label="documents returned")
    ax.set_yscale("log")
    ax.set_ylim(0.5, 2e6)
    for x, r in zip(xs, rows):
        if r["docs_examined"] == 0:
            ax.text(x - w / 2, 0.7, "0\n(index\nonly)", ha="center", va="bottom", fontsize=7, color=plots.INK2)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{r['id']}\n{r['collection'].replace('ship_', '')}\n{r['n_shards']} shard"
                        + ("s" if r["n_shards"] > 1 else "") for r in rows], fontsize=8)
    ax.set_ylabel("documents (log scale)")
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.02), ncol=2)
    ax.grid(axis="x", visible=False)
    ax.set_title("E3 - work done per query: examined vs returned", loc="left", pad=28)
    plots.save(fig, OUT / "img" / "e03_chart_docs_examined.png")


def main() -> None:
    rows = []
    with mongos_client() as c:
        shards = sorted(s["_id"] for s in c.admin.command("listShards")["shards"])
        qs = queries(c)
        section("E3 queries")
        for q in qs:
            print(f"{q['id']}  {q['coll']:<14} {fmt_filter(q['filter'])}   -- {q['why']}")
        for q in qs:
            r = run(c, q)
            rows.append(r)
            log(f"{r['id']}: {r['plan_stage']} shards={r['shards_contacted']} scan={r['scan']} "
                f"docs={r['docs_examined']:,} returned={r['returned']:,} client={r['client_median_ms']} ms")
            diagram.draw_routing(OUT / "img" / f"e03_{r['id']}.png", f"E3 {r['id']} - {r['why']}",
                                 fmt_filter(q["filter"]) + ", {_id: 1}", r["collection"], r["shard_key"],
                                 r["plan_stage"], shards, r["_per"],
                                 {"returned": r["returned"], "docs": r["docs_examined"], "keys": r["keys_examined"],
                                  "server_ms": r["server_ms"], "client_ms": r["client_median_ms"]})
    cols = ["id", "collection", "why", "plan_stage", "shards_contacted", "scan", "keys_examined", "docs_examined",
            "returned", "server_ms", "client_median_ms"]
    section("E3 group A: same filter {region: 'great_lakes'} on every collection")
    print_table([r for r in rows if r["group"] == "A"], cols)
    section("E3 group B: with vs without the shard key")
    print_table([r for r in rows if r["group"] == "B"], cols)
    with open(OUT / "queries.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in rows[0] if k != "_per"])
        w.writeheader()
        w.writerows([{k: v for k, v in r.items() if k != "_per"} for r in rows])
    save_json(OUT / "queries.json", rows)
    chart(rows)
    for g in "AB":
        diagram.storyboard([OUT / "img" / f"e03_{r['id']}.png" for r in rows if r["group"] == g],
                           OUT / "img" / f"e03_storyboard_{g}.png", cols=2)
    log(f"E3 done -> {OUT}")


if __name__ == "__main__":
    with Tee(OUT / "console.txt"):
        main()
    export.export("e03")
