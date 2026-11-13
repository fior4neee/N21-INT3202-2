"""E8 - Network partition, split brain and rollback (CAP).

1. P = primary of shard1RS. Inside P's container a mongosh loop (the "isolated client") runs for 60 s:
   every 0.2 s it records P's own hello and inserts one document DIRECTLY into P (lab.probe, k<0),
   alternating writeConcern w:1 and w:majority (wtimeout 800 ms).
   From Windows: RolePoller on all shard1 members, writer.py through mongos (alternating shards).
2. docker network disconnect P  -> P is cut off from the other members AND from mongos.
3. The majority side (2 of 3) elects a new primary after the election timeout; P keeps believing it is
   primary until it notices it cannot reach a majority -> a window where TWO nodes report PRIMARY.
   During that window P still acknowledges w:1 writes; w:majority writes time out.
4. P steps down; we reconnect P. It finds its oplog has diverged and ROLLS BACK the writes the majority
   never saw. They are written to /data/db/rollback/<collection-uuid>/removed.*.bson on P.
5. We list and bsondump the rollback files, and check which acknowledged writes survived.

Output results/e08/: inside.csv (P's own view + its direct writes), roles.csv, writer.csv, frames.json,
rollback/ (copied .bson files + rollback_docs.json), summary.json, img/e08_stepN.png, storyboard, timeline
"""
import csv
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import chaos, diagram, export, plots  # noqa: E402
from lab.cluster import healthy, print_table, wait_healthy_cluster  # noqa: E402
from lab.common import (DB_NAME, REPLSETS, Tee, docker, log, mongos_client, results_dir, run,  # noqa: E402
                        save_json, section)
from lab.poller import Frames, RolePoller, state_from_frame  # noqa: E402
from lab.writer import Writer, ensure_probe  # noqa: E402

OUT = results_dir("e08")
SHARD = "shard1RS"
NODES = REPLSETS[SHARD]
INSIDE_S = 60
RUN = int(time.time()) % 100000

INSIDE_JS = """
const end = Date.now() + %(dur)d * 1000; let i = 0;
const c = db.getSiblingDB('%(db)s').probe;
while (Date.now() < end) {
  const t = Date.now(); let h = null;
  try { h = db.adminCommand({hello: 1}); } catch (e) {}
  const wc = (i %% 2 == 0) ? {w: 1} : {w: 'majority', wtimeout: 800};
  let ok = 0, err = '';
  try { c.insertOne({k: -(4000000000 + %(run)d * 1000 + i), seq: i, src: 'e08-isolated', wc: String(wc.w), t: new Date()},
                    {writeConcern: wc}); ok = 1; }
  catch (e) { err = (e.codeName || '') + ' ' + String(e.message || e).slice(0, 90); }
  print(JSON.stringify({t: t, done: Date.now(), i: i, wp: h ? h.isWritablePrimary : null,
                        sec: h ? h.secondary : null, primary: h ? (h.primary || '') : '', w: String(wc.w), ok: ok, err: err}));
  i++; sleep(200);
}
"""


def clock_offset(node: str) -> float:
    vals = []
    for _ in range(5):
        t0 = time.time()
        out = subprocess.run(["docker", "exec", node, "date", "+%s.%N"], capture_output=True, text=True).stdout
        vals.append(float(out) - (t0 + time.time()) / 2)
    vals.sort()
    return vals[2]


class Inside:
    """Runs INSIDE_JS in P via docker exec and collects its JSON lines (times converted to host clock)."""

    def __init__(self, node: str, offset: float):
        self.node, self.offset, self.rows = node, offset, []
        js = INSIDE_JS % {"dur": INSIDE_S, "db": DB_NAME, "run": RUN}
        self.p = subprocess.Popen(["docker", "exec", node, "mongosh", "--quiet", "--port", "27017", "--eval", js],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  errors="replace")
        self.th = threading.Thread(target=self._read, daemon=True)
        self.th.start()

    def _read(self):
        for line in self.p.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                j = json.loads(line)
            except ValueError:
                continue
            j["t_epoch"] = j["t"] / 1000 - self.offset
            self.rows.append(j)

    def latest(self):
        return self.rows[-1] if self.rows else None

    def role(self):
        r = self.latest()
        if not r:
            return "?"
        return "PRIMARY" if r["wp"] else ("SECONDARY" if r["sec"] else "OTHER")

    def wait(self):
        self.p.wait(timeout=INSIDE_S + 60)
        self.th.join(timeout=10)


def rollback_files(node: str) -> list[str]:
    out = docker("exec", node, "sh", "-c", "find /data/db/rollback -type f 2>/dev/null | sort", check=False)
    return [l for l in out.splitlines() if l.strip()]


def main() -> None:
    wait_healthy_cluster(120)
    with mongos_client() as c:
        ensure_probe(c)
    P = chaos.primary(SHARD)
    others = [n for n in NODES if n != P]
    off = clock_offset(P)
    before_files = set(rollback_files(P))
    section(f"E8 setup: isolate {P} (primary of {SHARD}); majority side = {', '.join(others)}")
    print(f"Docker VM clock - Windows clock = {off * 1000:+.0f} ms; rollback files already on {P}: {len(before_files)}")
    base = diagram.snapshot(inside_fallback=False)
    poller = RolePoller(NODES, 0.25).start()
    inside = Inside(P, off)
    frames = Frames(poller, NODES, 0.5, extra=lambda: {"P_inside_role": inside.role(),
                                                         "P_inside_last": inside.latest()}).start()
    writer = Writer(OUT / "writer.csv").start()
    time.sleep(6)

    section("E8 partition")
    t_cut = time.time()
    chaos.disconnect(P)
    t_new, roles = poller.wait_for(lambda r: any(r[n] == "PRIMARY" for n in others), 60)
    new_p = next((n for n in others if roles.get(n) == "PRIMARY"), None)
    log(f"majority side elected {new_p} at +{(t_new or time.time()) - t_cut:.2f} s")
    t_p_down = None
    end = time.time() + 40
    while time.time() < end:
        if inside.rows and inside.rows[-1]["t_epoch"] > t_cut and not inside.rows[-1]["wp"]:
            t_p_down = next(r["t_epoch"] for r in inside.rows if r["t_epoch"] > t_cut and not r["wp"])
            break
        time.sleep(0.1)
    log(f"isolated {P} stopped reporting PRIMARY at +{(t_p_down or time.time()) - t_cut:.2f} s (its own view)")
    time.sleep(3)

    section("E8 heal")
    t_heal = time.time()
    chaos.connect(P)
    t_rejoin, _ = poller.wait_for(lambda r: r[P] == "SECONDARY", 120)
    log(f"{P} back as SECONDARY at +{(t_rejoin or time.time()) - t_heal:.2f} s after reconnect")
    time.sleep(6)
    wrows = writer.stop()
    inside.wait()
    poller.stop()
    frames.stop()
    poller.save(OUT / "roles.csv")
    save_json(OUT / "frames.json", frames.rows)
    with open(OUT / "inside.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["t_epoch", "t", "done", "i", "wp", "sec", "primary", "w", "ok", "err"])
        w.writeheader()
        w.writerows(inside.rows)

    # ---- analysis
    section("E8 results")
    part = [r for r in inside.rows if t_cut <= r["t_epoch"] <= t_heal]
    ack1 = [r for r in part if r["ok"] and r["w"] == "1"]
    ackm = [r for r in part if r["ok"] and r["w"] == "majority"]
    failm = [r for r in part if not r["ok"] and r["w"] == "majority"]
    fail1 = [r for r in part if not r["ok"] and r["w"] == "1"]
    with mongos_client() as c:
        ks = [-(4000000000 + RUN * 1000 + r["i"]) for r in inside.rows if r["ok"]]
        present = {d["k"] for d in c[DB_NAME].probe.find({"k": {"$in": ks}}, {"k": 1})}
    lost1 = [r for r in ack1 if -(4000000000 + RUN * 1000 + r["i"]) not in present]
    pre = [r for r in inside.rows if r["t_epoch"] < t_cut and r["ok"]]
    pre_lost = [r for r in pre if -(4000000000 + RUN * 1000 + r["i"]) not in present]

    two = None
    if t_new and t_p_down and t_new < t_p_down:
        two = (t_new, t_p_down)
    print_table([
        {"event": f"network disconnect {P}", "t_s": 0.0},
        {"event": f"majority side: {new_p} PRIMARY (poller)", "t_s": round(t_new - t_cut, 2) if t_new else None},
        {"event": f"isolated {P}: stops reporting PRIMARY (own hello)", "t_s": round(t_p_down - t_cut, 2) if t_p_down else None},
        {"event": "TWO PRIMARIES at the same time", "t_s": f"{two[0] - t_cut:.2f} .. {two[1] - t_cut:.2f} "
                                                         f"({two[1] - two[0]:.2f} s)" if two else "not observed"},
        {"event": f"network connect {P}", "t_s": round(t_heal - t_cut, 2)},
        {"event": f"{P} SECONDARY again", "t_s": round(t_rejoin - t_cut, 2) if t_rejoin else None},
    ])
    print()
    print_table([
        {"direct writes to isolated P during partition": "w:1 acknowledged", "count": len(ack1)},
        {"direct writes to isolated P during partition": "w:1 failed", "count": len(fail1)},
        {"direct writes to isolated P during partition": "w:majority acknowledged", "count": len(ackm)},
        {"direct writes to isolated P during partition": "w:majority failed / timed out", "count": len(failm)},
        {"direct writes to isolated P during partition": "w:1 acknowledged but NOT in the collection after healing",
         "count": len(lost1)},
        {"direct writes to isolated P during partition": "writes acknowledged before the partition, missing after",
         "count": len(pre_lost)},
    ])
    if failm:
        print(f"\nexample w:majority error on the isolated primary: {failm[0]['err']}")
    if fail1:
        print(f"example w:1 error on the isolated primary: {fail1[0]['err']}")

    # rollback files
    files = [f for f in rollback_files(P) if f not in before_files]
    rb_dir = OUT / "rollback"
    rb_dir.mkdir(exist_ok=True)
    rb_docs = []
    print(f"\nnew rollback files on {P}:")
    for f in files:
        size = docker("exec", P, "stat", "-c", "%s", f, check=False)
        print(f"  {f}  ({size} bytes)")
        run(["docker", "cp", f"{P}:{f}", str(rb_dir / Path(f).name)], check=False)
        dump = docker("exec", P, "bsondump", "--quiet", f, check=False)
        for line in dump.splitlines():
            try:
                rb_docs.append(json.loads(line))
            except ValueError:
                pass
    save_json(rb_dir / "rollback_docs.json", rb_docs)
    rb_seqs = sorted(d.get("seq", {}).get("$numberInt", d.get("seq")) if isinstance(d.get("seq"), dict) else d.get("seq")
                     for d in rb_docs if d.get("src") == "e08-isolated")
    rb_seqs = [int(s) for s in rb_seqs if s is not None]
    print(f"documents in the new rollback files: {len(rb_docs)} "
          f"({len(rb_seqs)} from the isolated client; all w:1-acknowledged-and-lost are in it: "
          f"{set(r['i'] for r in lost1) <= set(rb_seqs)})")
    if rb_docs:
        print("first rolled-back document:", json.dumps(rb_docs[0])[:240])
    rlog = docker("logs", "--since", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_heal - 2)), P, check=False)
    rlines = []
    for line in rlog.splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if "ollback" in j.get("msg", ""):
            rlines.append(f"{j['t']['$date'][11:23]}  {j['msg']}")
    print(f"\n{P} log lines mentioning rollback ({len(rlines)}):")
    for l in rlines[:14]:
        print("  " + l)
    wsum = {}
    for sh in (SHARD, "shard2RS"):
        rs_ = [r for r in wrows if r["target"] == sh]
        wsum[sh] = {"writes": len(rs_), "failed": sum(1 for r in rs_ if not r["ok"])}
    print(f"\nwriter through mongos during the experiment: {wsum}")
    res = {"isolated": P, "new_primary": new_p, "t_cut": t_cut,
           "elected_s": round(t_new - t_cut, 2) if t_new else None,
           "isolated_stepdown_s": round(t_p_down - t_cut, 2) if t_p_down else None,
           "two_primaries_s": round(two[1] - two[0], 2) if two else 0,
           "heal_s": round(t_heal - t_cut, 2), "rejoin_s": round(t_rejoin - t_cut, 2) if t_rejoin else None,
           "w1_acked": len(ack1), "w1_failed": len(fail1), "wmaj_acked": len(ackm), "wmaj_failed": len(failm),
           "w1_acked_lost": len(lost1), "pre_partition_lost": len(pre_lost), "rollback_files": files,
           "rollback_docs": len(rb_docs), "writer": wsum}
    save_json(OUT / "summary.json", res)

    # ---- diagrams
    def P_role_at(f):
        return (f.get("extra") or {}).get("P_inside_role", "?")

    def acked_until(t):
        return sum(1 for r in part if r["ok"] and r["w"] == "1" and r["t_epoch"] <= t)

    def wnotes(t):
        out = {}
        for sh in (SHARD, "shard2RS"):
            w = [r for r in wrows if r["target"] == sh and t - 2.5 < r["t_epoch"] <= t]
            out[sh] = {"writes": None if not w else ("fail" if any(not r["ok"] for r in w) else "ok")}
        return out
    steps = [("normal operation", frames.at(t_cut - 1), [f"{P} PRIMARY of {SHARD}",
                                                          "isolated-client loop inside P: w:1 / w:majority writes",
                                                          "writer.py through mongos"], {}, {})]
    f2 = frames.at(t_cut + 2.0)
    steps.append((f"network partition: {P} cut off", f2,
                  [f"t=0: docker network disconnect mongolab_net {P}",
                   f"{P} (own view): {P_role_at(f2)} - still accepts w:1 writes ({acked_until(f2['t'])} so far)",
                   "w:majority writes on P time out (no majority reachable)",
                   "mongos cannot reach P: shard1 writes through mongos fail"],
                  {P: "isolated"}, {P: P_role_at(f2)}))
    if two:
        f3 = frames.at(min(two[0] + 0.6, two[1] - 0.1))
        steps.append(("split brain: two nodes report PRIMARY", f3,
                      [f"+{two[0] - t_cut:.1f} s: majority side elected {new_p}",
                       f"{P} still believes it is PRIMARY (own hello) for {two[1] - two[0]:.1f} s more",
                       f"w:1 writes acknowledged by isolated {P} so far: {acked_until(f3['t'])}",
                       "these writes exist only on P"], {P: "isolated, still PRIMARY", new_p: "new PRIMARY"},
                      {P: "PRIMARY"}))
    if t_p_down:
        f4 = frames.at(t_p_down + 1.0)
        steps.append((f"isolated {P} steps down", f4,
                      [f"+{t_p_down - t_cut:.1f} s: {P} sees no majority and steps down",
                       f"w:1 writes it acknowledged while isolated: {len(ack1)}",
                       f"{new_p} is the only PRIMARY"], {P: "isolated, stepped down"}, {P: P_role_at(f4)}))
    f5 = frames.at(t_heal + 1.0)
    steps.append(("partition healed: rollback", f5,
                  [f"docker network connect {P}", f"{P}'s oplog diverged from {new_p}'s",
                   f"{P} rolls back {len(lost1)} acknowledged w:1 writes",
                   f"rolled-back docs saved to /data/db/rollback ({len(rb_docs)} docs)"], {P: "rolling back"}, {}))
    if t_rejoin:
        f6 = frames.at(t_rejoin + 1.5)
        steps.append((f"{P} rejoined as SECONDARY", f6,
                      [f"+{t_rejoin - t_cut:.1f} s: {P} SECONDARY, in sync with {new_p}",
                       f"lost: {len(lost1)} writes that P had acknowledged with w:1",
                       f"w:majority writes acknowledged during the partition: {len(ackm)}"],
                      {P: f"rolled back {len(lost1)} docs"}, {}))
    pngs = []
    for i, (title, f, ev, hl, ov) in enumerate(steps, 1):
        st = state_from_frame(base, f, overrides=ov)
        pngs.append(diagram.draw(st, OUT / "img" / f"e08_step{i}.png", f"E8 - step {i}: {title}",
                                 f"t = {f['t'] - t_cut:+.1f} s relative to the partition (observed; P's role from its own hello)",
                                 shard_notes=wnotes(f["t"]), events=ev, highlight=hl,
                                 client_label="clients: writer.py via mongos + loop inside P"))
    diagram.storyboard(pngs, OUT / "img" / "e08_storyboard.png", cols=2)
    timeline_chart(inside.rows, poller.rows, t_cut, t_heal, P, others, two)
    wait_healthy_cluster(180)
    print()
    healthy()
    log(f"E8 done -> {OUT}")


def timeline_chart(inside_rows, poll_rows, t_cut, t_heal, P, others, two):
    import matplotlib.pyplot as plt
    plots.setup()
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 5.8), sharex=True, gridspec_kw={"height_ratios": [1, 1.2]})
    col = {"PRIMARY": "#2a78d6", "SECONDARY": "#b9d3f2", "UNREACHABLE": "#d6d4ce", "OTHER": "#fab219"}
    series = [(f"{P} (own view)", [(r["t_epoch"], "PRIMARY" if r["wp"] else ("SECONDARY" if r["sec"] else "OTHER"))
                                   for r in inside_rows])]
    for n in [P] + others:
        series.append((f"{n} (seen from host)", [(r["t_epoch"], r["role"]) for r in poll_rows if r["node"] == n]))
    for i, (lab, pts) in enumerate(series):
        pts.sort()
        for (t, role), nxt in zip(pts, pts[1:] + [None]):
            t1 = nxt[0] if nxt else t + 0.25
            a1.barh(i, t1 - t, left=t - t_cut, height=0.6, color=col.get(role, "#eee"), lw=0)
    a1.set_yticks(range(len(series)))
    a1.set_yticklabels([s[0] for s in series], fontsize=8.5)
    a1.invert_yaxis()
    a1.grid(axis="y", visible=False)
    from matplotlib.patches import Patch
    a1.legend(handles=[Patch(color=c, label=k) for k, c in col.items()], loc="upper left", bbox_to_anchor=(1, 1),
              fontsize=8)
    if two:
        for ax in (a1, a2):
            ax.axvspan(two[0] - t_cut, two[1] - t_cut, color="#d03b3b", alpha=0.12, lw=0)
        a1.text(two[0] - t_cut, -0.7, " two PRIMARIES", color="#d03b3b", fontsize=8.5, va="bottom")
    for w, mk, colr in (("1", "o", "#2a78d6"), ("majority", "s", "#eb6834")):
        ok = [(r["t_epoch"] - t_cut, 1 if w == "1" else 0) for r in inside_rows if r["w"] == w and r["ok"]]
        bad = [(r["t_epoch"] - t_cut, 1 if w == "1" else 0) for r in inside_rows if r["w"] == w and not r["ok"]]
        if ok:
            a2.scatter(*zip(*ok), marker=mk, s=16, color=colr, label=f"w:{w} acknowledged")
        if bad:
            a2.scatter(*zip(*bad), marker="x", s=24, color="#d03b3b" if w == "1" else "#9a6a00",
                       label=f"w:{w} failed")
    a2.set_yticks([0, 1])
    a2.set_yticklabels(["w:majority", "w:1"])
    a2.set_ylim(-0.6, 1.6)
    a2.legend(loc="upper left", bbox_to_anchor=(1, 1), fontsize=8)
    a2.set_xlabel("seconds since the partition")
    a2.set_title(f"direct writes from a client inside {P}", loc="left", fontsize=10)
    for ax in (a1, a2):
        ax.axvline(0, color="#d03b3b", lw=1.2)
        ax.axvline(t_heal - t_cut, color=plots.INK2, lw=1, ls="--")
    a1.text(t_heal - t_cut, -0.7, " reconnect", fontsize=8.5, color=plots.INK2, va="bottom")
    a1.set_title(f"E8 - partition of {P}: who believes it is primary, and which writes were acknowledged",
                 loc="left", pad=16)
    plots.save(fig, OUT / "img" / "e08_timeline.png")


if __name__ == "__main__":
    try:
        with Tee(OUT / "console.txt"):
            main()
    finally:
        chaos.restore()
    export.export("e08")
