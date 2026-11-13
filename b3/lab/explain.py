"""Helpers to summarise explain('executionStats') output obtained through mongos (find and aggregate)."""
from __future__ import annotations

SCANS = ("COLLSCAN", "IXSCAN", "IDHACK", "EXPRESS_IXSCAN", "EXPRESS_CLUSTERED_IXSCAN", "CLUSTERED_IXSCAN",
         "COUNT_SCAN", "DISTINCT_SCAN", "GEO_NEAR_2DSPHERE", "GEO_NEAR_2D")


def find_stages(node) -> list[str]:
    out = []
    if isinstance(node, dict):
        st = node.get("stage")
        if isinstance(st, str):
            out.append(st)
        for v in node.values():
            out += find_stages(v)
    elif isinstance(node, list):
        for v in node:
            out += find_stages(v)
    return out


def find_key(node, key):
    """First value stored under `key` anywhere in a nested dict/list."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def scan_of(node) -> str:
    stages = find_stages(node)
    hits = [s for s in stages if s in SCANS]
    if "GEO_NEAR_2DSPHERE" in hits:
        return "GEO_NEAR_2DSPHERE"
    return hits[0] if hits else (stages[-1] if stages else "?")


def fmt_filter(f) -> str:
    def v(x):
        if hasattr(x, "isoformat"):
            return f'ISODate("{x.strftime("%Y-%m-%dT%H:%M")}")'
        if type(x).__name__ == "ObjectId":
            return f'ObjectId("…{str(x)[-6:]}")'
        if isinstance(x, dict):
            return "{" + ", ".join(f"{k}: {v(y)}" for k, y in x.items()) + "}"
        if isinstance(x, list):
            return "[" + ", ".join(v(y) for y in x) + "]"
        if isinstance(x, float):
            return f"{x:g}"
        return repr(x).replace("'", '"')
    return v(f)


def summarize_find(ex: dict) -> dict:
    """{plan, per_shard{shard: {scan, keys, docs, returned, ms}}, keys, docs, returned, server_ms}"""
    es = ex["executionStats"]
    per = {}
    for s in es["executionStages"].get("shards", []):
        per[s["shardName"]] = {"scan": scan_of(s.get("executionStages", {})),
                               "keys": s.get("totalKeysExamined", 0), "docs": s.get("totalDocsExamined", 0),
                               "returned": s.get("nReturned", 0), "ms": s.get("executionTimeMillis", 0)}
    return {"plan": ex["queryPlanner"]["winningPlan"]["stage"], "per_shard": per,
            "keys": es.get("totalKeysExamined", 0), "docs": es.get("totalDocsExamined", 0),
            "returned": es.get("nReturned", 0), "server_ms": es.get("executionTimeMillis", 0)}


def summarize_agg(ex: dict) -> dict:
    """Aggregate explain through mongos: one entry per shard under ex['shards']."""
    per = {}
    for name, sh in (ex.get("shards") or {}).items():
        es = find_key(sh, "executionStats") or {}
        per[name] = {"scan": scan_of(sh), "keys": es.get("totalKeysExamined", 0),
                     "docs": es.get("totalDocsExamined", 0), "returned": es.get("nReturned", 0),
                     "ms": es.get("executionTimeMillis", 0)}
    plan = "SINGLE_SHARD" if len(per) == 1 else f"merge on {ex.get('mergeType', '?')}"
    return {"plan": plan, "per_shard": per, "keys": sum(p["keys"] for p in per.values()),
            "docs": sum(p["docs"] for p in per.values()), "returned": sum(p["returned"] for p in per.values()),
            "server_ms": max([p["ms"] for p in per.values()] or [0])}
