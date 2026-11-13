"""Steady write probe: one insert every 100 ms through mongos, logged to CSV.

Default target is lab.probe, a small collection sharded on {k: 1} with the range k < 0 pinned to
shard1RS and k >= 0 pinned to shard2RS. Writes alternate between the two shards, so a failure of
one shard shows up as errors on that shard only. --shard restricts writes to one shard.
With --coll ship_xxx the writer inserts generator-shaped documents into that collection instead
(used by E9 to measure write latency during migrations).

    python -m lab.writer --duration 60 --out results\\tmp\\writer.csv
    python -m lab.writer --shard shard1RS --duration 120

retryWrites=False on purpose: every failure is visible, nothing is hidden by a driver retry.
Each write is bounded by a client-side timeout (pymongo CSOT, default 2 s -> sent as maxTimeMS).

CSV columns: seq, t_iso, t_epoch, t_rel_s, target, ok, error_type, error_code, error_msg, latency_ms
"""
from __future__ import annotations

import argparse
import csv
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pymongo
from bson import ObjectId
from bson.max_key import MaxKey
from bson.min_key import MinKey
from pymongo.errors import PyMongoError

from .common import DB_NAME, MONGOS_URI, log, mongos_client

PROBE = "probe"
PROBE_SHARDS = {"shard1RS": -1, "shard2RS": 1}   # sign of k -> owning shard


def ensure_probe(client) -> None:
    """Create lab.probe sharded on {k:1}, split at k=0, k<0 on shard1RS, k>=0 on shard2RS (idempotent)."""
    ns = f"{DB_NAME}.{PROBE}"
    if not client.config.collections.find_one({"_id": ns}):
        client.admin.command("shardCollection", ns, key={"k": 1})
    meta = client.config.collections.find_one({"_id": ns})
    chunks = list(client.config.chunks.find({"uuid": meta["uuid"]}))
    if len(chunks) == 1:
        client.admin.command("split", ns, middle={"k": 0})
    def contains(ch, k):
        lo, hi = ch["min"]["k"], ch["max"]["k"]
        return (isinstance(lo, MinKey) or lo <= k) and (isinstance(hi, MaxKey) or k < hi)

    for k, shard in ((-1, "shard1RS"), (1, "shard2RS")):
        owner = next(ch for ch in client.config.chunks.find({"uuid": meta["uuid"]}) if contains(ch, k))
        if owner["shard"] != shard:
            client.admin.command("moveChunk", ns, find={"k": k}, to=shard)
    # same as sh.disableBalancing(ns): the probe ranges must stay where they are during experiments
    client.config.collections.update_one({"_id": ns}, {"$set": {"noBalance": True}})


class Writer:
    def __init__(self, out: Path, interval: float = 0.1, timeout: float = 2.0, shard: str | None = None,
                 coll: str = PROBE, w="majority"):
        self.out, self.interval, self.timeout, self.shard, self.coll = Path(out), interval, timeout, shard, coll
        self.w = int(w) if str(w).isdigit() else w
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.rows: list[dict] = []
        self.client = pymongo.MongoClient(MONGOS_URI, retryWrites=False, serverSelectionTimeoutMS=int(timeout * 1000),
                                          connectTimeoutMS=2000, w=self.w)
        self._gen_docs = None

    # -- document factories
    def _probe_doc(self, seq: int) -> tuple[str, dict]:
        shard = self.shard or ("shard1RS" if seq % 2 == 0 else "shard2RS")
        k = PROBE_SHARDS[shard] * (seq + 1)
        return shard, {"k": k, "seq": seq, "t": datetime.now(timezone.utc), "pad": "x" * 200}

    def _ship_doc(self, seq: int) -> tuple[str, dict]:
        if self._gen_docs is None:
            from .datagen import Generator
            from .load import ref_docs
            with mongos_client() as c:
                g = Generator(ref_docs(c), seed=random.randrange(1 << 30))
            self._gen_docs = [d for b in g.docs(5000) for d in b]
        d = dict(self._gen_docs[seq % len(self._gen_docs)])
        d["_id"] = ObjectId()
        d["ts"] = datetime.now(timezone.utc)
        return d.get("region", "?"), d

    # -- loop
    def run(self, duration: float | None = None) -> list[dict]:
        self.out.parent.mkdir(parents=True, exist_ok=True)
        coll = self.client[DB_NAME][self.coll]
        make = self._probe_doc if self.coll == PROBE else self._ship_doc
        if self.coll == PROBE:
            with mongos_client() as c:
                ensure_probe(c)
        f = open(self.out, "w", newline="", encoding="utf-8")
        cols = ["seq", "t_iso", "t_epoch", "t_rel_s", "target", "ok", "error_type", "error_code", "error_msg",
                "latency_ms"]
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        t0 = time.time()
        seq = 0
        try:
            while not self._stop.is_set() and (duration is None or time.time() - t0 < duration):
                target, doc = make(seq)
                ts = time.time()
                row = {"seq": seq, "t_iso": datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds"),
                       "t_epoch": round(ts, 3), "t_rel_s": round(ts - t0, 3), "target": target, "ok": 1, "error_type": "",
                       "error_code": "", "error_msg": ""}
                try:
                    with pymongo.timeout(self.timeout):
                        coll.insert_one(doc)
                except PyMongoError as e:
                    row.update(ok=0, error_type=type(e).__name__, error_code=getattr(e, "code", "") or "",
                               error_msg=str(e).split(", full error")[0][:160].replace("\n", " "))
                row["latency_ms"] = round((time.time() - ts) * 1000, 1)
                wr.writerow(row)
                f.flush()
                self.rows.append(row)
                seq += 1
                nxt = t0 + seq * self.interval
                self._stop.wait(max(0.0, nxt - time.time()))
        finally:
            f.close()
        return self.rows

    def start(self) -> "Writer":
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> list[dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.timeout + 5)
        self.client.close()
        return self.rows


def summarize(rows: list[dict], target: str | None = None) -> dict:
    """Outage = time from the first failed write to the first success after the last failure."""
    rs = [r for r in rows if target is None or r["target"] == target]
    fails = [r for r in rs if not r["ok"]]
    out = {"target": target or "all", "writes": len(rs), "failed": len(fails),
           "error_types": sorted({f'{r["error_type"]}({r["error_code"]})' for r in fails})}
    lat = sorted(r["latency_ms"] for r in rs if r["ok"])
    if lat:
        out.update(p50_ms=lat[len(lat) // 2], p99_ms=lat[min(len(lat) - 1, int(len(lat) * 0.99))], max_ms=lat[-1])
    if fails:
        first, last = fails[0], fails[-1]
        after = next((r for r in rs if r["seq"] > last["seq"] and r["ok"]), None)
        out["first_fail_s"] = first["t_rel_s"]
        out["recovered_s"] = after["t_rel_s"] if after else None
        out["outage_s"] = round(after["t_rel_s"] - first["t_rel_s"], 2) if after else None
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--shard", choices=list(PROBE_SHARDS))
    ap.add_argument("--coll", default=PROBE)
    ap.add_argument("--w", default="majority")
    ap.add_argument("--out", default="results/tmp/writer.csv")
    a = ap.parse_args(argv)
    w = Writer(Path(a.out), a.interval, a.timeout, a.shard, a.coll, a.w)
    log(f"writer: 1 insert / {a.interval * 1000:.0f} ms into {DB_NAME}.{a.coll} for {a.duration}s -> {a.out}")
    try:
        rows = w.run(a.duration)
    except KeyboardInterrupt:
        rows = w.rows
    for t in sorted({r["target"] for r in rows}):
        print(summarize(rows, t))


if __name__ == "__main__":
    main()
