"""Fault injection: thin wrappers around the docker CLI. Every action returns its wall-clock time.

    python -m lab.chaos kill s1a
    python -m lab.chaos stop s1a [--timeout 10]
    python -m lab.chaos start s1a
    python -m lab.chaos pause s1a | unpause s1a
    python -m lab.chaos disconnect s1a | connect s1a
    python -m lab.chaos netem s1a --delay 100 [--jitter 10] [--loss 0]   (sidecar with NET_ADMIN)
    python -m lab.chaos netem-clear s1a
    python -m lab.chaos primary shard1RS          # print the current primary of a replica set
    python -m lab.chaos restore                   # undo everything: unpause, reconnect, start, clear netem
"""
from __future__ import annotations

import argparse
import sys
import time

from .common import REPLSETS, container_state, docker, hello, log

NETWORK = "mongolab_net"
NETEM_IMAGE = "nicolaka/netshoot:v0.13"   # has tc/iproute2; runs inside the target's network namespace


def _act(desc: str, *args: str, timeout: float = 120) -> float:
    t = time.time()
    docker(*args, timeout=timeout)
    log(f"chaos: {desc}")
    return t


def kill(name: str, signal: str = "KILL") -> float:
    return _act(f"docker kill -s {signal} {name}", "kill", "-s", signal, name)


def stop(name: str, timeout: int = 10) -> float:
    """SIGTERM, then SIGKILL after `timeout` s. mongod handles SIGTERM with a clean shutdown/step-down."""
    return _act(f"docker stop -t {timeout} {name}", "stop", "-t", str(timeout), name, timeout=timeout + 60)


def start(name: str) -> float:
    return _act(f"docker start {name}", "start", name)


def pause(name: str) -> float:
    return _act(f"docker pause {name}", "pause", name)


def unpause(name: str) -> float:
    return _act(f"docker unpause {name}", "unpause", name)


def disconnect(name: str, network: str = NETWORK) -> float:
    return _act(f"docker network disconnect {network} {name}", "network", "disconnect", network, name)


def connect(name: str, network: str = NETWORK) -> float:
    return _act(f"docker network connect --alias {name} {network} {name}",
                "network", "connect", "--alias", name, network, name)


def is_connected(name: str, network: str = NETWORK) -> bool:
    out = docker("inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", name, check=False)
    return network in out.split()


def _tc(name: str, *tc_args: str) -> str:
    return docker("run", "--rm", "--net", f"container:{name}", "--cap-add", "NET_ADMIN", NETEM_IMAGE,
                  "tc", *tc_args, check=False, timeout=300)


def netem(name: str, delay_ms: int = 100, jitter_ms: int = 0, loss_pct: float = 0.0, dev: str = "eth0") -> float:
    t = time.time()
    _tc(name, "qdisc", "del", "dev", dev, "root")
    args = ["qdisc", "add", "dev", dev, "root", "netem", "delay", f"{delay_ms}ms"]
    if jitter_ms:
        args.append(f"{jitter_ms}ms")
    if loss_pct:
        args += ["loss", f"{loss_pct}%"]
    out = docker("run", "--rm", "--net", f"container:{name}", "--cap-add", "NET_ADMIN", NETEM_IMAGE, "tc", *args,
                 timeout=300)
    log(f"chaos: netem on {name}: delay={delay_ms}ms jitter={jitter_ms}ms loss={loss_pct}% {out}")
    return t


def netem_show(name: str, dev: str = "eth0") -> str:
    return _tc(name, "qdisc", "show", "dev", dev)


def netem_clear(name: str, dev: str = "eth0") -> float:
    t = time.time()
    _tc(name, "qdisc", "del", "dev", dev, "root")
    log(f"chaos: netem cleared on {name}")
    return t


def primary(rs: str) -> str | None:
    for n in REPLSETS[rs]:
        h = hello(n)
        if h and h.get("isWritablePrimary"):
            return n
    return None


def restore(names: list[str] | None = None) -> None:
    """Bring every lab container back to running + connected. Does not wait for health (see cluster)."""
    if names is None:
        names = [n for rs in REPLSETS.values() for n in rs] + ["mongos"]
    for n in names:
        st = container_state(n)
        if st == "missing":
            continue
        if st.startswith("paused"):
            unpause(n)
        elif not st.startswith("running"):
            start(n)
        if not is_connected(n):
            connect(n)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["kill", "stop", "start", "pause", "unpause", "disconnect", "connect",
                                       "netem", "netem-clear", "netem-show", "primary", "restore"])
    ap.add_argument("target", nargs="?")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--delay", type=int, default=100)
    ap.add_argument("--jitter", type=int, default=0)
    ap.add_argument("--loss", type=float, default=0.0)
    a = ap.parse_args(argv)
    f = {"kill": kill, "start": start, "pause": pause, "unpause": unpause, "disconnect": disconnect,
         "connect": connect, "netem-clear": netem_clear}
    if a.action in f:
        f[a.action](a.target)
    elif a.action == "stop":
        stop(a.target, a.timeout)
    elif a.action == "netem":
        netem(a.target, a.delay, a.jitter, a.loss)
    elif a.action == "netem-show":
        print(netem_show(a.target))
    elif a.action == "primary":
        print(primary(a.target))
    elif a.action == "restore":
        restore()
    return 0


if __name__ == "__main__":
    sys.exit(main())
