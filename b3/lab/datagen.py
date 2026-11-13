"""Synthetic shipwreck generator, sampled from the real sample_geospatial.shipwrecks data.

Each generated document:
  * picks a random real wreck (uniformly, so dense real areas stay dense -> skewed geography)
  * copies its categorical fields (chart, feature_type, watlev, quasou, sounding_type,
    gp_quality, history, vesslterms) so their joint distribution is the real one
  * jitters its position by ~5 km (gaussian, sigma 0.05 deg) -> coordinates, latdec, londec, location
  * depth: 0 stays 0 (77% of real docs), otherwise real value +-10%
  * recrd: SYNTHETIC record date 'YYYYMMDD' (1970-2024). In the source data recrd is '' in
    100% of documents, which would make the {region, recrd} shard key degenerate to {region}.
  * ts: strictly increasing timestamp (start + i seconds)  -> monotonic shard key
  * region: named sea area of the source wreck (bounding boxes below)
  * _id: deterministic ObjectId built from ts and i, so all 4 collections hold identical documents

    python -m lab.datagen --docs 5 --show 3      # preview using the local reference set
"""
from __future__ import annotations

import argparse
import random
import struct
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from bson import ObjectId

START_TS = datetime(2025, 1, 1, tzinfo=timezone.utc)
COPY_FIELDS = ["chart", "feature_type", "watlev", "quasou", "sounding_type", "gp_quality", "history",
               "vesslterms"]
_RECRD_DAYS = (date(2024, 12, 31) - date(1970, 1, 1)).days

# (name, lon_min, lon_max, lat_min, lat_max) - first match wins
REGION_BOXES = [
    ("great_lakes", -93.0, -75.0, 41.0, 49.5),
    ("gulf_of_mexico", -98.0, -80.5, 18.0, 31.0),
    ("caribbean", -90.0, -58.0, 5.0, 24.0),
    ("us_east_coast", -82.0, -60.0, 24.0, 47.0),
    ("us_west_coast", -130.0, -115.0, 30.0, 50.0),
    ("alaska_bering", -180.0, -125.0, 50.0, 75.0),
    ("alaska_bering", 165.0, 180.0, 50.0, 75.0),
    ("pacific_islands", -180.0, -140.0, -30.0, 50.0),
    ("pacific_islands", 120.0, 180.0, -30.0, 50.0),
]


def region_of(lon: float, lat: float) -> str:
    for name, x0, x1, y0, y1 in REGION_BOXES:
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return name
    return "other"


class Generator:
    def __init__(self, ref_docs: list[dict], seed: int = 42):
        if not ref_docs:
            raise ValueError("empty reference set - run `python -m lab.load atlas` first")
        self.seed = seed
        self.ref = []
        for d in ref_docs:
            lon, lat = d["coordinates"][0], d["coordinates"][1]
            self.ref.append(({f: d.get(f, "") for f in COPY_FIELDS}, float(lon), float(lat),
                             d.get("depth", 0) or 0, region_of(lon, lat)))

    def docs(self, n: int, batch: int = 1000):
        """Yield lists of `batch` documents; same seed -> identical documents."""
        rng = random.Random(self.seed)
        out = []
        for i in range(n):
            attrs, lon0, lat0, depth0, region = rng.choice(self.ref)
            lon = lon0 + rng.gauss(0, 0.05)
            lat = min(89.9, max(-89.9, lat0 + rng.gauss(0, 0.05)))
            lon = ((lon + 180.0) % 360.0) - 180.0
            lon, lat = round(lon, 6), round(lat, 6)
            depth = 0 if depth0 == 0 else round(max(0.0, depth0 * rng.uniform(0.9, 1.1)), 1)
            ts = START_TS + timedelta(seconds=i)
            oid = ObjectId(struct.pack(">I", int(ts.timestamp())) + b"lab01" + (i & 0xFFFFFF).to_bytes(3, "big"))
            doc = {
                "_id": oid,
                "recrd": (date(1970, 1, 1) + timedelta(days=rng.randrange(_RECRD_DAYS))).strftime("%Y%m%d"),
                "vesslterms": attrs["vesslterms"],
                "feature_type": attrs["feature_type"],
                "chart": attrs["chart"],
                "latdec": lat,
                "londec": lon,
                "gp_quality": attrs["gp_quality"],
                "depth": depth,
                "sounding_type": attrs["sounding_type"],
                "history": attrs["history"],
                "quasou": attrs["quasou"],
                "watlev": attrs["watlev"],
                "coordinates": [lon, lat],
                "ts": ts,
                "region": region,
                "location": {"type": "Point", "coordinates": [lon, lat]},
            }
            out.append(doc)
            if len(out) >= batch:
                yield out
                out = []
        if out:
            yield out


def main(argv=None) -> None:
    import json

    from .common import DB_NAME, mongos_client
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=20000)
    ap.add_argument("--show", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    with mongos_client() as c:
        ref = list(c[DB_NAME].shipwrecks_ref.find({}, {f: 1 for f in COPY_FIELDS + ["coordinates", "depth"]}))
    g = Generator(ref, a.seed)
    real = Counter(r[4] for r in g.ref)
    gen = Counter()
    shown = 0
    for b in g.docs(a.docs):
        for d in b:
            gen[d["region"]] += 1
            if shown < a.show:
                print(json.dumps(d, default=str, indent=1))
                shown += 1
    print(f"\n{'region':<16} {'real':>7} {'real%':>6} {'generated':>10} {'gen%':>6}")
    for r, cnt in real.most_common():
        print(f"{r:<16} {cnt:>7} {100 * cnt / len(g.ref):>5.1f}% {gen[r]:>10} {100 * gen[r] / a.docs:>5.1f}%")


if __name__ == "__main__":
    main()
