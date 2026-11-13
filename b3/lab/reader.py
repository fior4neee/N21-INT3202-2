"""Read probe through mongos with a chosen read preference; logs which node answered every read.

    python -m lab.reader --mode secondary --rate 20 --duration 20 --out results\\tmp\\reader.csv

Each read is find_one({region, recrd}) on lab.ship_compound with a full shard-key equality (so it is
routed to exactly one shard) and a unique comment "rd-<run>-<seq>". The per-node profiler (level 2,
filtered on that comment prefix) is switched on for the run; afterwards each node's system.profile is
read and every comment is mapped to the node that executed it. The profiler is switched off again.

CSV columns: seq, t_rel_s, mode, comment, ok, error, latency_ms, node, shard, node_role
"""
from __future__ import annotations

import argparse
import csv
import random
import time
import uuid
from pathlib import Path

from pymongo import ReadPreference

from .common import DB_NAME, REPLSETS, container_state, direct_client, hello, log, mongos_client, node_role

MODES = {"primary": ReadPreference.PRIMARY, "primaryPreferred": ReadPreference.PRIMARY_PREFERRED,
         "secondary": ReadPreference.SECONDARY, "secondaryPreferred": ReadPreference.SECONDARY_PREFERRED,
         "nearest": ReadPreference.NEAREST}


def shard_nodes() -> list[tuple[str, str]]:
    out = []
    for rs, members in REPLSETS.items():
        if rs == "cfgRS" or container_state(members[0]) == "missing":
            continue
        out += [(n, rs) for n in members]
    return out


def profiler(level: int, prefix: str = "rd-") -> None:
    for n, _ in shard_nodes():
        try:
            with direct_client(n) as c:
                if level:
                    c[DB_NAME].command("profile", level, filter={"command.comment": {"$regex": f"^{prefix}"}})
                else:
                    c[DB_NAME].command("profile", 0)
        except Exception as e:  # node down: nothing to profile there
            log(f"profiler level {level} on {n} failed: {e!r}")


def attribute(prefix: str) -> dict[str, str]:
    """comment -> node, from every shard node's system.profile."""
    out = {}
    for n, _ in shard_nodes():
        try:
            with direct_client(n) as c:
                for d in c[DB_NAME]["system.profile"].find({"command.comment": {"$regex": f"^{prefix}"}},
                                                           {"command.comment": 1}):
                    out[d["command"]["comment"]] = n
        except Exception:
            continue
    return out


def sample_keys(n: int = 300) -> list[dict]:
    with mongos_client() as c:
        return [{"region": d["region"], "recrd": d["recrd"]} for d in
                c[DB_NAME].ship_compound.aggregate([{"$sample": {"size": n}}, {"$project": {"region": 1, "recrd": 1}}])]


def run(mode: str, rate: float, duration: float, out: Path | None = None, keys: list[dict] | None = None) -> list[dict]:
    keys = keys or sample_keys()
    run_id = uuid.uuid4().hex[:6]
    prefix = f"rd-{run_id}-"
    roles = {n: node_role(hello(n)) for n, _ in shard_nodes()}
    rs_of = dict(shard_nodes())
    profiler(2, "rd-")
    rows = []
    try:
        with mongos_client() as c:
            coll = c[DB_NAME].ship_compound.with_options(read_preference=MODES[mode])
            t0 = time.time()
            seq = 0
            while time.time() - t0 < duration:
                k = random.choice(keys)
                comment = f"{prefix}{seq}"
                ts = time.time()
                row = {"seq": seq, "t_rel_s": round(ts - t0, 3), "mode": mode, "comment": comment, "ok": 1, "error": ""}
                try:
                    coll.find_one(k, {"_id": 1}, comment=comment)
                except Exception as e:
                    row.update(ok=0, error=type(e).__name__)
                row["latency_ms"] = round((time.time() - ts) * 1000, 2)
                rows.append(row)
                seq += 1
                time.sleep(max(0.0, t0 + seq / rate - time.time()))
        time.sleep(1.0)
        who = attribute(prefix)
    finally:
        profiler(0)
    for r in rows:
        n = who.get(r["comment"], "")
        r.update(node=n, shard=rs_of.get(n, ""), node_role=roles.get(n, ""))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return rows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=list(MODES), default="secondary")
    ap.add_argument("--rate", type=float, default=20)
    ap.add_argument("--duration", type=float, default=20)
    ap.add_argument("--out", default="results/tmp/reader.csv")
    a = ap.parse_args(argv)
    rows = run(a.mode, a.rate, a.duration, Path(a.out))
    from collections import Counter
    cnt = Counter(r["node"] or "unattributed" for r in rows)
    log(f"{a.mode}: {len(rows)} reads -> " + ", ".join(f"{n}: {v}" for n, v in sorted(cnt.items())))


if __name__ == "__main__":
    main()
