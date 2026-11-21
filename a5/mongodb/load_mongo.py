"""KB1.1 - Thu thập dữ liệu dân cư: nạp toàn bộ hokhau.csv + nhankhau.csv vào MongoDB.

Each household becomes ONE document (giống một cuốn sổ hộ khẩu) with its members embedded:
  { _id: SERIAL, SERIAL, GEO1_VN, ..., nhankhau: [ {PERNUM, AGE, SEX, ...}, ... ] }
Field names are kept identical to the MySQL columns so queries are easy to compare.

Usage (from the repo root, after running split_ipums_csv.py):
  python a5/mongodb/load_mongo.py                 # full load (~1M households, 3.55M people)
  python a5/mongodb/load_mongo.py --limit 5000    # small test load
"""
import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import (  # noqa: E402
    DATA_DIR, HOUSEHOLD_COLUMNS, PERSON_COLUMNS, convert, mongo_db,
)

BATCH = 1000

# Same indexes as MySQL after q4_index_explain.sql (+ the migration report index).
INDEXES = [
    ([("GEO1_VN", 1)], "idx_geo1_vn"),
    ([("nhankhau.AGE", 1)], "idx_nhankhau_age"),
    ([("nhankhau.MIGRATE5", 1)], "idx_nhankhau_migrate5"),
]


def read_households(limit):
    with (DATA_DIR / "hokhau.csv").open(newline="", encoding="utf-8") as hf, \
            (DATA_DIR / "nhankhau.csv").open(newline="", encoding="utf-8") as pf:
        people = csv.DictReader(pf)
        person = next(people, None)
        for count, household in enumerate(csv.DictReader(hf)):
            if limit and count >= limit:
                return
            serial = int(household["SERIAL"])
            doc = {"_id": serial}
            doc.update({c: convert(c, household[c]) for c in HOUSEHOLD_COLUMNS})
            members = []
            # Both files are sorted by SERIAL (the splitter checks this).
            while person is not None and int(person["SERIAL"]) == serial:
                members.append({c: convert(c, person[c]) for c in PERSON_COLUMNS})
                person = next(people, None)
            if len(members) != doc["PERSONS"]:
                raise ValueError(f"SERIAL {serial}: {len(members)} people, PERSONS={doc['PERSONS']}")
            doc["nhankhau"] = members
            yield doc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="only load the first N households")
    parser.add_argument("--db", default=None, help="database name (default: MONGO_DB or ipums_vn2019)")
    args = parser.parse_args()

    client, db = mongo_db(args.db)
    db.drop_collection("hokhau")
    db.drop_collection("biendong_cutru")
    coll = db.hokhau

    start = time.perf_counter()
    batch, households, people = [], 0, 0
    for doc in read_households(args.limit):
        batch.append(doc)
        households += 1
        people += len(doc["nhankhau"])
        if len(batch) == BATCH:
            coll.insert_many(batch, ordered=False)
            batch.clear()
            if households % 100_000 == 0:
                print(f"  {households:,} hộ ...", flush=True)
    if batch:
        coll.insert_many(batch, ordered=False)
    load_s = time.perf_counter() - start

    start = time.perf_counter()
    for keys, name in INDEXES:
        coll.create_index(keys, name=name)
    db.biendong_cutru.create_index([("SERIAL", 1)], name="idx_serial")
    index_s = time.perf_counter() - start

    stats = db.command("collStats", "hokhau")
    print(f"Nạp {households:,} hộ / {people:,} nhân khẩu trong {load_s:.1f} s")
    print(f"Tạo index trong {index_s:.1f} s")
    print(f"Dung lượng: data {stats['storageSize'] / 2**20:.0f} MB, "
          f"index {stats['totalIndexSize'] / 2**20:.0f} MB")
    client.close()


if __name__ == "__main__":
    main()
