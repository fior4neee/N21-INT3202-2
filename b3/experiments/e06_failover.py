"""E6 - Failover of a shard primary under write load: docker kill vs docker stop vs docker pause.

For each method, on the CURRENT primary of shard1RS:
  writer.py (1 write / 100 ms through mongos, retryWrites=False, alternating shard1RS / shard2RS)
  RolePoller (hello on s1a/s1b/s1c every 0.25 s) + frame recorder (roles + docker state every 0.5 s)
  baseline 8 s -> FAULT -> wait for a new primary -> wait for writes to resume -> 5 s ->
  RESTORE (docker start / docker unpause) -> wait until the old node is back as SECONDARY -> 5 s
Measured: election time (fault -> new primary seen), write outage (first failed shard1 write -> first
success after the last failure), failed writes + error types, rejoin time, shard2RS control.
Diagrams: one per observed step (normal / fault / no primary / new primary / writes resumed / rejoined).
The cluster is verified healthy at the end.

Output results/e06/<method>/ writer.csv, roles.csv, frames.json; results/e06/summary.csv;
img/e06_<method>_stepN.png, img/e06_<method>_storyboard.png, img/e06_<method>_timeline.png,
img/e06_chart_compare.png
"""
import copy
import csv
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import chaos, diagram, export, plots, timeline  # noqa: E402
from lab.cluster import print_table, wait_healthy_cluster  # noqa: E402
from lab.common import REPLSETS, Tee, container_state, log, results_dir, save_json, section  # noqa: E402
from lab.poller import RolePoller  # noqa: E402
from lab.writer import Writer, summarize  # noqa: E402

OUT = results_dir("e06")
SHARD = "shard1RS"
NODES = REPLSETS[SHARD]
METHODS = ["kill", "stop", "pause"]
FAULT = {"kill": chaos.kill, "stop": chaos.stop, "pause": chaos.pause}
RESTORE = {"kill": chaos.start, "stop": chaos.start, "pause": chaos.unpause}
VERB = {"kill": "killed (SIGKILL)", "stop": "stopped (SIGTERM, clean shutdown)", "pause": "paused (cgroup freeze)"}


class Frames:
    """Every 0.5 s: shard1 roles (from the poller) + docker state of the shard1 containers."""

    def __init__(self, poller):
        self.poller, self.rows = poller, []
        self._stop = threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            t = time.time()
            self.rows.append({"t": t, "roles": self.poller.roles(), "docker": {n: container_state(n) for n in NODES}})
            self._stop.wait(max(0.0, t + 0.5 - time.time()))

    def start(self):
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()
        return self

    def stop(self):
        self._stop.set()
        self.th.join(timeout=5)

    def at(self, t):
        """last frame at or before t (or the first one after if none)."""
        before = [f for f in self.rows if f["t"] <= t]
        return before[-1] if before else self.rows[0]


def log_events(node: str, t_from: float, t_to: float, patterns: dict) -> dict:
    """First epoch time of each pattern in the node's mongod JSON log between t_from and t_to.
    patterns: {key: substring of the 'msg' field}."""
    import json as _json
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    from lab.common import run
    iso = lambda e: _dt.fromtimestamp(e, _tz.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # noqa: E731
    cp = run(["docker", "logs", "--since", iso(t_from), "--until", iso(t_to), node], check=False, timeout=60)
    found = {}
    for line in (cp.stdout + cp.stderr).splitlines():
        try:
            j = _json.loads(line)
        except ValueError:
            continue
        for k, p in patterns.items():
            if k not in found and p in j.get("msg", ""):
                # mongod logs use the Docker VM clock; convert to the Windows clock used for t_fault
                found[k] = _dt.fromisoformat(j["t"]["$date"].replace("Z", "+00:00")).timestamp() - CLOCK["offset"]
    return found


CLOCK = {"offset": 0.0}


def measure_clock_offset() -> float:
    """Docker VM clock minus Windows clock (median of 5 `docker exec date` round trips)."""
    import statistics as _st
    import subprocess
    vals = []
    for _ in range(5):
        t0 = time.time()
        out = subprocess.run(["docker", "exec", "s2a", "date", "+%s.%N"], capture_output=True, text=True).stdout
        t1 = time.time()
        vals.append(float(out) - (t0 + t1) / 2)
    CLOCK["offset"] = _st.median(vals)
    return CLOCK["offset"]


NEW_PRIMARY_LOG = {"election_started": "Starting an election", "election_won": "Election succeeded",
                   "catchup_exited": "Exited primary catch-up mode",
                   "writable": "Transition to primary complete; database writes are now permitted"}
OLD_PRIMARY_LOG = {"signal": "Received signal", "stepdown": "Stepping down the ReplicationCoordinator for shutdown",
                   "quiesce": "Entering quiesce mode for shutdown"}


def write_status(rows, shard, t, window=1.5):
    w = [r for r in rows if r["target"] == shard and t - window < float(r["t_epoch"]) <= t]
    if not w:
        return None
    return "fail" if any(not r["ok"] for r in w) else "ok"


def state_from_frame(base, f):
    st = copy.deepcopy(base)
    for n in NODES:
        dk = f["docker"][n]
        role = f["roles"].get(n, "?")
        if dk.startswith(("exited", "dead", "created")):
            role = "DOWN"
        elif dk.startswith("paused"):
            role = "PAUSED"
        st["nodes"][n].update(role=role, docker=dk, connected=True)
    return st


def run_method(method: str) -> dict:
    d = OUT / method
    target = chaos.primary(SHARD)
    others = [n for n in NODES if n != target]
    section(f"E6 {method.upper()}: docker {method} on {target} (current primary of {SHARD})")
    base = diagram.snapshot(inside_fallback=False)
    poller = RolePoller(NODES, 0.25).start()
    frames = Frames(poller).start()
    writer = Writer(d / "writer.csv").start()
    time.sleep(8)
    # run the fault command in a thread: `docker stop` blocks until the container has exited (up to its
    # 10 s timeout), and the first E6 run measured that blocking time instead of the failover
    done = {}
    th = threading.Thread(target=lambda: (FAULT[method](target), done.setdefault("t", time.time())))
    t_fault = time.time()
    th.start()
    t_elect, roles = poller.wait_for(lambda r: any(r[n] == "PRIMARY" for n in others), 90)
    th.join(timeout=60)
    t_action_done = done.get("t", time.time())
    exit_code = None
    if method in ("kill", "stop"):
        from lab.common import docker as _docker
        exit_code = _docker("inspect", "-f", "{{.State.ExitCode}}", target, check=False)
    new_primary = next((n for n in others if roles.get(n) == "PRIMARY"), None)
    log(f"new primary {new_primary} seen at +{(t_elect or time.time()) - t_fault:.2f} s")
    # wait for the first successful shard1 write after the election
    t_resume = None
    end = time.time() + 60
    while time.time() < end and t_resume is None:
        ok_after = [r for r in writer.rows if r["target"] == SHARD and r["ok"] and float(r["t_epoch"]) > t_fault
                    and any((not x["ok"]) and x["target"] == SHARD for x in writer.rows)]
        fails = [r for r in writer.rows if r["target"] == SHARD and not r["ok"]]
        if fails:
            after = [r for r in ok_after if float(r["t_epoch"]) > float(fails[-1]["t_epoch"])]
            if after and t_elect and float(after[0]["t_epoch"]) > t_elect - 1:
                t_resume = float(after[0]["t_epoch"])
        elif t_elect and time.time() > t_elect + 3:
            break  # no failures at all
        time.sleep(0.2)
    time.sleep(5)
    t_restore = time.time()
    RESTORE[method](target)
    t_rejoin, _ = poller.wait_for(lambda r: r[target] == "SECONDARY"
                                  and sum(v == "PRIMARY" for v in r.values()) == 1, 180)
    log(f"{target} back as SECONDARY at +{(t_rejoin or time.time()) - t_restore:.2f} s after restore")
    time.sleep(5)
    wrows = writer.stop()
    poller.stop()
    frames.stop()
    poller.save(d / "roles.csv")
    save_json(d / "frames.json", frames.rows)

    # ---- analysis
    s1 = summarize(wrows, SHARD)
    s2 = summarize(wrows, "shard2RS")
    after_restore = [r for r in poller.rows if r["t_epoch"] >= t_restore]
    multi = {}
    for r in after_restore:
        multi.setdefault(round(r["t_epoch"] * 4) / 4, set())
        if r["role"] == "PRIMARY":
            multi[round(r["t_epoch"] * 4) / 4].add(r["node"])
    two_prim = sorted(t for t, v in multi.items() if len(v) > 1)
    terms = sorted({r["term"] for r in poller.rows if r["term"] is not None})
    t_first_fail = min((float(r["t_epoch"]) for r in wrows if r["target"] == SHARD and not r["ok"]), default=None)
    lg = log_events(new_primary, t_fault - 1, t_fault + 90, NEW_PRIMARY_LOG) if new_primary else {}
    lo = log_events(target, t_fault - 1, t_fault + 20, OLD_PRIMARY_LOG) if method == "stop" else {}
    rel = lambda e: round(e - t_fault, 3) if e else None  # noqa: E731
    res = {"method": method, "target": target, "new_primary": new_primary,
           "t_fault_epoch": round(t_fault, 3), "docker_cmd_returned_s": round(t_action_done - t_fault, 2),
           "old_exit_code": exit_code,
           "log_election_started_s": rel(lg.get("election_started")), "log_election_won_s": rel(lg.get("election_won")),
           "log_catchup_s": round(lg["catchup_exited"] - lg["election_won"], 3)
           if lg.get("catchup_exited") and lg.get("election_won") else None,
           "log_writable_s": rel(lg.get("writable")),
           "old_stepdown_s": rel(lo.get("stepdown")), "old_quiesce_s": rel(lo.get("quiesce")),
           "election_s": round(t_elect - t_fault, 2) if t_elect else None,
           "first_fail_s": round(t_first_fail - t_fault, 2) if t_first_fail else None,
           "writes_resumed_s": round(t_resume - t_fault, 2) if t_resume else None,
           "outage_s": s1.get("outage_s"), "failed_writes": s1["failed"], "writes_shard1": s1["writes"],
           "error_types": "; ".join(s1["error_types"]), "shard2_failed": s2["failed"],
           "rejoin_after_restore_s": round(t_rejoin - t_restore, 2) if t_rejoin else None,
           "terms_seen": "->".join(map(str, terms)), "two_primaries_seen": bool(two_prim)}
    print_table([res], ["method", "target", "new_primary", "election_s", "first_fail_s", "writes_resumed_s",
                        "outage_s", "failed_writes", "shard2_failed", "rejoin_after_restore_s", "terms_seen"])
    print("(election_s = first hello with isWritablePrimary on a new node, polled every 0.25 s)")
    print(f"error types: {res['error_types'] or '-'}")
    errs = {}
    for r in wrows:
        if r["target"] == SHARD and not r["ok"]:
            errs.setdefault(r["error_type"], r["error_msg"])
    for k, v in errs.items():
        print(f"  {k}: {v}")
    print(f"from the new primary's log: election started +{res['log_election_started_s']} s, won "
          f"+{res['log_election_won_s']} s, catch-up {res['log_catchup_s']} s, writable +{res['log_writable_s']} s")
    if method == "stop":
        print(f"old primary log: SIGTERM -> stepdown +{res['old_stepdown_s']} s, quiesce mode +{res['old_quiesce_s']} s; "
              f"docker stop returned after {res['docker_cmd_returned_s']} s, container exit code {exit_code} "
              "(137 = SIGKILL after docker's 10 s timeout)")
    if two_prim:
        print(f"two nodes reported PRIMARY at the same time after restore, at +{two_prim[0] - t_restore:.2f} s")
    res["t_restore_epoch"] = round(t_restore, 3)
    res["terms"] = terms
    save_json(d / "base_state.json", base)
    draw_steps(res, frames.rows, wrows, base, poller.rows)
    wait_healthy_cluster(180)
    log("cluster healthy again")
    return res


def frame_at(rows, t):
    """last frame at or before t (or the first one if none)."""
    before = [f for f in rows if f["t"] <= t]
    return before[-1] if before else rows[0]


def draw_steps(res, frame_rows, wrows, base, poll_rows) -> None:
    """Step diagrams + storyboard + timeline for one method, from saved observations only."""
    method, target, new_primary = res["method"], res["target"], res["new_primary"]
    t_fault, t_restore = res["t_fault_epoch"], res["t_restore_epoch"]
    ab = lambda rel: t_fault + rel if rel is not None else None  # noqa: E731
    t_won = ab(res["log_election_won_s"]) or ab(res["election_s"])
    t_writable = ab(res["election_s"])  # first hello with isWritablePrimary
    t_resume = ab(res["writes_resumed_s"])
    t_rejoin = t_restore + res["rejoin_after_restore_s"] if res["rejoin_after_restore_s"] is not None else None
    term = res["terms"][-1] if res.get("terms") else "?"
    verb = VERB[method].split(" ")[0]
    steps = [("normal operation", frame_at(frame_rows, t_fault - 0.6),
              ["writer: 10 writes/s alternating shard1RS / shard2RS", f"{target} is PRIMARY of {SHARD}"], {})]
    f_fault = frame_at(frame_rows, t_fault + 1.0)
    steps.append((f"docker {method} {target}", f_fault,
                  [f"t=0: docker {method} {target}", f"{target} {VERB[method]}",
                   f"new primary already writable at +{res['election_s']:.2f} s"
                   if t_writable and t_writable <= f_fault["t"] else "no new primary yet"], {target: verb}))
    if t_won and t_won - t_fault > 2.5:
        f_np = frame_at(frame_rows, t_won - 0.6)
        steps.append(("no primary: election pending", f_np,
                      [f"+{f_np['t'] - t_fault:.1f} s: {SHARD} has no primary",
                       "secondaries wait until heartbeats have been missing for the election timeout (10 s)",
                       f"writes to {SHARD} fail; shard2RS unaffected"], {target: verb}))
    if res["log_catchup_s"] and res["log_catchup_s"] > 2:
        f_cu = frame_at(frame_rows, t_won + min(2.0, res["log_catchup_s"] / 2))
        steps.append((f"{new_primary} won the election, in catch-up mode", f_cu,
                      [f"+{res['log_election_won_s']:.2f} s: {new_primary} won the election (term {term})",
                       f"primary catch-up mode for {res['log_catchup_s']:.1f} s: not writable yet",
                       "it waits for heartbeat replies from the other members (lost node: until timeout)",
                       f"hello on {new_primary} still says {f_cu['roles'].get(new_primary, '?')} (observed)",
                       f"writes to {SHARD} still fail"], {target: verb, new_primary: "catch-up"}))
    if t_writable and t_writable - t_fault > 1.5:
        steps.append((f"new primary {new_primary} writable", frame_at(frame_rows, t_writable + 0.4),
                      [f"+{res['election_s']:.2f} s: {new_primary} is PRIMARY and accepts writes (term {term})",
                       f"election won at +{res['log_election_won_s']} s (mongod log)"],
                      {new_primary: f"writable +{res['election_s']:.1f} s", target: verb}))
    if t_resume and (not t_writable or t_resume - t_writable > 1.0):
        steps.append(("writes resumed through mongos", frame_at(frame_rows, t_resume + 0.6),
                      [f"+{res['writes_resumed_s']:.2f} s: first successful write to {SHARD} through mongos",
                       f"write outage {res['outage_s']} s, {res['failed_writes']} failed writes",
                       f"shard2RS failed writes: {res['shard2_failed']}"], {target: verb}))
    if t_rejoin:
        steps.append((f"{target} restored, rejoined as SECONDARY", frame_at(frame_rows, t_rejoin + 0.6),
                      [f"docker {'start' if method != 'pause' else 'unpause'} {target}",
                       f"{target} SECONDARY {res['rejoin_after_restore_s']:.2f} s after restore",
                       f"{new_primary} stays PRIMARY (equal priorities: no takeover)"], {target: "rejoined"}))
    steps.sort(key=lambda s: s[1]["t"])
    for p in (OUT / "img").glob(f"e06_{method}_step*.png"):
        p.unlink()  # our own previous step images (the number of steps can change)
    pngs = []
    for i, (title, f, events, hl) in enumerate(steps, 1):
        t = f["t"]
        notes = {SHARD: {"writes": write_status(wrows, SHARD, t)},
                 "shard2RS": {"writes": write_status(wrows, "shard2RS", t)}}
        pngs.append(diagram.draw(state_from_frame(base, f), OUT / "img" / f"e06_{method}_step{i}.png",
                                 f"E6 {method} - step {i}: {title}",
                                 f"t = {t - t_fault:+.1f} s relative to the fault (observed)",
                                 shard_notes=notes, events=events, highlight={k: v for k, v in hl.items() if k}))
    diagram.storyboard(pngs, OUT / "img" / f"e06_{method}_storyboard.png", cols=2)
    marks = []
    if t_won:
        marks.append((t_won - t_fault, "elected"))
    if t_writable and abs(t_writable - (t_won or 0)) > 1:
        marks.append((t_writable - t_fault, "writable"))
    if t_resume:
        marks.append((t_resume - t_fault, "writes ok"))
    marks.append((t_restore - t_fault, "restore"))
    timeline.draw(OUT / "img" / f"e06_{method}_timeline.png",
                  f"E6 {method}: roles of {SHARD} members and every write (shard2RS = control)",
                  poll_rows, wrows, t_fault, NODES, marks, shards=[SHARD, "shard2RS"])


def replot() -> None:
    """Redraw step diagrams / storyboards / timelines / chart from the saved run (no faults injected)."""
    import json
    results = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    base_now = diagram.snapshot(inside_fallback=False)
    for res in results:
        d = OUT / res["method"]
        frows = json.loads((d / "frames.json").read_text(encoding="utf-8"))
        with open(d / "writer.csv", encoding="utf-8") as f:
            wrows = [dict(r, ok=int(r["ok"]), t_epoch=float(r["t_epoch"]), latency_ms=float(r["latency_ms"]))
                     for r in csv.DictReader(f)]
        with open(d / "roles.csv", encoding="utf-8") as f:
            prows = [dict(r, t_epoch=float(r["t_epoch"]), term=int(r["term"]) if r["term"] else None)
                     for r in csv.DictReader(f)]
        if "t_restore_epoch" not in res:  # older summary: restore = first frame where the container runs again
            prev = None
            for fr in frows:
                st = fr["docker"][res["target"]]
                if fr["t"] > res["t_fault_epoch"] and prev and not prev.startswith("running") and st.startswith("running"):
                    res["t_restore_epoch"] = fr["t"]
                    break
                prev = st
        if "terms" not in res:
            res["terms"] = sorted({r["term"] for r in prows if r["term"] is not None})
        base_file = d / "base_state.json"
        base = json.loads(base_file.read_text(encoding="utf-8")) if base_file.exists() else base_now
        draw_steps(res, frows, wrows, base, prows)
        log(f"redrew {res['method']}")
    compare_chart(results)


def compare_chart(results):
    """Stacked breakdown of each failover from the fault (t=0):
    waiting for the election (-> election won) | catch-up (-> writable) | until mongos routes writes again."""
    import matplotlib.pyplot as plt
    plots.setup()
    fig, ax = plt.subplots(figsize=(11, 3.9))
    segs = [("until election won (detect failure + vote)", "#2a78d6"),
            ("primary catch-up (elected, not writable)", "#eb6834"),
            ("until a write through mongos succeeds", "#1baf7a")]
    for i, r in enumerate(results):
        won = r["log_election_won_s"] or 0
        wr = r["log_writable_s"] or won
        res_ = r["writes_resumed_s"] if r["writes_resumed_s"] is not None else wr
        parts = [won, max(0, wr - won), max(0, res_ - wr)]
        left = 0
        for (lab, col), v in zip(segs, parts):
            ax.barh(i, v, left=left, color=col, height=0.55, label=lab if i == 0 else None)
            if v > 0.6:
                ax.text(left + v / 2, i, f"{v:.1f} s", ha="center", va="center", fontsize=8.5, color="white",
                        fontweight="bold")
            left += v
        ax.text(left + 0.25, i, f"{r['failed_writes']} failed writes", va="center", fontsize=8.5, color=plots.INK2)
    ax.set_yticks(range(len(results)))
    ax.set_yticklabels([f"docker {r['method']}" for r in results])
    ax.invert_yaxis()
    ax.axvline(10, color=plots.INK2, lw=1, ls="--")
    ax.text(10.1, len(results) - 0.45, "electionTimeoutMillis 10 s", fontsize=8, color=plots.INK2)
    ax.set_xlabel("seconds after the fault (election / catch-up times from the mongod log)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=3, fontsize=8.5)
    ax.set_title("E6 - where the failover time goes", loc="left", pad=26)
    plots.save(fig, OUT / "img" / "e06_chart_compare.png")


def main() -> None:
    wait_healthy_cluster(120)
    off = measure_clock_offset()
    print(f"Docker VM clock - Windows clock = {off * 1000:+.0f} ms (log timestamps are corrected by this; "
          "resolution about +-0.1 s)")
    results = []
    try:
        for m in METHODS:
            results.append(run_method(m))
            time.sleep(10)
    finally:
        chaos.restore()
        wait_healthy_cluster(240)
    section("E6 summary (times in s after the fault; log_* from the new primary's mongod log)")
    print_table(results, ["method", "target", "new_primary", "log_election_won_s", "log_catchup_s", "log_writable_s",
                          "writes_resumed_s", "outage_s", "failed_writes", "shard2_failed", "rejoin_after_restore_s"])
    print()
    print_table(results, ["method", "error_types", "docker_cmd_returned_s", "old_exit_code", "terms_seen"])
    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    save_json(OUT / "summary.json", results)
    compare_chart(results)
    from lab.cluster import healthy
    print()
    healthy()
    log(f"E6 done -> {OUT}")


if __name__ == "__main__":
    if "--replot" in sys.argv:
        replot()
    else:
        with Tee(OUT / "console.txt"):
            main()
    export.export("e06")
