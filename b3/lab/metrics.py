"""Periodic metrics sampler -> CSV.

    python -m lab.metrics --interval 5 --duration 60 --colls ship_mono --out results\\tmp

Writes into the output folder:
  dist.csv        t_rel_s, coll, shard, owned_docs, owned_MB, orphan_docs, orphan_MB, raw_MB, chunks, jumbo
  opcounters.csv  t_rel_s, node, replset, role, insert, query, update, delete, getmore, command,
                  repl_insert   (cumulative serverStatus counters of every reachable mongod)
  balancer.csv    t_rel_s, mode, in_round, rounds, migrations_total, active_migrations,
                  plus one compliant_<coll> column per collection
  docker.csv      t_rel_s, container, cpu_pct, mem_MiB  (docker stats --no-stream; optional)

Every sample tolerates unreachable nodes (they are simply missing from that sample).
"""
from __future__ import annotations

import argparse
import csv
import threading
import time
from pathlib import Path

from .common import (DB_NAME, REPLSETS, container_state, direct_client, docker, log, mongos_client,
                     node_role, shard_distribution)


class CsvAppender:
    def __init__(self, path: Path, cols: list[str]):
        self.path, self.cols = path, cols
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w", newline="", encoding="utf-8")
        self.w = csv.DictWriter(self.f, fieldnames=cols, extrasaction="ignore")
        self.w.writeheader()

    def write(self, rows):
        for r in rows:
            self.w.writerow(r)
        self.f.flush()

    def close(self):
        self.f.close()


def _to_mib(s: str) -> float:
    s = s.strip()
    for unit, f in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1024 / 1024)):
        if s.endswith(unit):
            return round(float(s[: -len(unit)]) * f, 1)
    return 0.0


def nodes_present() -> list[tuple[str, str]]:
    out = []
    for rs, members in REPLSETS.items():
        if rs == "shard3RS" and container_state("s3a") == "missing":
            continue
        out += [(n, rs) for n in members]
    return out


def sample_opcounters(nodes) -> list[dict]:
    rows = []
    for n, rs in nodes:
        try:
            with direct_client(n, serverSelectionTimeoutMS=1000, connectTimeoutMS=1000, socketTimeoutMS=3000) as c:
                ss = c.admin.command("serverStatus", repl=1, metrics=0, locks=0, wiredTiger=0)
        except Exception:
            continue
        oc, ocr = ss.get("opcounters", {}), ss.get("opcountersRepl", {})
        rows.append({"node": n, "replset": rs, "role": node_role(ss.get("repl")),
                     **{k: oc.get(k, 0) for k in ("insert", "query", "update", "delete", "getmore", "command")},
                     "repl_insert": ocr.get("insert", 0)})
    return rows


def sample_balancer(client, colls) -> dict:
    b = client.admin.command("balancerStatus")
    row = {"mode": b.get("mode"), "in_round": b.get("inBalancerRound"), "rounds": b.get("numBalancerRounds"),
           "migrations_total": client.config.changelog.count_documents({"what": "moveChunk.commit"}),
           "active_migrations": client.config.migrationCoordinators.count_documents({})
           if "migrationCoordinators" in client.config.list_collection_names() else 0}
    for c in colls:
        try:
            st = client.admin.command("balancerCollectionStatus", f"{DB_NAME}.{c}")
            row[f"compliant_{c}"] = st.get("balancerCompliant")
            row[f"reason_{c}"] = st.get("firstComplianceViolation", "")
        except Exception:
            row[f"compliant_{c}"] = None
    return row


def sample_docker(names) -> list[dict]:
    raw = docker("stats", "--no-stream", "--format", "{{.Name}},{{.CPUPerc}},{{.MemUsage}}", *names, check=False)
    rows = []
    for line in raw.splitlines():
        try:
            name, cpu, mem = line.split(",")
            rows.append({"container": name, "cpu_pct": float(cpu.rstrip("%") or 0),
                         "mem_MiB": _to_mib(mem.split("/")[0])})
        except ValueError:
            continue
    return rows


class Sampler:
    """Background sampler. t_rel_s is relative to `t0` (pass a shared t0 to align with other logs)."""

    def __init__(self, out_dir: Path, colls: list[str], interval: float = 5.0, docker_stats: bool = True,
                 t0: float | None = None, tag: str = ""):
        self.out, self.colls, self.interval, self.docker_stats = Path(out_dir), colls, interval, docker_stats
        self.t0 = t0 or time.time()
        self.tag = tag
        self._stop = threading.Event()
        self._thread = None
        p = self.out
        self.dist = CsvAppender(p / "dist.csv", ["t_rel_s", "tag", "coll", "shard", "owned_docs", "owned_MB",
                                                 "orphan_docs", "orphan_MB", "raw_docs", "raw_MB", "chunks", "jumbo",
                                                 "write_ops", "read_ops"])
        self.ops = CsvAppender(p / "opcounters.csv", ["t_rel_s", "tag", "node", "replset", "role", "insert", "query",
                                                      "update", "delete", "getmore", "command", "repl_insert"])
        self.bal = CsvAppender(p / "balancer.csv", ["t_rel_s", "tag", "mode", "in_round", "rounds",
                                                    "migrations_total", "active_migrations"]
                               + [f"compliant_{c}" for c in colls] + [f"reason_{c}" for c in colls])
        self.dock = CsvAppender(p / "docker.csv", ["t_rel_s", "tag", "container", "cpu_pct", "mem_MiB"]) \
            if docker_stats else None
        self.last: dict = {}

    def sample_once(self) -> dict:
        t = round(time.time() - self.t0, 1)
        stamp = {"t_rel_s": t, "tag": self.tag}
        nodes = nodes_present()
        res = {"t_rel_s": t}
        try:
            with mongos_client(serverSelectionTimeoutMS=2000) as c:
                d = []
                for coll in self.colls:
                    for r in shard_distribution(c, coll):
                        d.append({**stamp, "coll": coll, **r})
                self.dist.write(d)
                res["dist"] = d
                b = {**stamp, **sample_balancer(c, self.colls)}
                self.bal.write([b])
                res["balancer"] = b
        except Exception as e:  # mongos/config down: still sample nodes
            res["error"] = repr(e)
        ops = [{**stamp, **r} for r in sample_opcounters(nodes)]
        self.ops.write(ops)
        res["ops"] = ops
        if self.dock:
            self.dock.write([{**stamp, **r} for r in sample_docker([n for n, _ in nodes] + ["mongos"])])
        self.last = res
        return res

    def _loop(self):
        nxt = time.time()
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception as e:  # noqa: BLE001
                log(f"sampler error: {e!r}")
            nxt += self.interval
            self._stop.wait(max(0.0, nxt - time.time()))

    def start(self) -> "Sampler":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)
        for a in (self.dist, self.ops, self.bal, self.dock):
            if a:
                a.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--colls", nargs="*", default=["ship_mono", "ship_lowcard", "ship_hashed", "ship_compound"])
    ap.add_argument("--no-docker", action="store_true")
    ap.add_argument("--out", default="results/tmp/metrics")
    a = ap.parse_args(argv)
    s = Sampler(Path(a.out), a.colls, a.interval, not a.no_docker).start()
    log(f"sampling every {a.interval}s for {a.duration}s -> {a.out}")
    try:
        time.sleep(a.duration)
    except KeyboardInterrupt:
        pass
    s.stop()
    log("done")


if __name__ == "__main__":
    main()
