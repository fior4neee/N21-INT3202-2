"""E0 - Test environment (report chapter 3): facts about the host, Docker VM and every container,
read from the RUNNING system (docker info / docker inspect / buildInfo), plus an architecture diagram.

Output results/e00/: console.txt, containers.csv, environment.json, img/e00_architecture.png
"""
import csv
import ctypes
import json
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from lab import export  # noqa: E402
from lab.cluster import print_table  # noqa: E402
from lab.common import (MONGOS_PORT, REPLSETS, Tee, container_state, docker, log, mongos_client,  # noqa: E402
                        results_dir, save_json, section)

OUT = results_dir("e00")
BLUE, BLUE_LT, INK, INK2, MUTED, GRID = "#2a78d6", "#dbe8f8", "#0b0b0b", "#52514e", "#898781", "#e4e3df"
SHARD_COL = {"shard1RS": "#2a78d6", "shard2RS": "#eb6834", "shard3RS": "#1baf7a", "cfgRS": "#6250d6"}


def host_ram_gb() -> float:
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return round(m.ullTotalPhys / 2**30, 1)


def flag(cmd: list, name: str, default="default"):
    return cmd[cmd.index(name) + 1] if name in cmd else default


def gather() -> dict:
    info = json.loads(docker("info", "--format", "{{json .}}"))
    names = [n for rs in REPLSETS.values() for n in rs if container_state(n) != "missing"] + ["mongos"]
    rows = []
    for n in names:
        d = json.loads(docker("inspect", n))[0]
        cmd = d["Config"]["Cmd"]
        role = next((rs for rs, m in REPLSETS.items() if n in m), "router")
        ports = d["HostConfig"]["PortBindings"].get("27017/tcp", [{}])[0].get("HostPort")
        vol = next((m["Name"] for m in d["Mounts"] if m["Destination"] == "/data/db"), "")
        rows.append({"container": n, "role": role, "image": d["Config"]["Image"],
                     "host_port": ports, "ip": next(iter(d["NetworkSettings"]["Networks"].values()))["IPAddress"],
                     "mem_limit_MB": d["HostConfig"]["Memory"] // 2**20,
                     "wt_cache_GB": flag(cmd, "--wiredTigerCacheSizeGB", "-"),
                     "oplog_MB": flag(cmd, "--oplogSize", "default (5% disk)") if cmd[0] == "mongod" else "-",
                     "volume": vol if vol.startswith("mongolab_") else "(anonymous)",
                     "restart": d["HostConfig"]["RestartPolicy"]["Name"]})
    with mongos_client() as c:
        ver = c.server_info()["version"]
    return {
        "host": {"os": f"{platform.system()} {platform.release()} ({platform.version()})", "ram_GB": host_ram_gb(),
                 "python": platform.python_version()},
        "docker": {"server": info["ServerVersion"], "os": info["OperatingSystem"], "kernel": info["KernelVersion"],
                   "vm_cpus": info["NCPU"], "vm_mem_GB": round(info["MemTotal"] / 2**30, 2)},
        "mongodb": ver, "mongos_host_port": MONGOS_PORT, "network": "mongolab_net (bridge)",
        "containers": rows,
    }


def _box(ax, x, y, w, h, fc, ec, lw=1.2, ls="-", r=1.0, z=1):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw,
                                ls=ls, zorder=z))


def _arrow(ax, a, b, color=INK2, ls="-", lw=1.4, label=None, z=3, rad=0.0):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=11, color=color, lw=lw, ls=ls, zorder=z,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((a[0] + b[0]) / 2 + 0.6, (a[1] + b[1]) / 2, label, fontsize=7.8, color=color, va="center",
                bbox=dict(fc="white", ec="none", pad=0.8), zorder=z + 1)


def architecture(env: dict, path: Path) -> None:
    rows = {r["container"]: r for r in env["containers"]}
    fig = plt.figure(figsize=(14, 9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 66)
    ax.axis("off")
    ax.text(1.5, 64.5, "Test environment: MongoDB sharded cluster on one Windows machine", fontsize=15,
            fontweight="bold", va="top", color=INK)
    h, d = env["host"], env["docker"]
    ax.text(1.5, 61.6, f"{h['os'].split(' (')[0]} · {h['ram_GB']} GB RAM · Docker {d['server']} · "
            f"MongoDB {env['mongodb']} · values read from the running containers", fontsize=9.5, color=INK2, va="top")

    # Windows host
    _box(ax, 1, 1, 98, 58, "#fbfbf9", MUTED, lw=1.5, r=1.6)
    ax.text(2.2, 57.8, f"Windows host ({h['ram_GB']} GB RAM)", fontsize=11, fontweight="bold", color=INK, va="top")
    # host-side tools
    _box(ax, 2.2, 38, 21, 17.5, "white", GRID, r=1.0, z=2)
    ax.text(3.2, 54.6, f"PowerShell + Python {h['python']}", fontsize=8.6, fontweight="bold", va="top", zorder=3)
    ax.text(3.2, 52.4, "lab/cluster.py   init / status / reset\nlab/load.py        Atlas import + load\n"
            "lab/writer.py     1 write / 100 ms\nlab/metrics.py    5 s sampler\nlab/chaos.py       docker kill/stop/\n"
            "                     pause/disconnect\nexperiments/e00…e10\nresults/  CSV · JSON · PNG · PDF",
            fontsize=7.6, va="top", family="monospace", color=INK, zorder=3, linespacing=1.35)
    # Atlas
    _box(ax, 2.2, 28, 21, 7.5, "white", GRID, ls="--", r=1.0, z=2)
    ax.text(3.2, 34.6, "MongoDB Atlas (cloud)", fontsize=8.6, fontweight="bold", va="top", zorder=3)
    ax.text(3.2, 32.4, "sample_geospatial.shipwrecks\n11,095 docs, copied once (pymongo)", fontsize=7.6,
            va="top", color=INK2, zorder=3)
    # native MongoDB service (port conflict)
    _box(ax, 2.2, 18.5, 21, 7, "white", GRID, r=1.0, z=2)
    ax.text(3.2, 24.6, "native MongoDB service", fontsize=8.6, fontweight="bold", va="top", zorder=3)
    ax.text(3.2, 22.4, "127.0.0.1:27017 (not part of lab)\n→ mongos published on " + str(env["mongos_host_port"]),
            fontsize=7.6, va="top", color=INK2, zorder=3)
    ax.text(3.2, 16.5, "Host ports → containers\n" + "\n".join(
        f"{n:<7}{rows[n]['host_port']}" for n in ["mongos"] + [x for rs in REPLSETS.values() for x in rs if x in rows]),
        fontsize=7.4, va="top", family="monospace", color=INK2, zorder=3, linespacing=1.15)

    # Docker VM
    vx, vy, vw, vh = 25, 2.2, 73, 53.5
    _box(ax, vx, vy, vw, vh, "white", INK2, lw=1.5, r=1.4, z=1)
    ax.text(vx + 1.2, vy + vh - 1.0, f"Docker Desktop VM (WSL 2, kernel {d['kernel']}) · {d['vm_cpus']} vCPU · "
            f"{d['vm_mem_GB']} GB", fontsize=9.5, fontweight="bold", va="top", color=INK, zorder=2)
    ax.text(vx + 1.2, vy + vh - 3.4, f"bridge network {env['network']} · every node listens on <name>:27017 · "
            "data in named volumes (no Windows bind mounts)", fontsize=8, va="top", color=INK2, zorder=2)

    # mongos
    mx, my = vx + 30, 39
    _box(ax, mx, my, 13, 7.5, "#eeeeea", INK, lw=1.6, r=0.8, z=3)
    m = rows["mongos"]
    ax.text(mx + 6.5, my + 5.4, "mongos", ha="center", fontsize=11, fontweight="bold", zorder=4)
    ax.text(mx + 6.5, my + 3.2, f"router · host :{m['host_port']}", ha="center", fontsize=7.4, color=INK2, zorder=4)
    ax.text(mx + 6.5, my + 1.4, f"{m['mem_limit_MB']} MB · {m['ip']}", ha="center", fontsize=7.0, color=INK2, zorder=4)
    _arrow(ax, (23.2, 47), (mx, my + 4), INK2, label="pymongo :" + str(m["host_port"]))
    _arrow(ax, (23.2, 31), (mx, my + 1.5), MUTED, ls="--", label="import", rad=-0.1)

    # config RS
    cx, cy = vx + 46, 37.5
    _box(ax, cx, cy, 31.5 - 4.5, 13, "#f7f6fd", SHARD_COL["cfgRS"], r=1.0, z=2)
    ax.text(cx + 0.8, cy + 12.2, "cfgRS · config servers (--configsvr)", fontsize=8.6, fontweight="bold", va="top",
            zorder=3)
    for i, n in enumerate(REPLSETS["cfgRS"]):
        x = cx + 0.4 + i * 8.8
        _box(ax, x, cy + 1.2, 8.4, 8.2, "white", SHARD_COL["cfgRS"], lw=1.6, r=0.6, z=3)
        r = rows[n]
        ax.text(x + 4.2, cy + 7.6, n, ha="center", fontsize=9.5, fontweight="bold", zorder=4)
        ax.text(x + 4.2, cy + 5.6, f":{r['host_port']}", ha="center", fontsize=6.8, color=INK2, zorder=4)
        ax.text(x + 4.2, cy + 3.9, f"{r['mem_limit_MB']} MB", ha="center", fontsize=6.6, color=INK2, zorder=4)
        ax.text(x + 4.2, cy + 2.3, f"cache {r['wt_cache_GB']} GB", ha="center", fontsize=6.6, color=INK2, zorder=4)
    _arrow(ax, (mx + 13, my + 4), (cx, cy + 6.5), SHARD_COL["cfgRS"], label="metadata")

    # shards
    shards = [rs for rs in REPLSETS if rs != "cfgRS" and REPLSETS[rs][0] in rows]
    sw = (vw - 2.4 - 1.5 * (len(shards) - 1)) / len(shards)
    for j, rs in enumerate(shards):
        x0 = vx + 1.2 + j * (sw + 1.5)
        y0 = 4
        _box(ax, x0, y0, sw, 25, "#fafcff", SHARD_COL[rs], r=1.0, z=2)
        ax.text(x0 + 0.8, y0 + 24.2, f"{rs} · shard (--shardsvr), 3-member replica set", fontsize=8.6,
                fontweight="bold", va="top", zorder=3)
        nw = min(9.6, (sw - 2) / 3 - 0.4)
        for i, n in enumerate(REPLSETS[rs]):
            x = x0 + 0.8 + i * (nw + 0.6)
            _box(ax, x, y0 + 9, nw, 11, "white", SHARD_COL[rs], lw=1.6, r=0.6, z=3)
            r = rows[n]
            ax.text(x + nw / 2, y0 + 17.8, n, ha="center", fontsize=10, fontweight="bold", zorder=4)
            ax.text(x + nw / 2, y0 + 15.6, f"host :{r['host_port']}", ha="center", fontsize=7, color=INK2, zorder=4)
            ax.text(x + nw / 2, y0 + 14.0, r["ip"], ha="center", fontsize=6.8, color=INK2, zorder=4)
            ax.text(x + nw / 2, y0 + 12.4, f"{r['mem_limit_MB']} MB · cache {r['wt_cache_GB']} GB", ha="center",
                    fontsize=6.6, color=INK2, zorder=4)
            ax.text(x + nw / 2, y0 + 10.6, f"oplog {r['oplog_MB']}".replace("default (5% disk)", "default"),
                    ha="center", fontsize=6.4, color=MUTED, zorder=4)
        ax.text(x0 + 0.8, y0 + 6.5, "replication: primary → secondaries (oplog)\nelection timeout 10 s · "
                "heartbeat 2 s\nvolumes: " + ", ".join(rows[n]["volume"].replace("mongolab_", "")
                                                      for n in REPLSETS[rs]),
                fontsize=7.4, va="top", color=INK2, zorder=3, linespacing=1.3)
        _arrow(ax, (mx + 6.5, my), (x0 + sw / 2, y0 + 25), SHARD_COL[rs], label="reads / writes")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)


def main() -> None:
    env = gather()
    section("E0 host and Docker VM")
    h, d = env["host"], env["docker"]
    print_table([{"item": "host OS", "value": h["os"]}, {"item": "host RAM", "value": f"{h['ram_GB']} GB"},
                 {"item": "Python", "value": h["python"]},
                 {"item": "Docker engine", "value": f"{d['server']} ({d['os']})"},
                 {"item": "Docker VM", "value": f"{d['vm_cpus']} vCPU, {d['vm_mem_GB']} GB, kernel {d['kernel']}"},
                 {"item": "MongoDB", "value": env["mongodb"]},
                 {"item": "mongos host port", "value": env["mongos_host_port"]},
                 {"item": "network", "value": env["network"]}])
    section("E0 containers (from docker inspect)")
    print_table(env["containers"])
    total = sum(r["mem_limit_MB"] for r in env["containers"])
    print(f"\nsum of container memory limits: {total} MB ({total / 1024:.1f} GB); "
          "actual use is measured in E1 (docker stats)")
    save_json(OUT / "environment.json", env)
    with open(OUT / "containers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(env["containers"][0].keys()))
        w.writeheader()
        w.writerows(env["containers"])
    (OUT / "img").mkdir(exist_ok=True)
    architecture(env, OUT / "img" / "e00_architecture.png")
    log(f"E0 done -> {OUT}")


if __name__ == "__main__":
    with Tee(OUT / "console.txt"):
        main()
    export.export("e00")
