"""E1 - Cluster survey: sh.status(), rs.status() of every replica set, hello on every node.

Output: results/e01/
  sh_status.txt       raw sh.status() text from mongosh inside the mongos container
  nodes.csv           one row per node: role, replica set, state, ports, version
  rs_status.json      replSetGetStatus of each replica set (trimmed)
  rs_config.json      replica-set settings (election timeout, heartbeat, members, priorities)
  hello.json          full hello reply of each node
  mongos.json         listShards, balancerStatus, config.settings, mongos version
  docker_stats.csv    memory/CPU per container at survey time
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import export  # noqa: E402
from lab.cluster import node_table, print_table  # noqa: E402
from lab.common import (NODE_PORTS, REPLSETS, Tee, container_state, direct_client, docker,  # noqa: E402
                        hello, log, mongos_client, mongosh, now_iso, results_dir, save_json, section)

OUT = results_dir("e01")


def main() -> None:
    log(f"E1 cluster survey -> {OUT}")
    s3 = container_state("s3a") != "missing"
    replsets = [rs for rs in REPLSETS if rs != "shard3RS" or s3]

    # ---- 1. sh.status()
    section("1. sh.status()  (mongosh inside the mongos container)")
    txt = mongosh("mongos", "sh.status()")
    print(txt)
    (OUT / "sh_status.txt").write_text(txt, encoding="utf-8")

    # ---- 2. mongos view
    section("2. mongos: shards, balancer, settings")
    with mongos_client() as c:
        mongos_info = {
            "time": now_iso(),
            "version": c.server_info()["version"],
            "hello": c.admin.command("hello"),
            "listShards": c.admin.command("listShards")["shards"],
            "balancerStatus": c.admin.command("balancerStatus"),
            "config.settings": list(c.config.settings.find()),
            "config.databases": list(c.config.databases.find()),
            "config.mongos": list(c.config.mongos.find()),
        }
    print(f"mongos version {mongos_info['version']}  (hello.msg={mongos_info['hello'].get('msg')})")
    print_table([{"shard": s["_id"], "host": s["host"], "state": s.get("state")}
                 for s in mongos_info["listShards"]])
    b = mongos_info["balancerStatus"]
    print(f"\nbalancer mode={b.get('mode')} inBalancerRound={b.get('inBalancerRound')} "
          f"numBalancerRounds={b.get('numBalancerRounds')}")
    print("config.settings:", [{k: v for k, v in d.items()} for d in mongos_info["config.settings"]])
    save_json(OUT / "mongos.json", mongos_info)

    # ---- 3. rs.status() / rs.conf() per replica set
    section("3. rs.status() per replica set")
    rs_status, rs_conf = {}, {}
    for rs in replsets:
        with direct_client(REPLSETS[rs][0]) as c:
            st = c.admin.command("replSetGetStatus")
            conf = c.admin.command("replSetGetConfig")["config"]
        rs_status[rs] = st
        rs_conf[rs] = conf
        primary = next((m for m in st["members"] if m["stateStr"] == "PRIMARY"), None)
        print(f"\n{rs}  term={st.get('term')}  configsvr={conf.get('configsvr', False)}  "
              f"electionTimeoutMillis={conf['settings']['electionTimeoutMillis']}  "
              f"heartbeatIntervalMillis={conf['settings']['heartbeatIntervalMillis']}  "
              f"writeMajorityCount={st.get('writeMajorityCount')}")
        rows = []
        for m in st["members"]:
            lag = ""
            if primary and m["stateStr"] == "SECONDARY":
                lag = f"{(primary['optimeDate'] - m['optimeDate']).total_seconds():.1f}s"
            pr = next(x for x in conf["members"] if x["_id"] == m["_id"])
            rows.append({"member": m["name"], "state": m["stateStr"], "health": m["health"],
                         "uptime_s": m["uptime"], "priority": pr["priority"], "votes": pr["votes"],
                         "syncSource": m.get("syncSourceHost") or "-", "lag": lag or "-",
                         "electionDate_UTC": m.get("electionDate", "-")})
        print_table(rows)
    save_json(OUT / "rs_status.json", rs_status)
    save_json(OUT / "rs_config.json", rs_conf)

    # ---- 4. hello on every node
    section("4. hello on each node (direct connection to its host port)")
    hellos = {}
    rows = []
    for rs in replsets:
        for n in REPLSETS[rs]:
            h = hello(n)
            hellos[n] = h
            with direct_client(n) as c:
                ver = c.server_info()["version"]
                opts = c.admin.command("getCmdLineOpts")["parsed"]
            cache = opts.get("storage", {}).get("wiredTiger", {}).get("engineConfig", {}).get("cacheSizeGB")
            role = "PRIMARY" if h and h.get("isWritablePrimary") else ("SECONDARY" if h and h.get("secondary") else "?")
            rows.append({"node": n, "host_port": NODE_PORTS[n], "internal": h.get("me") if h else "-",
                         "setName": h.get("setName") if h else "-",
                         "cluster_role": opts.get("sharding", {}).get("clusterRole", "-"),
                         "role": role, "primary": h.get("primary") if h else "-",
                         "version": ver, "wt_cache_GB": cache,
                         "electionId": str(h.get("electionId", "")) if h and h.get("isWritablePrimary") else ""})
    print_table(rows)
    save_json(OUT / "hello.json", hellos)
    with open(OUT / "nodes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- 5. resources
    section("5. docker stats (memory budget)")
    names = [n for rs in replsets for n in REPLSETS[rs]] + ["mongos"]
    raw = docker("stats", "--no-stream", "--format",
                 "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}", *names)
    stats = []
    for line in raw.splitlines():
        name, cpu, mem, memp = line.split(",")
        used, limit = [x.strip() for x in mem.split("/")]
        stats.append({"container": name, "cpu": cpu, "mem_used": used, "mem_limit": limit, "mem_pct": memp})
    print_table(stats)
    total_mib = sum(_to_mib(s["mem_used"]) for s in stats)
    print(f"\ntotal memory used by cluster: {total_mib:.0f} MiB ({total_mib / 1024:.2f} GiB)")
    with open(OUT / "docker_stats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(stats[0].keys()))
        w.writeheader()
        w.writerows(stats)

    section("summary")
    print_table(node_table(s3))
    topology_diagram()
    log(f"E1 done. Raw data in {OUT}")


def topology_diagram() -> None:
    """Observed topology at survey time (lab.diagram, same style as the E6-E9 step diagrams)."""
    from lab import diagram
    st = diagram.snapshot()
    with mongos_client() as c:
        bal = c.admin.command("balancerStatus")
        cs = c.config.settings.find_one({"_id": "chunksize"})
    prim = {rs: next((n for n, v in st["nodes"].items() if v["rs"] == rs and v["role"] == "PRIMARY"), "none")
            for rs in REPLSETS if any(v["rs"] == rs for v in st["nodes"].values())}
    diagram.draw(st, OUT / "img" / "e01_topology.png", "E1 - cluster topology (observed)",
                 "roles read with hello on every node; mongos routes all client traffic",
                 events=[f"primaries: " + ", ".join(f"{rs}={p}" for rs, p in prim.items()),
                         "each shard = 3-member replica set (1 primary + 2 secondaries)",
                         f"balancer mode={bal.get('mode')}, chunk size {cs and cs.get('value')} MB",
                         "election timeout 10 s, heartbeat 2 s (rs.conf)"],
                 client_label="client  (pymongo from Windows)")


def _to_mib(s: str) -> float:
    s = s.strip()
    for unit, f in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1024 / 1024)):
        if s.endswith(unit):
            return float(s[: -len(unit)]) * f
    return 0.0


if __name__ == "__main__":
    with Tee(OUT / "console.txt"):
        main()
    export.export("e01")
