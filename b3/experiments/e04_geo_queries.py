"""E4 - Indexes and geo queries on a sharded collection (lab.ship_compound, key {region, recrd}).

1. A geospatial index cannot be a shard key: shardCollection with {location: "2dsphere"} -> error.
2. BEFORE a 2dsphere index: $geoWithin works but is a COLLSCAN on every shard; $near fails.
3. createIndex({location: "2dsphere"}) through mongos (built on every shard), timed.
4. AFTER: $geoWithin -> IXSCAN; $near and $geoNear work through mongos; without the shard key they
   are scatter-gather, with region (prefix of the shard key) added they are targeted.

Search point: Lake Erie off Cleveland (-81.7, 41.6), radius 30 km (great_lakes region).
Re-runs: the 2dsphere index created by a previous E4 run is dropped first so "before" is real.

Output results/e04/: queries.csv, explain/*.json, img/e04_<id>.png (routing), img/e04_map.png,
img/e04_storyboard.png
"""
import csv
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson.max_key import MaxKey  # noqa: E402
from bson.min_key import MinKey  # noqa: E402

from lab import diagram, export, plots  # noqa: E402
from lab.cluster import print_table  # noqa: E402
from lab.common import DB_NAME, Tee, log, mongos_client, results_dir, save_json, section  # noqa: E402
from lab.explain import fmt_filter, summarize_agg, summarize_find  # noqa: E402

OUT = results_dir("e04")
COLL = "ship_compound"
KEY = "{'region': 1, 'recrd': 1}"
CENTER = [-81.7, 41.6]
RADIUS_M = 30_000
EARTH_M = 6_378_100
IDX = "location_2dsphere"
PT = {"type": "Point", "coordinates": CENTER}


def timed(fn, n=5):
    fn()
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        r = fn()
        ts.append((time.perf_counter() - t) * 1000)
    return r, round(statistics.median(ts), 1)


def run_find(c, qid, why, flt, shards, limit=0):
    db = c[DB_NAME]
    coll = db[COLL]
    cmd = {"find": COLL, "filter": flt, "projection": {"_id": 1}}
    if limit:
        cmd["limit"] = limit
    try:
        res, ms = timed(lambda: list(coll.find(flt, {"_id": 1}, limit=limit)))
    except Exception as e:  # e.g. $near without a geo index
        msg = (getattr(e, "details", None) or {}).get("errmsg") or str(e).split(", full error")[0]
        if "unable to find index" in msg:
            msg = msg[msg.index("unable to find index"):].split("\n")[0]
        log(f"{qid}: ERROR {type(e).__name__} code={getattr(e, 'code', '')}: {msg[:160]}")
        return {"id": qid, "why": why, "query": f"find({fmt_filter(flt)})", "plan": "ERROR",
                "shards_contacted": "", "n_shards": 0, "scan": "", "keys": "", "docs": "", "returned": "",
                "server_ms": "", "client_ms": "", "error": f"{type(e).__name__} {getattr(e, 'code', '')}: {msg[:200]}"}
    ex = db.command("explain", cmd, verbosity="executionStats")
    save_json(OUT / "explain" / f"{qid}.json", ex)
    s = summarize_find(ex)
    return finish(qid, why, f"find({fmt_filter(flt)})" + (f".limit({limit})" if limit else ""), s, ms, shards)


def run_agg(c, qid, why, pipeline, shards):
    db = c[DB_NAME]
    res, ms = timed(lambda: list(db[COLL].aggregate(pipeline)))
    ex = db.command("explain", {"aggregate": COLL, "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
    save_json(OUT / "explain" / f"{qid}.json", ex)
    s = summarize_agg(ex)
    # per-shard nReturned of the $geoNear cursor counts what each shard produced before mongos merged
    # and applied $limit; report what the CLIENT received instead
    s["returned"] = len(res)
    return finish(qid, why, f"aggregate({fmt_filter(pipeline)})", s, ms, shards)


def finish(qid, why, qtext, s, ms, shards):
    r = {"id": qid, "why": why, "query": qtext, "plan": s["plan"],
         "shards_contacted": "+".join(sorted(s["per_shard"])), "n_shards": len(s["per_shard"]),
         "scan": "/".join(sorted({p["scan"] for p in s["per_shard"].values()})),
         "keys": s["keys"], "docs": s["docs"], "returned": s["returned"], "server_ms": s["server_ms"],
         "client_ms": ms, "error": ""}
    log(f"{qid}: {r['plan']} shards={r['shards_contacted']} scan={r['scan']} keys={r['keys']:,} "
        f"docs={r['docs']:,} returned={r['returned']:,} client={ms} ms")
    diagram.draw_routing(OUT / "img" / f"e04_{qid}.png", f"E4 {qid} - {why}", qtext.replace(COLL + ".", ""),
                         COLL, KEY, r["plan"], shards, s["per_shard"],
                         {"returned": r["returned"], "docs": r["docs"], "keys": r["keys"],
                          "server_ms": r["server_ms"], "client_ms": ms})
    return r


# ---------------------------------------------------------------- map
def _k(v):
    if isinstance(v, MinKey):
        return (0, "")
    if isinstance(v, MaxKey):
        return (2, "")
    return (1, v)


def owner_lookup(c):
    meta = c.config.collections.find_one({"_id": f"{DB_NAME}.{COLL}"})
    chunks = sorted(c.config.chunks.find({"uuid": meta["uuid"]}),
                    key=lambda ch: (_k(ch["min"]["region"]), _k(ch["min"]["recrd"])))
    mins = [((_k(ch["min"]["region"]), _k(ch["min"]["recrd"])), ch["shard"]) for ch in chunks]

    def owner(region, recrd):
        k = (_k(region), _k(recrd))
        sh = mins[0][1]
        for m, s in mins:
            if m <= k:
                sh = s
            else:
                break
        return sh
    return owner


def draw_map(c, near_ids: set, rows_by_id: dict) -> None:
    import matplotlib.pyplot as plt
    plots.setup()
    owner = owner_lookup(c)
    db = c[DB_NAME]
    sample = list(db[COLL].aggregate([{"$sample": {"size": 25000}},
                                      {"$project": {"coordinates": 1, "region": 1, "recrd": 1}}]))
    dlat = RADIUS_M / 111_195
    dlon = dlat / math.cos(math.radians(CENTER[1]))
    box = {"location": {"$geoWithin": {"$box": [[CENTER[0] - 4 * dlon, CENTER[1] - 4 * dlat],
                                                [CENTER[0] + 4 * dlon, CENTER[1] + 4 * dlat]]}}}
    local = list(db[COLL].find(box, {"coordinates": 1, "region": 1, "recrd": 1}))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 6.2), gridspec_kw={"width_ratios": [1.5, 1]})
    for ax, docs, s in ((a1, sample, 4), (a2, local, 9)):
        for sh in sorted({owner(d["region"], d["recrd"]) for d in docs}):
            pts = [d["coordinates"] for d in docs if owner(d["region"], d["recrd"]) == sh]
            ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=s, color=plots.color(sh), alpha=0.55,
                       linewidths=0, label=f"{sh} ({len(pts):,} shown)")
    a1.set_xlim(-180, -55)
    a1.set_ylim(5, 72)
    a1.set_title("25,000-doc sample of ship_compound, coloured by the shard that owns it", loc="left", fontsize=10.5)
    a1.add_patch(plt.Rectangle((CENTER[0] - 4 * dlon, CENTER[1] - 4 * dlat), 8 * dlon, 8 * dlat, fill=False,
                               ec=plots.INK, lw=1.4))
    a1.annotate("zoom →", (CENTER[0] + 4 * dlon, CENTER[1]), xytext=(CENTER[0] + 12, CENTER[1] + 6),
                fontsize=9, arrowprops=dict(arrowstyle="-|>", color=plots.INK))
    a1.legend(loc="lower left", markerscale=3)
    a1.set_xlabel("longitude")
    a1.set_ylabel("latitude")
    # zoom: query circle + $near results
    th = [i * 2 * math.pi / 200 for i in range(201)]
    a2.plot([CENTER[0] + dlon * math.cos(t) for t in th], [CENTER[1] + dlat * math.sin(t) for t in th],
            color=plots.INK, lw=1.6, ls="--", label=f"{RADIUS_M // 1000} km search radius")
    hits = [d["coordinates"] for d in local if d["_id"] in near_ids]
    a2.scatter([p[0] for p in hits], [p[1] for p in hits], s=16, facecolors="none", edgecolors=plots.INK,
               linewidths=0.5, label=f"$near result ({len(hits)} docs)")
    a2.scatter([CENTER[0]], [CENTER[1]], marker="*", s=160, color="#e34948", zorder=5, label="search point")
    a2.set_xlim(CENTER[0] - 4 * dlon, CENTER[0] + 4 * dlon)
    a2.set_ylim(CENTER[1] - 4 * dlat, CENTER[1] + 4 * dlat)
    a2.set_title("zoom: Lake Erie off Cleveland", loc="left", fontsize=10.5)
    a2.set_xlabel("longitude")
    a2.legend(loc="lower left", fontsize=8, markerscale=1)
    note = []
    for qid in ("G3", "G4"):
        r = rows_by_id.get(qid)
        if r:
            note.append(f"{qid}: {r['why']} → contacted {r['shards_contacted'] or '-'}")
    fig.suptitle("E4 - where the geo data lives and which shards a $near query must ask", fontweight="bold",
                 x=0.01, ha="left")
    fig.text(0.01, 0.005, "   ".join(note).replace("$", r"\$"), fontsize=8.5, color=plots.INK2)
    plots.save(fig, OUT / "img" / "e04_map.png")


def main(after_only: bool = False) -> None:
    rows = []
    with mongos_client() as c:
        db = c[DB_NAME]
        coll = db[COLL]
        shards = sorted(s["_id"] for s in c.admin.command("listShards")["shards"])
        within = {"location": {"$geoWithin": {"$centerSphere": [CENTER, RADIUS_M / EARTH_M]}}}
        near = {"location": {"$near": {"$geometry": PT, "$maxDistance": RADIUS_M}}}
        if after_only:
            # keep the measured BEFORE results (G1, G2) and index build of the previous full run; nothing dropped
            import json
            prev = json.loads((OUT / "queries.json").read_text(encoding="utf-8"))
            con = OUT / "console.txt"
            text = con.read_text(encoding="utf-8")
            cut = text.find("\n" + "=" * 78 + "\nE4 step 4")
            sys.stdout.write("")
            con.write_text(text[:cut], encoding="utf-8")
            rows = [r for r in prev["rows"] if r["id"] in ("G1", "G2")]
            for r in rows:
                if r["error"] and "unable to find index" not in r["error"]:
                    try:  # full error text: same $near on ship_mono (same data, also no geo index)
                        list(c[DB_NAME].ship_mono.find(near, limit=1))
                    except Exception as e:
                        m = (getattr(e, "details", None) or {}).get("errmsg", "")
                        if "unable to find index" in m:
                            r["error"] = f"OperationFailure {getattr(e, 'code', '')}: " + \
                                m[m.index("unable to find index"):].split("\n")[0] + \
                                " (full text re-read from the same query on ship_mono, also without a geo index)"
            build_s, sizes = prev["index_build_s"], prev["index_MB"]
            return after(c, coll, shards, within, near, rows, build_s, sizes)

        section("E4 step 1: can a 2dsphere index be the shard key?")
        try:
            c.admin.command("shardCollection", f"{DB_NAME}.e04_geokey_test", key={"location": "2dsphere"})
            print("UNEXPECTED: shardCollection with a 2dsphere key succeeded")
        except Exception as e:
            print(f"shardCollection({DB_NAME}.e04_geokey_test, key={{location: '2dsphere'}})\n"
                  f"  -> {type(e).__name__} code {getattr(e, 'code', '')}: {str(e).split(', full error')[0]}")
        print(f"collection created by the failed command: {'e04_geokey_test' in db.list_collection_names()}")

        section("E4 step 2: BEFORE a 2dsphere index")
        if IDX in coll.index_information():
            print(f"dropping {IDX} left by a previous E4 run so that 'before' really has no geo index")
            coll.drop_index(IDX)
        print("indexes:", [list(i["key"].items()) for i in coll.list_indexes()])
        rows.append(run_find(c, "G1", "geoWithin, no geo index", within, shards))
        rows.append(run_find(c, "G2", "$near, no geo index", near, shards))

        section("E4 step 3: createIndex({location: '2dsphere'}) through mongos")
        t = time.time()
        coll.create_index([("location", "2dsphere")], name=IDX)
        build_s = time.time() - t
        sizes = {s["shard"]: round(s["storageStats"]["indexSizes"].get(IDX, 0) / 2**20, 1)
                 for s in coll.aggregate([{"$collStats": {"storageStats": {}}}])}
        print(f"built in {build_s:.1f} s; index size per shard (MB): {sizes}")
        after(c, coll, shards, within, near, rows, build_s, sizes)


def after(c, coll, shards, within, near, rows, build_s, sizes) -> None:
    if True:
        section("E4 step 4: AFTER the 2dsphere index")
        rows.append(run_find(c, "G3", "geoWithin, 2dsphere index, no shard key", within, shards))
        rows.append(run_find(c, "G4", "geoWithin + region (shard-key prefix)", {**within, "region": "great_lakes"},
                             shards))
        rows.append(run_find(c, "G5", "$near, no shard key", near, shards))
        rows.append(run_find(c, "G6", "$near + region (shard-key prefix)", {**near, "region": "great_lakes"}, shards))
        gn = {"near": PT, "distanceField": "dist_m", "maxDistance": RADIUS_M, "key": "location"}
        rows.append(run_agg(c, "G7", "$geoNear aggregation, no shard key",
                            [{"$geoNear": gn}, {"$limit": 50}, {"$project": {"_id": 1, "dist_m": 1}}], shards))
        rows.append(run_agg(c, "G8", "$geoNear + region in query",
                            [{"$geoNear": {**gn, "query": {"region": "great_lakes"}}}, {"$limit": 50},
                             {"$project": {"_id": 1, "dist_m": 1}}], shards))
        near_ids = {d["_id"] for d in coll.find(near, {"_id": 1})}
        nearest = list(coll.aggregate([{"$geoNear": gn}, {"$limit": 5},
                                       {"$project": {"_id": 0, "dist_m": 1, "feature_type": 1, "depth": 1,
                                                     "coordinates": 1}}]))

        cols = ["id", "why", "plan", "shards_contacted", "scan", "keys", "docs", "returned", "server_ms", "client_ms"]
        section("E4 results")
        print_table(rows, cols)
        for r in rows:
            if r["error"]:
                print(f"{r['id']} error: {r['error']}")
        print("\n5 nearest wrecks to the search point ($geoNear, distance in metres):")
        for d in nearest:
            print(f"  {d['dist_m']:>8.0f} m  {d['coordinates']}  {d['feature_type']}  depth={d['depth']}")
        with open(OUT / "queries.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        save_json(OUT / "queries.json", {"rows": rows, "index_build_s": round(build_s, 1), "index_MB": sizes,
                                         "center": CENTER, "radius_m": RADIUS_M})
        draw_map(c, near_ids, {r["id"]: r for r in rows})
    if True:
        diagram.storyboard([OUT / "img" / f"e04_{r['id']}.png" for r in rows if not r["error"]],
                           OUT / "img" / "e04_storyboard.png", cols=2)
    log(f"E4 done -> {OUT}")


if __name__ == "__main__":
    only = "--after-only" in sys.argv
    with Tee(OUT / "console.txt", mode="a" if only else "w"):
        main(after_only=only)
    export.export("e04")
