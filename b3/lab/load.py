"""Data loading: Atlas reference copy, sharded collections, bulk insert of generated documents.

    python -m lab.load atlas [--force]          # copy Atlas shipwrecks -> lab.shipwrecks_ref (unsharded)
    python -m lab.load create [names...]        # drop + shardCollection (empty)
    python -m lab.load all [--docs 500000]      # atlas (if needed) + create + load all 4 collections
    python -m lab.load one ship_mono [--docs N] # one collection only
    python -m lab.load dist                     # per-shard distribution of every collection

The Atlas connection string is read from .env (ATLAS_URI) and never printed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from pymongo import InsertOne, MongoClient

from .cluster import print_table
from .common import DB_NAME, log, mongos_client, shard_distribution
from .datagen import COPY_FIELDS, Generator

REF_COLL = "shipwrecks_ref"
DEFAULT_DOCS = 500_000
SHARD_KEYS = {
    "ship_mono": {"ts": 1},                   # monotonic -> hot shard
    "ship_lowcard": {"feature_type": 1},      # 5 values  -> jumbo chunks
    "ship_hashed": {"_id": "hashed"},         # even spread, range queries scatter-gather
    "ship_compound": {"region": 1, "recrd": 1},  # balanced, targeted by region
}


# ---------------------------------------------------------------- Atlas -> local reference set
def import_atlas(force: bool = False) -> int:
    uri = os.environ.get("ATLAS_URI")
    if not uri:
        raise SystemExit("ATLAS_URI missing from .env")
    with MongoClient(uri, serverSelectionTimeoutMS=20000) as atlas, mongos_client() as local:
        src = atlas.sample_geospatial.shipwrecks
        dst = local[DB_NAME][REF_COLL]
        n_src = src.estimated_document_count()
        n_dst = dst.estimated_document_count()
        if n_dst == n_src and not force:
            log(f"{DB_NAME}.{REF_COLL} already has {n_dst} docs (same as Atlas) - skipping (use --force)")
            return n_dst
        log(f"copying Atlas sample_geospatial.shipwrecks ({n_src} docs) -> {DB_NAME}.{REF_COLL}")
        dst.drop()
        t0, batch, n = time.time(), [], 0
        for d in src.find():
            batch.append(d)
            if len(batch) == 2000:
                dst.insert_many(batch, ordered=False)
                n += len(batch)
                batch = []
        if batch:
            dst.insert_many(batch, ordered=False)
            n += len(batch)
        log(f"copied {n} docs in {time.time() - t0:.1f}s")
        return n


def ref_docs(client) -> list[dict]:
    docs = list(client[DB_NAME][REF_COLL].find({}, {f: 1 for f in COPY_FIELDS + ["coordinates", "depth"]}))
    if not docs:
        raise SystemExit(f"{DB_NAME}.{REF_COLL} is empty - run `python -m lab.load atlas` first")
    return docs


# ---------------------------------------------------------------- sharded collections
def create_sharded(client, name: str) -> None:
    db = client[DB_NAME]
    db[name].drop()
    client.admin.command("shardCollection", f"{DB_NAME}.{name}", key=SHARD_KEYS[name])
    log(f"shardCollection {DB_NAME}.{name} key={SHARD_KEYS[name]}")


def load_collection(client, name: str, n: int, seed: int = 42, batch: int = 1000,
                    on_progress=None, progress_every: float = 5.0) -> dict:
    """Insert n generated docs. on_progress(inserted, elapsed_s) is called about every progress_every s."""
    gen = Generator(ref_docs(client), seed)
    coll = client[DB_NAME][name]
    t0 = last = time.time()
    done = 0
    for docs in gen.docs(n, batch):
        coll.bulk_write([InsertOne(d) for d in docs], ordered=False)
        done += len(docs)
        now = time.time()
        if now - last >= progress_every:
            last = now
            if on_progress:
                on_progress(done, now - t0)
            else:
                log(f"  {name}: {done:>9,}/{n:,} ({done / (now - t0):,.0f} docs/s)")
    el = time.time() - t0
    log(f"{name}: inserted {done:,} docs in {el:.1f}s ({done / el:,.0f} docs/s)")
    return {"collection": name, "docs": done, "seconds": round(el, 1), "docs_per_s": round(done / el)}


def print_distribution(client, names=None) -> None:
    for name in names or SHARD_KEYS:
        if name not in client[DB_NAME].list_collection_names():
            continue
        rows = shard_distribution(client, name)
        print(f"\n{DB_NAME}.{name}  key={SHARD_KEYS[name]}")
        print_table(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["atlas", "create", "all", "one", "dist"])
    ap.add_argument("names", nargs="*", help="collection names (default: all 4)")
    ap.add_argument("--docs", type=int, default=DEFAULT_DOCS)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    names = a.names or list(SHARD_KEYS)
    bad = [n for n in names if n not in SHARD_KEYS]
    if bad:
        raise SystemExit(f"unknown collection(s) {bad}; choose from {list(SHARD_KEYS)}")

    if a.cmd == "atlas":
        import_atlas(a.force)
        return 0
    with mongos_client() as c:
        if a.cmd == "dist":
            print_distribution(c, names)
            return 0
        if a.cmd in ("all", "one"):
            if c[DB_NAME][REF_COLL].estimated_document_count() == 0:
                import_atlas()
        summary = []
        for name in names:
            create_sharded(c, name)
            if a.cmd in ("all", "one"):
                summary.append(load_collection(c, name, a.docs, a.seed))
        if summary:
            print()
            print_table(summary)
            print_distribution(c, names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
