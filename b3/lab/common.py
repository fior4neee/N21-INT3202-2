"""Shared constants and helpers: topology, docker CLI wrapper, Mongo clients, results dir."""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
COMPOSE_BASE = ROOT / "docker-compose.yml"
COMPOSE_SHARD3 = ROOT / "docker-compose.shard3.yml"

load_dotenv(ROOT / ".env")  # ATLAS_URI (never printed), optional MONGOS_PORT
# 127.0.0.1 (not "localhost") so we never hit the IPv6 relay or a native mongod by accident.
MONGOS_PORT = int(os.environ.get("MONGOS_PORT", "27100"))
MONGOS_URI = f"mongodb://127.0.0.1:{MONGOS_PORT}/"
INTERNAL_PORT = 27017

# container name -> host port
NODE_PORTS = {
    "cfg1": 27101, "cfg2": 27102, "cfg3": 27103,
    "s1a": 27104, "s1b": 27105, "s1c": 27106,
    "s2a": 27107, "s2b": 27108, "s2c": 27109,
    "s3a": 27110, "s3b": 27111, "s3c": 27112,
}
REPLSETS = {
    "cfgRS": ["cfg1", "cfg2", "cfg3"],
    "shard1RS": ["s1a", "s1b", "s1c"],
    "shard2RS": ["s2a", "s2b", "s2c"],
    "shard3RS": ["s3a", "s3b", "s3c"],
}
BASE_REPLSETS = ["cfgRS", "shard1RS", "shard2RS"]
CHUNK_SIZE_MB = 8
DB_NAME = "lab"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- docker CLI
def run(cmd: list[str], check: bool = True, timeout: float | None = 300) -> subprocess.CompletedProcess:
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                        encoding="utf-8", errors="replace")
    if check and cp.returncode != 0:
        raise RuntimeError(f"command failed ({cp.returncode}): {' '.join(cmd)}\n{cp.stdout}\n{cp.stderr}")
    return cp


def docker(*args: str, check: bool = True, timeout: float | None = 300) -> str:
    return run(["docker", *args], check=check, timeout=timeout).stdout.strip()


def compose(*args: str, shard3: bool = False, check: bool = True, timeout: float | None = 600) -> str:
    cmd = ["docker", "compose", "-f", str(COMPOSE_BASE)]
    if shard3:
        cmd += ["-f", str(COMPOSE_SHARD3)]
    return run(cmd + list(args), check=check, timeout=timeout).stdout.strip()


def container_state(name: str) -> str:
    """'running', 'paused', 'exited', 'missing', ... (+ '/healthy' etc. when available)."""
    cp = run(["docker", "inspect", "-f",
              "{{.State.Status}}{{if .State.Health}}/{{.State.Health.Status}}{{end}}", name], check=False)
    return cp.stdout.strip() if cp.returncode == 0 else "missing"


def mongosh(container: str, js: str, timeout: float = 60) -> str:
    """Run JS with the mongosh that ships inside the container (no host mongosh needed)."""
    return run(["docker", "exec", container, "mongosh", "--quiet", "--port", str(INTERNAL_PORT),
                "--eval", js], check=False, timeout=timeout).stdout


# ---------------------------------------------------------------- Mongo clients
def mongos_client(**kw) -> MongoClient:
    kw.setdefault("serverSelectionTimeoutMS", 5000)
    return MongoClient(MONGOS_URI, **kw)


def direct_client(node: str, **kw) -> MongoClient:
    """Direct (non-replica-set-aware) connection to one mongod through its host port."""
    kw.setdefault("serverSelectionTimeoutMS", 3000)
    kw.setdefault("connectTimeoutMS", 3000)
    kw.setdefault("socketTimeoutMS", 10000)
    return MongoClient(f"mongodb://127.0.0.1:{NODE_PORTS[node]}/", directConnection=True, **kw)


def hello(node: str, timeout_ms: int = 2000) -> dict | None:
    try:
        with direct_client(node, serverSelectionTimeoutMS=timeout_ms, connectTimeoutMS=timeout_ms,
                           socketTimeoutMS=timeout_ms) as c:
            return c.admin.command("hello")
    except Exception:
        return None


def node_role(h: dict | None) -> str:
    if h is None:
        return "UNREACHABLE"
    if h.get("msg") == "isdbgrid":
        return "MONGOS"
    if h.get("isWritablePrimary"):
        return "PRIMARY"
    if h.get("secondary"):
        return "SECONDARY"
    if h.get("arbiterOnly"):
        return "ARBITER"
    return "OTHER"


def wait_until(pred, timeout: float, interval: float = 1.0, desc: str = "condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = pred()
            if last:
                return last
        except Exception as e:  # noqa: BLE001 - keep polling
            last = e
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for {desc} (last={last!r})")


# ---------------------------------------------------------------- sharding info
def shard_distribution(client: MongoClient, coll: str, db: str = DB_NAME) -> list[dict]:
    """Per-shard distribution of one sharded collection.

    owned_* come from $shardedDataDistribution and exclude orphans (documents already copied to
    another shard by a migration but not yet deleted by the range deleter, default delay 900 s).
    raw_* come from $collStats and INCLUDE orphans; the difference is the migration backlog.
    write_ops / read_ops are $collStats latencyStats of THIS collection on each shard's primary
    (cumulative since the collection was created there); unlike serverStatus opcounters they do not
    mix in other collections and do not count migration clones.
    """
    rows: dict[str, dict] = {}

    def row(shard):
        return rows.setdefault(shard, {"shard": shard, "owned_docs": 0, "owned_MB": 0.0, "orphan_docs": 0,
                                       "orphan_MB": 0.0, "raw_docs": 0, "raw_MB": 0.0, "storage_MB": 0.0,
                                       "chunks": 0, "jumbo": 0, "write_ops": 0, "read_ops": 0})
    ns = f"{db}.{coll}"
    for d in client.admin.aggregate([{"$shardedDataDistribution": {}}, {"$match": {"ns": ns}}]):
        for s in d["shards"]:
            r = row(s["shardName"])
            r["owned_docs"] = s.get("numOwnedDocuments", 0)
            r["owned_MB"] = round(s.get("ownedSizeBytes", 0) / 2**20, 2)
            r["orphan_docs"] = s.get("numOrphanedDocs", 0)
            r["orphan_MB"] = round(s.get("orphanedSizeBytes", 0) / 2**20, 2)
    for s in client[db][coll].aggregate([{"$collStats": {"storageStats": {}, "latencyStats": {}}}]):
        st = s["storageStats"]
        r = row(s["shard"])
        lat = s.get("latencyStats", {})
        r["write_ops"] = lat.get("writes", {}).get("ops", 0)
        r["read_ops"] = lat.get("reads", {}).get("ops", 0)
        r["raw_docs"] = st.get("count", 0)
        r["raw_MB"] = round(st.get("size", 0) / 2**20, 2)
        r["storage_MB"] = round(st.get("storageSize", 0) / 2**20, 2)
    meta = client.config.collections.find_one({"_id": ns})
    if meta:
        for ch in client.config.chunks.find({"uuid": meta["uuid"]}, {"shard": 1, "jumbo": 1}):
            r = row(ch["shard"])
            r["chunks"] += 1
            r["jumbo"] += int(bool(ch.get("jumbo")))
    return sorted(rows.values(), key=lambda r: r["shard"])


# ---------------------------------------------------------------- results
class Tee:
    """Copy everything printed to stdout into a file (results/eNN/console.txt) as well."""

    def __init__(self, path: Path, mode: str = "w"):
        self.path, self.mode = path, mode

    def __enter__(self):
        import sys
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, self.mode, encoding="utf-8")
        self._orig = sys.stdout
        sys.stdout = self
        return self

    def write(self, s):
        self._orig.write(s)
        self._f.write(s)
        return len(s)

    def flush(self):
        self._orig.flush()
        self._f.flush()

    def __exit__(self, *exc):
        import sys
        sys.stdout = self._orig
        self._f.close()
        return False


def section(title: str) -> None:
    """Section header; lab.export splits console.txt into one image per section."""
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def results_dir(exp: str) -> Path:
    d = RESULTS / exp
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path
