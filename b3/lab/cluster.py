"""Cluster lifecycle: start containers, initiate replica sets, add shards, set chunk size.

Every step is idempotent, so `init` can be re-run at any time.

    python -m lab.cluster up        # docker compose up + init
    python -m lab.cluster init      # (re)initialise an already running cluster
    python -m lab.cluster status    # one-screen status table
    python -m lab.cluster health    # exit code 0 only if every set has a primary and all members are healthy
    python -m lab.cluster reset     # down -v (incl. shard3), up, init  [+ load, see --load]
    python -m lab.cluster down      # stop and remove containers, keep volumes
    python -m lab.cluster shard3    # start s3a/s3b/s3c, initiate shard3RS, addShard (used by E9)
    python -m lab.cluster shrink    # cap oplogs at 1 GB + compact them (disk space), no restart needed
"""
from __future__ import annotations

import argparse
import sys
import time

from pymongo.errors import OperationFailure

from .common import (BASE_REPLSETS, CHUNK_SIZE_MB, INTERNAL_PORT, MONGOS_PORT, NODE_PORTS, REPLSETS, compose,
                     container_state, direct_client, hello, log, mongos_client, node_role, wait_until)


# ---------------------------------------------------------------- containers
def wait_healthy(containers: list[str], timeout: float = 180) -> None:
    def ok():
        states = {c: container_state(c) for c in containers}
        return all(s == "running/healthy" for s in states.values())
    log(f"waiting for containers to be healthy: {', '.join(containers)}")
    wait_until(ok, timeout, 3, "containers healthy")


def up(shard3: bool = False) -> None:
    log("docker compose up -d" + (" (with shard3)" if shard3 else ""))
    # mongos depends_on the config servers being healthy; compose handles the ordering.
    compose("up", "-d", shard3=shard3)
    # mongos itself is waited for in init(): it cannot serve until cfgRS is initiated.
    nodes = [n for rs in BASE_REPLSETS for n in REPLSETS[rs]]
    if shard3:
        nodes += REPLSETS["shard3RS"]
    wait_healthy(nodes)


# ---------------------------------------------------------------- replica sets
def rs_status(rs: str) -> dict | None:
    for n in REPLSETS[rs]:
        try:
            with direct_client(n) as c:
                return c.admin.command("replSetGetStatus")
        except OperationFailure as e:
            if e.code == 94:  # NotYetInitialized
                return None
        except Exception:
            continue
    return None


def init_replset(rs: str) -> None:
    members = REPLSETS[rs]
    first = members[0]
    with direct_client(first) as c:
        try:
            c.admin.command("replSetGetStatus")
            log(f"{rs}: already initiated")
        except OperationFailure as e:
            if e.code != 94:
                raise
            cfg = {
                "_id": rs,
                "members": [{"_id": i, "host": f"{m}:{INTERNAL_PORT}"} for i, m in enumerate(members)],
            }
            if rs == "cfgRS":
                cfg["configsvr"] = True
            log(f"{rs}: replSetInitiate {[m['host'] for m in cfg['members']]}")
            c.admin.command("replSetInitiate", cfg)
    wait_primary(rs)


def primary_of(rs: str) -> str | None:
    for n in REPLSETS[rs]:
        h = hello(n)
        if h and h.get("isWritablePrimary"):
            return n
    return None


def wait_primary(rs: str, timeout: float = 90) -> str:
    t0 = time.time()
    p = wait_until(lambda: primary_of(rs), timeout, 1, f"{rs} primary")
    # also wait until every member is PRIMARY or SECONDARY
    wait_until(lambda: all(node_role(hello(n)) in ("PRIMARY", "SECONDARY") for n in REPLSETS[rs]),
               timeout, 1, f"{rs} members ready")
    log(f"{rs}: primary is {p} ({time.time() - t0:.1f}s)")
    return p


# ---------------------------------------------------------------- sharding
def wait_mongos(timeout: float = 120) -> None:
    def ok():
        with mongos_client(serverSelectionTimeoutMS=2000) as c:
            return c.admin.command("ping")["ok"] == 1
    wait_until(ok, timeout, 2, "mongos ping")


def add_shard(rs: str) -> None:
    with mongos_client() as c:
        existing = {s["_id"] for s in c.admin.command("listShards")["shards"]}
        if rs in existing:
            log(f"{rs}: already a shard")
            return
        seed = f"{rs}/" + ",".join(f"{m}:{INTERNAL_PORT}" for m in REPLSETS[rs])
        log(f"addShard {seed}")
        c.admin.command("addShard", seed, name=rs)


def set_chunk_size(mb: int = CHUNK_SIZE_MB) -> None:
    with mongos_client() as c:
        c.config.settings.update_one({"_id": "chunksize"}, {"$set": {"value": mb}}, upsert=True)
    log(f"config.settings chunksize = {mb} MB")


def init(shard3: bool = False) -> None:
    for rs in BASE_REPLSETS:
        init_replset(rs)
    wait_mongos()
    for rs in ["shard1RS", "shard2RS"]:
        add_shard(rs)
    set_chunk_size()
    if shard3:
        init_replset("shard3RS")
        add_shard("shard3RS")


def shard3() -> None:
    log("starting shard3 containers")
    compose("up", "-d", "s3a", "s3b", "s3c", shard3=True)
    wait_healthy(REPLSETS["shard3RS"])
    init_replset("shard3RS")
    add_shard("shard3RS")


# ---------------------------------------------------------------- status / health
def node_table(include_shard3: bool | None = None) -> list[dict]:
    if include_shard3 is None:
        include_shard3 = container_state("s3a") != "missing"
    rows = []
    for rs, members in REPLSETS.items():
        if rs == "shard3RS" and not include_shard3:
            continue
        for n in members:
            h = hello(n)
            rows.append({
                "container": n, "host_port": NODE_PORTS[n], "replset": rs,
                "docker": container_state(n), "role": node_role(h),
                "setName": (h or {}).get("setName"), "primary_seen": (h or {}).get("primary"),
            })
    h = None
    try:
        with mongos_client(serverSelectionTimeoutMS=2000) as c:
            h = c.admin.command("hello")
    except Exception:
        pass
    rows.append({"container": "mongos", "host_port": MONGOS_PORT, "replset": "-", "docker": container_state("mongos"),
                 "role": node_role(h), "setName": None, "primary_seen": None})
    return rows


def print_table(rows: list[dict], cols: list[str] | None = None) -> None:
    if not rows:
        print("(no rows)")
        return
    cols = cols or list(rows[0].keys())
    w = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  ".join(c.ljust(w[c]) for c in cols))
    print("  ".join("-" * w[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w[c]) for c in cols))


def status() -> list[dict]:
    rows = node_table()
    print_table(rows)
    try:
        with mongos_client(serverSelectionTimeoutMS=2000) as c:
            shards = c.admin.command("listShards")["shards"]
            bal = c.admin.command("balancerStatus")
            cs = c.config.settings.find_one({"_id": "chunksize"})
        print("\nshards:", ", ".join(f"{s['_id']} ({s['host']}, state={s.get('state')})" for s in shards))
        print(f"balancer: mode={bal.get('mode')} inBalancerRound={bal.get('inBalancerRound')}  "
              f"chunksize={cs and cs.get('value')} MB")
    except Exception as e:
        print(f"\nmongos unavailable: {e}")
    return rows


def healthy(verbose: bool = True) -> bool:
    rows = node_table()
    problems = []
    for rs in {r["replset"] for r in rows if r["replset"] != "-"}:
        members = [r for r in rows if r["replset"] == rs]
        if sum(r["role"] == "PRIMARY" for r in members) != 1:
            problems.append(f"{rs}: {sum(r['role'] == 'PRIMARY' for r in members)} primaries")
        for r in members:
            if r["role"] not in ("PRIMARY", "SECONDARY") or r["docker"] != "running/healthy":
                problems.append(f"{r['container']}: role={r['role']} docker={r['docker']}")
    m = rows[-1]
    if m["role"] != "MONGOS":
        problems.append(f"mongos: {m['role']}")
    if verbose:
        print("HEALTHY" if not problems else "UNHEALTHY:\n  " + "\n  ".join(problems))
    return not problems


def wait_healthy_cluster(timeout: float = 180) -> None:
    wait_until(lambda: healthy(verbose=False), timeout, 3, "cluster healthy")


def shrink(size_mb: int = 1024) -> None:
    """Cap every member's oplog at size_mb (replSetResizeOplog, online) and compact it, so space is
    released inside Docker's VM. Same limit as --oplogSize in the compose files, for clusters that were
    created before that flag existed. Safe to re-run."""
    for rs, members in REPLSETS.items():
        for n in members:
            if not container_state(n).startswith("running"):
                continue
            with direct_client(n, socketTimeoutMS=600000) as c:
                before = c.local.command("collStats", "oplog.rs")
                c.admin.command("replSetResizeOplog", 1, size=float(size_mb))
                try:
                    c.local.command("compact", "oplog.rs")
                except OperationFailure:
                    c.local.command("compact", "oplog.rs", force=True)
                after = c.local.command("collStats", "oplog.rs")
            log(f"{n}: oplog max {before['maxSize'] / 2**20:,.0f} -> {after['maxSize'] / 2**20:,.0f} MB, "
                f"on disk {before['storageSize'] / 2**20:,.0f} -> {after['storageSize'] / 2**20:,.0f} MB")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["up", "init", "status", "health", "reset", "down", "shard3", "shrink"])
    ap.add_argument("--load", action="store_true", help="reset: also run the data load afterwards")
    ap.add_argument("--docs", type=int, default=None, help="reset --load: generated document count")
    a = ap.parse_args(argv)

    if a.cmd == "up":
        up()
        init()
        status()
    elif a.cmd == "init":
        init(shard3=container_state("s3a").startswith("running"))
        status()
    elif a.cmd == "status":
        status()
    elif a.cmd == "health":
        return 0 if healthy() else 1
    elif a.cmd == "down":
        compose("down", "--remove-orphans", shard3=True)
    elif a.cmd == "reset":
        log("docker compose down -v (all volumes, incl. shard3)")
        compose("down", "-v", "--remove-orphans", shard3=True)
        up()
        init()
        if a.load:
            from . import load  # imported lazily: only needed here
            load.main(["all"] + (["--docs", str(a.docs)] if a.docs else []))
        status()
    elif a.cmd == "shard3":
        shard3()
        status()
    elif a.cmd == "shrink":
        shrink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
