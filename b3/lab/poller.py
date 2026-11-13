"""Background replica-set role poller: hello on each node every `interval` s, one thread per node
(so a paused/unreachable node that blocks until its timeout does not delay the others).

rows: t_epoch, node, role (PRIMARY/SECONDARY/OTHER/UNREACHABLE), term (from electionId, primaries only),
      sees_primary (the node's own idea of who is primary)
"""
from __future__ import annotations

import csv
import threading
import time
from pathlib import Path

from .common import direct_client, node_role


def _term(h) -> int | None:
    eid = (h or {}).get("electionId")
    if eid is None:
        return None
    return int(str(eid)[-16:], 16)  # electionId = 7fffffff + term (8 bytes)


class RolePoller:
    def __init__(self, nodes: list[str], interval: float = 0.25, timeout_ms: int = 700):
        self.nodes, self.interval, self.timeout_ms = nodes, interval, timeout_ms
        self.rows: list[dict] = []
        self.latest: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _loop(self, n: str):
        cli = None
        while not self._stop.is_set():
            t = time.time()
            h = None
            try:
                if cli is None:
                    cli = direct_client(n, serverSelectionTimeoutMS=self.timeout_ms, connectTimeoutMS=self.timeout_ms,
                                        socketTimeoutMS=self.timeout_ms)
                h = cli.admin.command("hello")
            except Exception:
                if cli is not None:
                    cli.close()
                cli = None
            row = {"t_epoch": round(t, 3), "node": n, "role": node_role(h), "term": _term(h),
                   "sees_primary": (h or {}).get("primary", "")}
            with self._lock:
                self.rows.append(row)
                self.latest[n] = row
            self._stop.wait(max(0.0, t + self.interval - time.time()))
        if cli is not None:
            cli.close()

    def start(self) -> "RolePoller":
        for n in self.nodes:
            th = threading.Thread(target=self._loop, args=(n,), daemon=True)
            th.start()
            self._threads.append(th)
        return self

    def stop(self) -> list[dict]:
        self._stop.set()
        for th in self._threads:
            th.join(timeout=5)
        return self.rows

    def roles(self) -> dict[str, str]:
        with self._lock:
            return {n: r["role"] for n, r in self.latest.items()}

    def wait_for(self, pred, timeout: float, step: float = 0.05):
        """Block until pred(roles_dict) is truthy; returns (time_epoch, roles) or (None, roles)."""
        end = time.time() + timeout
        while time.time() < end:
            r = self.roles()
            if len(r) == len(self.nodes) and pred(r):
                return time.time(), r
            time.sleep(step)
        return None, self.roles()

    def save(self, path: Path) -> None:  # noqa: D102
        self._save(path)

    def _save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["t_epoch", "node", "role", "term", "sees_primary"])
            w.writeheader()
            w.writerows(sorted(self.rows, key=lambda r: r["t_epoch"]))


class Frames:
    """Every `interval` s: roles from a RolePoller + docker state (+ network attachment) of `nodes`,
    plus any extra values returned by extra() (e.g. an isolated node's own view)."""

    def __init__(self, poller: RolePoller, nodes: list[str], interval: float = 0.5, extra=None):
        from .common import container_state
        self._cs = container_state
        self.poller, self.nodes, self.interval, self.extra = poller, nodes, interval, extra
        self.rows: list[dict] = []
        self._stop = threading.Event()

    def _loop(self):
        from .chaos import is_connected
        while not self._stop.is_set():
            t = time.time()
            dk = {n: self._cs(n) for n in self.nodes}
            row = {"t": t, "roles": self.poller.roles(), "docker": dk,
                   "connected": {n: (is_connected(n) if dk[n].startswith(("running", "paused")) else True)
                                 for n in self.nodes}}
            if self.extra:
                row["extra"] = self.extra()
            self.rows.append(row)
            self._stop.wait(max(0.0, t + self.interval - time.time()))

    def start(self) -> "Frames":
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.th.join(timeout=10)

    def at(self, t: float) -> dict:
        before = [f for f in self.rows if f["t"] <= t]
        return before[-1] if before else self.rows[0]


def state_from_frame(base: dict, f: dict, overrides: dict | None = None) -> dict:
    """Diagram state = base snapshot with the frame's observed roles/docker/network for its nodes."""
    import copy
    st = copy.deepcopy(base)
    for n, dk in f["docker"].items():
        role = f["roles"].get(n, st["nodes"].get(n, {}).get("role", "?"))
        if dk.startswith(("exited", "dead", "created")):
            role = "DOWN"
        elif dk.startswith("paused"):
            role = "PAUSED"
        st["nodes"].setdefault(n, {"rs": ""}).update(role=role, docker=dk,
                                                     connected=f.get("connected", {}).get(n, True))
    for n, role in (overrides or {}).items():
        st["nodes"][n]["role"] = role
    return st
