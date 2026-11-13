"""E7 - Quorum: lose 2 of 3 members of a shard, then 2 of 3 config servers.

Phase A (shard1RS): docker stop the two SECONDARIES of shard1RS. The primary keeps running but can no longer
          see a majority, so it must step down; nothing is copied to another shard. The survivor stays
          SECONDARY: shard1RS becomes read-only (secondary reads) while shard2RS is unaffected.
Phase B (cfgRS):  docker stop two config servers. Data reads/writes keep working with the routing table
          mongos already has; metadata changes (new sharded collection, chunk size, migrations) fail.
Each phase: baseline -> fault -> degraded (probe matrix every 2 s) -> restore (docker start) -> healthy.

Probe matrix (every op bounded by a 3 s client timeout, retryWrites off):
  W shard1 w:majority / W shard1 w:1 / W shard2 w:majority
  R shard1 primary / R shard1 secondaryPreferred / R shard1 nearest / R shard2 primary
  M config write (chunksize=8, unchanged value) / M shardCollection of a NEW collection (phase B only)
  M flushRouterConfig + read (forces mongos to reload routing from the config servers)

Output results/e07/: probes_A.csv, probes_B.csv, roles_*.csv, frames_*.json, summary.json,
img/e07_A_stepN.png, img/e07_B_stepN.png, storyboards, img/e07_matrix_A.png / _B.png
"""
import csv
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymongo  # noqa: E402
from pymongo import ReadPreference  # noqa: E402
from pymongo.write_concern import WriteConcern  # noqa: E402

from lab import chaos, diagram, export, plots  # noqa: E402
from lab.cluster import healthy, print_table, wait_healthy_cluster  # noqa: E402
from lab.common import (DB_NAME, MONGOS_URI, REPLSETS, Tee, log, results_dir, save_json,  # noqa: E402
                        section)
from lab.poller import Frames, RolePoller, state_from_frame  # noqa: E402
from lab.writer import ensure_probe  # noqa: E402

OUT = results_dir("e07")
TIMEOUT = 3.0


class Prober:
    def __init__(self, phase: str):
        self.phase = phase
        self.c = pymongo.MongoClient(MONGOS_URI, retryWrites=False, retryReads=False,
                                     serverSelectionTimeoutMS=3000)
        self.rows = []
        self.seq = 0
        self._stop = threading.Event()
        db = self.c[DB_NAME]
        p = db.probe
        self.ops = [
            ("W shard1 w:majority", lambda: p.insert_one(self._doc(-1))),
            ("W shard1 w:1", lambda: p.with_options(write_concern=WriteConcern(w=1)).insert_one(self._doc(-1))),
            ("W shard2 w:majority", lambda: p.insert_one(self._doc(1))),
            ("R shard1 primary", lambda: p.find_one({"k": {"$lt": 0}})),
            ("R shard1 secondaryPreferred",
             lambda: p.with_options(read_preference=ReadPreference.SECONDARY_PREFERRED).find_one({"k": {"$lt": 0}})),
            ("R shard1 nearest", lambda: p.with_options(read_preference=ReadPreference.NEAREST).find_one({"k": {"$lt": 0}})),
            ("R shard2 primary", lambda: p.find_one({"k": {"$gt": 0}})),
            ("M config write (chunksize)", lambda: self.c.config.settings.update_one(
                {"_id": "chunksize"}, {"$set": {"value": 8}})),
            ("M flushRouterConfig + read", self._flush_read),
        ]
        if phase == "B":
            self.ops.append(("M shardCollection (new coll)", self._new_sharded))

    def _doc(self, sign):
        self.seq += 1
        return {"k": sign * (3 * 10**9 + int(time.time() * 1000) % 10**8 * 10 + self.seq % 10), "src": f"e07{self.phase}"}

    def _flush_read(self):
        self.c.admin.command("flushRouterConfig", f"{DB_NAME}.probe")
        return self.c[DB_NAME].probe.find_one({"k": {"$gt": 0}})

    def _new_sharded(self):
        name = f"{DB_NAME}.e07_ddl_{int(time.time())}"
        self.created = getattr(self, "created", [])
        self.c.admin.command("shardCollection", name, key={"x": 1})
        self.created.append(name)

    def round(self, t0, stage):
        out = []
        for name, fn in self.ops:
            if name.startswith("M shardCollection") and stage != "degraded":
                continue  # would create a new (empty) sharded collection whenever the config RS is healthy
            ts = time.time()
            r = {"t_epoch": round(ts, 3), "t_rel_s": round(ts - t0, 2), "stage": stage, "op": name, "ok": 1, "error": ""}
            try:
                with pymongo.timeout(TIMEOUT):
                    fn()
            except Exception as e:
                code = getattr(e, "code", "")
                msg = (getattr(e, "details", None) or {}).get("errmsg") or str(e)
                r.update(ok=0, error=f"{type(e).__name__}({code}): {msg.split(', full error')[0][:110]}")
            r["latency_ms"] = round((time.time() - ts) * 1000, 1)
            out.append(r)
        self.rows += out
        return out

    def run_bg(self, t0, stage_fn, every=2.0):
        def loop():
            while not self._stop.is_set():
                t = time.time()
                self.round(t0, stage_fn())
                self._stop.wait(max(0.0, t + every - time.time()))
        self.th = threading.Thread(target=loop, daemon=True)
        self.th.start()

    def stop(self):
        self._stop.set()
        self.th.join(timeout=60)
        self.c.close()


def status_at(rows, t, prefix, window=15.0):
    """ok / fail from the most recent probe round (of ops starting with prefix) before t.
    Rounds slow down to ~10 s when several ops time out (3 s each), hence the wide window."""
    w = [r for r in rows if t - window <= r["t_epoch"] <= t + 0.5 and r["op"].startswith(prefix)]
    if not w:
        return None
    last = max(r["t_epoch"] for r in w)
    w = [r for r in w if r["t_epoch"] >= last - 6]
    return "ok" if all(r["ok"] for r in w) else "fail"


def matrix_chart(rows, path, title, marks):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    plots.setup()
    ops = list(dict.fromkeys(r["op"] for r in rows))
    rounds = sorted({round(r["t_rel_s"]) for r in rows if r["op"] == ops[0]})
    fig, ax = plt.subplots(figsize=(13, 0.42 * len(ops) + 1.8))
    for i, op in enumerate(ops):
        for r in (x for x in rows if x["op"] == op):
            ax.barh(i, 2.0, left=r["t_rel_s"] - 1.0, height=0.8, color="#0ca30c" if r["ok"] else "#d03b3b",
                    edgecolor="white", lw=0.6)
    ax.set_yticks(range(len(ops)))
    ax.set_yticklabels(ops, fontsize=8.5)
    ax.invert_yaxis()
    ax.grid(False)
    for t, lab in marks:
        ax.axvline(t, color=plots.INK, lw=1.1, ls="--")
        ax.text(t, -0.8, " " + lab, fontsize=8, color=plots.INK, va="bottom")
    ax.set_xlabel("seconds since the fault")
    ax.legend(handles=[Patch(color="#0ca30c", label="✓ succeeded"), Patch(color="#d03b3b", label="✗ failed / timed out")],
              loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8.5)
    ax.set_title(title, loc="left", pad=18)
    plots.save(fig, path)


def run_phase(phase: str) -> dict:
    rs = "shard1RS" if phase == "A" else "cfgRS"
    nodes = REPLSETS[rs]
    prim = chaos.primary(rs)
    victims = [n for n in nodes if n != prim]
    section(f"E7 phase {phase}: stop 2 of 3 members of {rs} ({', '.join(victims)}); {prim} keeps running")
    base = diagram.snapshot(inside_fallback=False)
    poller = RolePoller(nodes, 0.25).start()
    frames = Frames(poller, nodes, 0.5).start()
    pr = Prober(phase)
    stage = {"v": "baseline"}
    t0 = time.time() + 8  # fault after 8 s of baseline
    pr.run_bg(t0, lambda: stage["v"])
    time.sleep(8)
    t_fault = time.time()
    # 'stopping': docker stop waits up to 10 s while mongod sits in its 15 s quiesce period and is still a
    # replica-set member; in the first E7 run metadata writes (5 x shardCollection) succeeded during it
    stage["v"] = "stopping"
    ths = [threading.Thread(target=chaos.stop, args=(n,)) for n in victims]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    t_stopped = time.time()
    stage["v"] = "degraded"
    t_stepdown, _ = poller.wait_for(lambda r: r[prim] != "PRIMARY", 60)
    log(f"{prim}: {'stepped down to ' + poller.roles()[prim] if t_stepdown else 'still PRIMARY'}"
        + (f" at +{t_stepdown - t_fault:.1f} s" if t_stepdown else ""))
    time.sleep(20)
    t_restore = time.time()
    stage["v"] = "restoring"
    for n in victims:
        chaos.start(n)
    t_primary, roles = poller.wait_for(lambda r: sum(v == "PRIMARY" for v in r.values()) == 1
                                       and all(v in ("PRIMARY", "SECONDARY") for v in r.values()), 120)
    new_prim = next((n for n, v in roles.items() if v == "PRIMARY"), None)
    log(f"{rs} has primary {new_prim} again at +{(t_primary or time.time()) - t_restore:.1f} s after restore")
    stage["v"] = "restored"
    time.sleep(8)
    pr.stop()
    poller.stop()
    frames.stop()
    poller.save(OUT / f"roles_{phase}.csv")
    save_json(OUT / f"frames_{phase}.json", frames.rows)
    with open(OUT / f"probes_{phase}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(pr.rows[0].keys()))
        w.writeheader()
        w.writerows(pr.rows)

    # ---- what worked while degraded
    deg = [r for r in pr.rows if r["stage"] == "degraded" and t_stepdown and r["t_epoch"] > t_stepdown + 1]
    if not deg:
        deg = [r for r in pr.rows if r["stage"] == "degraded"]
    table = []
    for op in dict.fromkeys(r["op"] for r in pr.rows):
        b = [r for r in pr.rows if r["op"] == op and r["stage"] == "baseline"]
        d = [r for r in deg if r["op"] == op]
        a = [r for r in pr.rows if r["op"] == op and r["stage"] == "restored"]
        errs = sorted({r["error"].split(":")[0] for r in d if not r["ok"]})
        table.append({"operation": op, "baseline_ok": f"{sum(r['ok'] for r in b)}/{len(b)}",
                      "degraded_ok": f"{sum(r['ok'] for r in d)}/{len(d)}",
                      "restored_ok": f"{sum(r['ok'] for r in a)}/{len(a)}", "degraded_errors": ", ".join(errs)})
    print()
    print_table(table)
    print("\nexample error messages while degraded:")
    seen = set()
    for r in deg:
        if not r["ok"] and r["op"] not in seen:
            seen.add(r["op"])
            print(f"  {r['op']}: {r['error']}")
    created = getattr(pr, "created", [])
    if created:
        print(f"note: shardCollection succeeded and created {created} (left in place, empty)")
    res = {"phase": phase, "replset": rs, "survivor": prim, "stopped": victims,
           "stepdown_s": round(t_stepdown - t_fault, 2) if t_stepdown else None,
           "stop_cmd_s": round(t_stopped - t_fault, 2),
           "primary_again_after_restore_s": round(t_primary - t_restore, 2) if t_primary else None,
           "new_primary": new_prim, "table": table, "created_collections": created}

    # ---- diagrams
    def notes(t):
        n = {}
        for sh, pfx in (("shard1RS", "W shard1"), ("shard2RS", "W shard2")):
            ws = status_at(pr.rows, t, pfx)
            n[sh] = {"writes": ws}
            if ws == "fail":
                sec = status_at(pr.rows, t, "R shard1 secondaryPreferred") if sh == "shard1RS" else None
                if sec == "ok":
                    n[sh] = {"writes": "readonly", "note": "writes fail; secondary reads still work"}
                else:
                    n[sh] = {"writes": "fail"}
        mw = status_at(pr.rows, t, "M config write")
        return n, ({"writes": "readonly" if mw == "fail" else "ok"} if mw else {})

    steps = [("normal operation", frames.at(t_fault - 1), [f"{prim} is PRIMARY of {rs}", "all probes succeed"], {})]
    steps.append((f"docker stop {' + '.join(victims)}", frames.at(t_stopped + 0.4),
                  [f"t=0: docker stop {', '.join(victims)} (took {t_stopped - t_fault:.1f} s)",
                   f"{prim} is the only running member: 1 of 3 votes, no majority"],
                  {v: "stopped" for v in victims}))
    if t_stepdown:
        steps.append((f"{prim} steps down: {rs} has no primary", frames.at(t_stepdown + 2.5),
                      [f"+{t_stepdown - t_fault:.1f} s: {prim} stepped down to SECONDARY (cannot see a majority)",
                       "no election possible: a majority (2 of 3) is required",
                       "no data moves to another shard; shard2RS keeps working" if phase == "A"
                       else "config metadata is read-only; data routing uses mongos' cached table"],
                      {**{v: "stopped" for v in victims}, prim: "survivor"}))
    steps.append(("members restarted", frames.at(t_restore + 1.0),
                  [f"docker start {', '.join(victims)}"], {v: "starting" for v in victims}))
    if t_primary:
        steps.append((f"majority restored, {new_prim} is PRIMARY", frames.at(t_primary + 1.5),
                      [f"+{t_primary - t_restore:.1f} s after restart: {new_prim} elected PRIMARY",
                       "all probes succeed again"], {new_prim: "primary again"}))
    pngs = []
    for i, (title, f, ev, hl) in enumerate(steps, 1):
        sn, cfg = notes(f["t"])
        st = state_from_frame(base, f)
        pngs.append(diagram.draw(st, OUT / "img" / f"e07_{phase}_step{i}.png",
                                 f"E7 {phase} - step {i}: {title}",
                                 f"t = {f['t'] - t_fault:+.1f} s relative to the fault (observed)",
                                 shard_notes=sn, cfg_note=cfg, events=ev, highlight=hl,
                                 client_label="client  (probe matrix every 2 s)"))
    diagram.storyboard(pngs, OUT / "img" / f"e07_{phase}_storyboard.png", cols=2)
    marks = [(0, "stop 2 members")]
    if t_stepdown:
        marks.append((t_stepdown - t_fault, "step-down"))
    marks.append((t_restore - t_fault, "restart"))
    if t_primary:
        marks.append((t_primary - t_fault, "primary"))
    rows_rel = [dict(r, t_rel_s=r["t_epoch"] - t_fault) for r in pr.rows]
    matrix_chart(rows_rel, OUT / "img" / f"e07_matrix_{phase}.png",
                 f"E7 phase {phase}: which operations work with 2 of 3 {rs} members down", marks)
    wait_healthy_cluster(180)
    log("cluster healthy again")
    return res


def main() -> None:
    wait_healthy_cluster(120)
    from lab.common import mongos_client
    with mongos_client() as c:
        ensure_probe(c)
    results = []
    try:
        for ph in ("A", "B"):
            results.append(run_phase(ph))
            time.sleep(5)
    finally:
        chaos.restore()
        wait_healthy_cluster(240)
    section("E7 summary")
    print_table([{k: v for k, v in r.items() if k not in ("table", "created_collections")} for r in results])
    save_json(OUT / "summary.json", results)
    print()
    healthy()
    log(f"E7 done -> {OUT}")


if __name__ == "__main__":
    with Tee(OUT / "console.txt"):
        main()
    export.export("e07")
