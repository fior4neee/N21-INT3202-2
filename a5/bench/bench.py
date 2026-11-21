"""So sánh hiệu năng MySQL vs MongoDB theo các kịch bản nghiệp vụ quản lý nhân khẩu.

Each scenario has a MySQL version and a MongoDB version that do the same business
operation on the same data. Every run is timed from the client (including fetching
all result rows), and read scenarios also check that both systems return the same result.

  KB1 Thu thập dữ liệu dân cư   KB1.2 đăng ký hộ mới (KB1.1 = nạp hàng loạt, xem README)
  KB2 Cập nhật dữ liệu dân cư   KB2.1 cập nhật 1 người, KB2.2 cập nhật hàng loạt cả tỉnh
  KB3 Đăng ký / quản lý cư trú  KB3.1 khai sinh, KB3.2 chuyển hộ khẩu, KB3.3 tách hộ,
                                KB3.4 xóa đăng ký thường trú
  KB4 Tra cứu & thống kê        KB4.1-KB4.5

Write scenarios KB1.2 and KB3.x only touch test households (SERIAL >= 900000000000),
which are deleted at the start and end. KB2.x edits real rows and restores them after each run.

Usage (repo root):
  python a5/bench/bench.py                    # all scenarios, both systems
  python a5/bench/bench.py --quick            # fewer runs
  python a5/bench/bench.py --only KB3 KB4.4   # pick scenarios / groups
  python a5/bench/bench.py --explain          # also save execution plans of the reads
"""
import argparse
import copy
import csv
import datetime
import json
import random
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import (  # noqa: E402
    HOUSEHOLD_COLUMNS, PERSON_COLUMNS, RESULTS_DIR, TEST_SERIAL_BASE,
    mongo_db, mongo_supports_transactions, mysql_connect,
)

T = TEST_SERIAL_BASE
HH_COLS = list(HOUSEHOLD_COLUMNS)
P_REST = [c for c in PERSON_COLUMNS if c != "PERNUM"]
NK_COLS = ["SERIAL", "PERNUM"] + P_REST  # same order as the nhankhau table


def cols(names):
    return ", ".join(f"`{c}`" for c in names)


def marks(n):
    return ", ".join(["%s"] * n)


def clean(value):
    return float(value) if isinstance(value, Decimal) else value


# ---------------------------------------------------------------------------
# Connections and helpers
# ---------------------------------------------------------------------------
class Ctx:
    def __init__(self, args):
        from pymongo import WriteConcern
        self.args = args
        self.rng = random.Random(args.seed)
        self.dbs = ["mysql", "mongo"] if args.db == "both" else [args.db]
        self.my = mysql_connect() if "mysql" in self.dbs else None
        self.client = self.mg = None
        self.txn = False
        if "mongo" in self.dbs:
            self.client, self.mg = mongo_db()
            self.txn = mongo_supports_transactions(self.client)
            # j=True: wait for the journal like MySQL's default innodb_flush_log_at_trx_commit=1.
            self.wc = WriteConcern(w=1, j=not args.no_journal)
            self.hk = self.mg.get_collection("hokhau", write_concern=self.wc)
            self.log = self.mg.get_collection("biendong_cutru", write_concern=self.wc)
        self.serial = T + 500_000

    def new_serial(self):
        self.serial += 1
        return self.serial

    def close(self):
        if self.my:
            self.my.close()
        if self.client:
            self.client.close()


def mysql_tx(ctx, fn):
    ctx.my.begin()
    try:
        with ctx.my.cursor() as cur:
            out = fn(cur)
        ctx.my.commit()
        return out
    except BaseException:
        ctx.my.rollback()
        raise


def mongo_tx(ctx, fn):
    """Run fn(session) in a multi-document transaction when the server supports it
    (replica set). On a standalone mongod, fn runs without a transaction."""
    if not ctx.txn:
        return fn(None)
    with ctx.client.start_session() as session:
        return session.with_transaction(fn, write_concern=ctx.wc)


def mysql_log(cur, serial, pernum, loai, lien_quan=None):
    cur.execute(
        "INSERT INTO biendong_cutru (SERIAL, PERNUM, LOAI, SERIAL_LIEN_QUAN) VALUES (%s, %s, %s, %s)",
        (serial, pernum, loai, lien_quan),
    )


def mongo_log_doc(serial, pernum, loai, lien_quan=None):
    return {"SERIAL": serial, "PERNUM": pernum, "LOAI": loai, "SERIAL_LIEN_QUAN": lien_quan,
            "NGAY": datetime.datetime.now(datetime.timezone.utc)}


def mysql_insert_household(cur, doc):
    cur.execute(f"INSERT INTO hokhau ({cols(HH_COLS)}) VALUES ({marks(len(HH_COLS))})",
                [doc[c] for c in HH_COLS])
    members = doc["nhankhau"]
    values = [doc["SERIAL"] if c == "SERIAL" else m[c] for m in members for c in NK_COLS]
    row = f"({marks(len(NK_COLS))})"
    cur.execute(f"INSERT INTO nhankhau ({cols(NK_COLS)}) VALUES {', '.join([row] * len(members))}",
                values)


def test_household(template, serial):
    doc = copy.deepcopy(template)
    doc["_id"] = doc["SERIAL"] = serial
    return doc


# ---------------------------------------------------------------------------
# Sampling real data (done once, untimed, from the first available system)
# ---------------------------------------------------------------------------
def sample_serials(ctx, k, persons=None):
    if ctx.my:
        where = "SERIAL < %s" + (" AND PERSONS = %s" if persons else "")
        params = (T, persons) if persons else (T,)
        with ctx.my.cursor() as cur:
            cur.execute(f"SELECT SERIAL FROM hokhau WHERE {where} ORDER BY RAND(%s) LIMIT %s",
                        params + (ctx.args.seed, k))
            return [r[0] for r in cur.fetchall()]
    match = {"_id": {"$lt": T}}
    if persons:
        match["PERSONS"] = persons
    return [d["_id"] for d in ctx.mg.hokhau.aggregate(
        [{"$match": match}, {"$sample": {"size": k}}, {"$project": {"_id": 1}}])]


def fetch_households(ctx, serials):
    """Households with embedded members, in MongoDB document shape."""
    if ctx.my:
        import pymysql
        with ctx.my.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(f"SELECT * FROM hokhau WHERE SERIAL IN ({marks(len(serials))})", serials)
            docs = {r["SERIAL"]: {**{c: clean(r[c]) for c in HH_COLS}, "nhankhau": []}
                    for r in cur.fetchall()}
            cur.execute(f"SELECT * FROM nhankhau WHERE SERIAL IN ({marks(len(serials))}) "
                        "ORDER BY SERIAL, PERNUM", serials)
            for r in cur.fetchall():
                docs[r["SERIAL"]]["nhankhau"].append({c: clean(r[c]) for c in PERSON_COLUMNS})
        return [docs[s] for s in serials]
    docs = {d["SERIAL"]: d for d in ctx.mg.hokhau.find({"_id": {"$in": serials}}, {"_id": 0})}
    return [docs[s] for s in serials]


def create_pool(ctx, n):
    """n test households with 4 members each, copied from real 4-person households."""
    templates = fetch_households(ctx, sample_serials(ctx, n, persons=4))
    docs = [test_household(t, ctx.new_serial()) for t in templates]
    if ctx.my:
        def insert_all(cur):
            for d in docs:
                mysql_insert_household(cur, d)
        mysql_tx(ctx, insert_all)
    if ctx.mg is not None:
        ctx.hk.insert_many(copy.deepcopy(docs))
    return [d["SERIAL"] for d in docs]


def cleanup(ctx):
    if ctx.my:
        with ctx.my.cursor() as cur:
            cur.execute("DELETE FROM nhankhau WHERE SERIAL >= %s", (T,))
            cur.execute("DELETE FROM hokhau WHERE SERIAL >= %s", (T,))
            cur.execute("DELETE FROM biendong_cutru WHERE SERIAL >= %s", (T,))
    if ctx.mg is not None:
        ctx.hk.delete_many({"_id": {"$gte": T}})
        ctx.log.delete_many({"SERIAL": {"$gte": T}})


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
class Scenario:
    sid = name = ""
    runs = 1
    read = False
    note = ""

    def prepare(self, ctx):
        return [None] * self.runs

    def restore(self, ctx, db, p):
        pass


class Read(Scenario):
    """A read scenario = one SQL query + one aggregation pipeline doing the same thing."""
    read = True
    sql = ""

    def pipeline(self, p):
        raise NotImplementedError

    def sql_params(self, p):
        return p if "%s" in self.sql else None

    def mysql(self, ctx, p):
        with ctx.my.cursor() as cur:
            cur.execute(self.sql, self.sql_params(p))
            return cur.fetchall()

    def mongo(self, ctx, p):
        return list(ctx.mg.hokhau.aggregate(self.pipeline(p), allowDiskUse=True))


class TraCuuHo(Read):
    sid, name, runs = "KB4.1", "Tra cứu 1 hộ + nhân khẩu theo SERIAL (q1)", 50
    sql = ("SELECT h.*, n.* FROM hokhau h JOIN nhankhau n ON n.SERIAL = h.SERIAL "
           "WHERE h.SERIAL = %s ORDER BY n.PERNUM")

    def prepare(self, ctx):
        return [(s,) for s in sample_serials(ctx, self.runs)]

    def pipeline(self, p):
        return [{"$match": {"_id": p[0]}}]

    def norm_mysql(self, rows):
        return [r[len(HH_COLS) + 1] for r in rows]  # PERNUM of each member

    def norm_mongo(self, docs):
        return [m["PERNUM"] for d in docs for m in d["nhankhau"]]


class ViecLamTheoTuoi(Read):
    sid, name, runs = "KB4.2", "Thống kê số người có việc làm theo tuổi (q2)", 5
    sql = "SELECT AGE, COUNT(*) FROM nhankhau WHERE EMPSTAT = 1 GROUP BY AGE ORDER BY AGE"

    def pipeline(self, p):
        return [
            {"$unwind": "$nhankhau"},
            {"$match": {"nhankhau.EMPSTAT": 1}},
            {"$group": {"_id": "$nhankhau.AGE", "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]

    def norm_mysql(self, rows):
        return [tuple(r) for r in rows]

    def norm_mongo(self, docs):
        return [(d["_id"], d["n"]) for d in docs]


class DoTuoiLaoDong(Read):
    sid, name, runs = "KB4.3", "Danh sách người 18-60 tuổi của 1 tỉnh (q3)", 5
    sql = ("SELECT n.*, h.GEO1_VN FROM hokhau h JOIN nhankhau n ON n.SERIAL = h.SERIAL "
           "WHERE h.GEO1_VN = %s AND n.AGE BETWEEN 18 AND 60")

    def prepare(self, ctx):
        return [(ctx.args.province,)] * self.runs

    def pipeline(self, p):
        return [
            {"$match": {"GEO1_VN": p[0], "nhankhau.AGE": {"$gte": 18, "$lte": 60}}},
            {"$unwind": "$nhankhau"},
            {"$match": {"nhankhau.AGE": {"$gte": 18, "$lte": 60}}},
            {"$replaceRoot": {"newRoot": {"$mergeObjects": [
                "$nhankhau", {"SERIAL": "$SERIAL", "GEO1_VN": "$GEO1_VN"}]}}},
        ]

    def norm_mysql(self, rows):
        return sorted((r[0], r[1]) for r in rows)

    def norm_mongo(self, docs):
        return sorted((d["SERIAL"], d["PERNUM"]) for d in docs)


class NhapCu(Read):
    sid, name, runs = "KB4.4", "Người chuyển đến 1 tỉnh trong 5 năm, theo tỉnh nơi đi (q5)", 10
    sql = ("SELECT n.GEOMIG1_5, COUNT(*) AS so_nguoi FROM hokhau h "
           "JOIN nhankhau n ON n.SERIAL = h.SERIAL "
           "WHERE h.GEO1_VN = %s AND n.MIGRATE5 = 20 "
           "GROUP BY n.GEOMIG1_5 ORDER BY so_nguoi DESC, n.GEOMIG1_5")

    def prepare(self, ctx):
        return [(ctx.args.province,)] * self.runs

    def pipeline(self, p):
        return [
            {"$match": {"GEO1_VN": p[0], "nhankhau.MIGRATE5": 20}},
            {"$unwind": "$nhankhau"},
            {"$match": {"nhankhau.MIGRATE5": 20}},
            {"$group": {"_id": "$nhankhau.GEOMIG1_5", "so_nguoi": {"$sum": 1}}},
            {"$sort": {"so_nguoi": -1, "_id": 1}},
        ]

    def norm_mysql(self, rows):
        return sorted(tuple(r) for r in rows)

    def norm_mongo(self, docs):
        return sorted((d["_id"], d["so_nguoi"]) for d in docs)


class ThapDanSo(Read):
    sid, name, runs = "KB4.5", "Tháp dân số: tỉnh x giới tính x nhóm 5 tuổi (q6)", 3
    sql = ("SELECT h.GEO1_VN, n.SEX, LEAST(FLOOR(n.AGE / 5) * 5, 80) AS nhom_tuoi, COUNT(*) "
           "FROM hokhau h JOIN nhankhau n ON n.SERIAL = h.SERIAL "
           "GROUP BY h.GEO1_VN, n.SEX, nhom_tuoi ORDER BY h.GEO1_VN, n.SEX, nhom_tuoi")

    def pipeline(self, p):
        return [
            {"$unwind": "$nhankhau"},
            {"$group": {
                "_id": {
                    "tinh": "$GEO1_VN",
                    "gioi": "$nhankhau.SEX",
                    "nhom": {"$min": [
                        {"$multiply": [{"$floor": {"$divide": ["$nhankhau.AGE", 5]}}, 5]}, 80]},
                },
                "n": {"$sum": 1},
            }},
            {"$sort": {"_id.tinh": 1, "_id.gioi": 1, "_id.nhom": 1}},
        ]

    def norm_mysql(self, rows):
        return sorted((r[0], r[1], int(r[2]), r[3]) for r in rows)

    def norm_mongo(self, docs):
        return sorted((d["_id"]["tinh"], d["_id"]["gioi"], int(d["_id"]["nhom"]), d["n"])
                      for d in docs)


# --- KB1: thu thập ---------------------------------------------------------
class DangKyHoMoi(Scenario):
    sid, name, runs = "KB1.2", "Đăng ký hộ mới (1 hộ + 4 nhân khẩu + ghi biến động)", 100

    def prepare(self, ctx):
        templates = fetch_households(ctx, sample_serials(ctx, self.runs, persons=4))
        return [test_household(t, ctx.new_serial()) for t in templates]

    def mysql(self, ctx, doc):
        def tx(cur):
            mysql_insert_household(cur, doc)
            mysql_log(cur, doc["SERIAL"], None, "DANG_KY_HO_MOI")
        mysql_tx(ctx, tx)
        return 1

    def mongo(self, ctx, doc):
        def tx(s):
            ctx.hk.insert_one(copy.deepcopy(doc), session=s)
            ctx.log.insert_one(mongo_log_doc(doc["SERIAL"], None, "DANG_KY_HO_MOI"), session=s)
        mongo_tx(ctx, tx)
        return 1


# --- KB2: cập nhật -----------------------------------------------------------
class CapNhatCaNhan(Scenario):
    sid, name, runs = "KB2.1", "Cập nhật 1 nhân khẩu: đăng ký kết hôn", 50
    FIELDS = ("MARST", "MARSTD", "AGEMARR", "MARRYR")

    def prepare(self, ctx):
        if ctx.my:
            with ctx.my.cursor() as cur:
                cur.execute(
                    "SELECT SERIAL, PERNUM, AGE, MARST, MARSTD, AGEMARR, MARRYR FROM nhankhau "
                    "WHERE MARST = 1 AND AGE BETWEEN 20 AND 35 AND SERIAL < %s "
                    "ORDER BY RAND(%s) LIMIT %s", (T, ctx.args.seed, self.runs))
                rows = cur.fetchall()
        else:
            rows = [(d["_id"], d["p"]["PERNUM"], d["p"]["AGE"],
                     *(d["p"][f] for f in self.FIELDS))
                    for d in ctx.mg.hokhau.aggregate([
                        {"$match": {"_id": {"$lt": T}}},
                        {"$sample": {"size": self.runs * 20}},
                        {"$unwind": "$nhankhau"},
                        {"$match": {"nhankhau.MARST": 1, "nhankhau.AGE": {"$gte": 20, "$lte": 35}}},
                        {"$limit": self.runs},
                        {"$project": {"p": "$nhankhau"}},
                    ])]
        return [{"serial": r[0], "pernum": r[1],
                 "new": dict(zip(self.FIELDS, (2, 200, r[2], 2019))),
                 "old": dict(zip(self.FIELDS, r[3:7]))} for r in rows]

    @staticmethod
    def _mysql_set(ctx, p, values):
        with ctx.my.cursor() as cur:
            cur.execute("UPDATE nhankhau SET MARST = %s, MARSTD = %s, AGEMARR = %s, MARRYR = %s "
                        "WHERE SERIAL = %s AND PERNUM = %s",
                        (*values.values(), p["serial"], p["pernum"]))
            return cur.rowcount

    @staticmethod
    def _mongo_set(ctx, p, values):
        return ctx.hk.update_one(
            {"_id": p["serial"]},
            {"$set": {f"nhankhau.$[p].{k}": v for k, v in values.items()}},
            array_filters=[{"p.PERNUM": p["pernum"]}],
        ).modified_count

    def mysql(self, ctx, p):
        return self._mysql_set(ctx, p, p["new"])

    def mongo(self, ctx, p):
        return self._mongo_set(ctx, p, p["new"])

    def restore(self, ctx, db, p):
        (self._mysql_set if db == "mysql" else self._mongo_set)(ctx, p, p["old"])


class CapNhatHangLoat(Scenario):
    sid, name, runs = "KB2.2", "Cập nhật hàng loạt: tăng tuổi toàn bộ dân 1 tỉnh", 3
    note = "MySQL đếm số người, MongoDB đếm số hộ (document) được sửa"

    def prepare(self, ctx):
        return [ctx.args.province] * self.runs

    @staticmethod
    def _mysql_age(ctx, province, delta):
        with ctx.my.cursor() as cur:
            cur.execute("UPDATE nhankhau n JOIN hokhau h ON n.SERIAL = h.SERIAL "
                        "SET n.AGE = n.AGE + %s WHERE h.GEO1_VN = %s", (delta, province))
            return cur.rowcount

    @staticmethod
    def _mongo_age(ctx, province, delta):
        return ctx.hk.update_many({"GEO1_VN": province},
                                  {"$inc": {"nhankhau.$[].AGE": delta}}).modified_count

    def mysql(self, ctx, p):
        return self._mysql_age(ctx, p, 1)

    def mongo(self, ctx, p):
        return self._mongo_age(ctx, p, 1)

    def restore(self, ctx, db, p):
        (self._mysql_age if db == "mysql" else self._mongo_age)(ctx, p, -1)


# --- KB3: đăng ký / quản lý cư trú ------------------------------------------
class KhaiSinh(Scenario):
    sid, name, runs = "KB3.1", "Khai sinh: thêm trẻ mới sinh vào hộ", 50

    def prepare(self, ctx):
        return create_pool(ctx, self.runs)

    @staticmethod
    def newborn(ctx):
        # Synthetic newborn; the remaining codes are copied from a fixed real person.
        return {"PERWT": 30.0, "RELATE": 3, "RELATED": 3000, "AGE": 0, "SEX": 1, "MARST": 1,
                "MARSTD": 100, "AGEMARR": 0, "MARRYR": 9999, "BIRTHYR": 2019, "BTHCERT": 9,
                "RELIGION": 1, "RELIGIOND": 1000, "ETHNICVN": 1, "SCHOOL": 0, "LIT": 0,
                "EDATTAIN": 0, "EDATTAIND": 0, "YRSCHOOL": 0, "EDUCVN": 0, "EMPSTAT": 0,
                "EMPSTATD": 0, "LABFORCE": 9, "CLASSWK": 0, "CLASSWKD": 0, "WRKAVAIL": 0,
                "MIGRATE5": 0, "GEOMIG1_5": 0, "MIGCAUSE": 0}

    def mysql(self, ctx, serial):
        baby = self.newborn(ctx)

        def tx(cur):
            cur.execute("SELECT PERSONS FROM hokhau WHERE SERIAL = %s FOR UPDATE", (serial,))
            cur.execute("SELECT COALESCE(MAX(PERNUM), 0) + 1 FROM nhankhau WHERE SERIAL = %s",
                        (serial,))
            pernum = cur.fetchone()[0]
            cur.execute(f"INSERT INTO nhankhau ({cols(NK_COLS)}) VALUES ({marks(len(NK_COLS))})",
                        [serial, pernum] + [baby[c] for c in P_REST])
            cur.execute("UPDATE hokhau SET PERSONS = PERSONS + 1 WHERE SERIAL = %s", (serial,))
            mysql_log(cur, serial, pernum, "KHAI_SINH")
        mysql_tx(ctx, tx)
        return 1

    def mongo(self, ctx, serial):
        from pymongo import ReturnDocument
        baby = self.newborn(ctx)

        def tx(s):
            # One atomic update on one document: append the baby with PERNUM = max + 1.
            doc = ctx.hk.find_one_and_update(
                {"_id": serial},
                [{"$set": {
                    "nhankhau": {"$concatArrays": ["$nhankhau", [{"$mergeObjects": [
                        {"$literal": baby},
                        {"PERNUM": {"$add": [{"$ifNull": [{"$max": "$nhankhau.PERNUM"}, 0]}, 1]}},
                    ]}]]},
                    "PERSONS": {"$add": ["$PERSONS", 1]},
                }}],
                projection={"nhankhau": {"$slice": -1}},
                return_document=ReturnDocument.AFTER,
                session=s,
            )
            ctx.log.insert_one(mongo_log_doc(serial, doc["nhankhau"][-1]["PERNUM"], "KHAI_SINH"),
                               session=s)
        mongo_tx(ctx, tx)
        return 1


class ChuyenHoKhau(Scenario):
    sid, name, runs = "KB3.2", "Chuyển hộ khẩu: chuyển 1 người từ hộ A sang hộ B", 50

    def prepare(self, ctx):
        pool = create_pool(ctx, self.runs * 2)
        return list(zip(pool[0::2], pool[1::2]))

    def mysql(self, ctx, p):
        a, b = p

        def tx(cur):
            cur.execute("SELECT SERIAL FROM hokhau WHERE SERIAL IN (%s, %s) "
                        "ORDER BY SERIAL FOR UPDATE", (a, b))  # fixed lock order: no deadlock
            cur.execute("SELECT MAX(PERNUM) FROM nhankhau WHERE SERIAL = %s", (a,))
            pernum = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(MAX(PERNUM), 0) + 1 FROM nhankhau WHERE SERIAL = %s", (b,))
            new_pernum = cur.fetchone()[0]
            cur.execute(f"INSERT INTO nhankhau ({cols(NK_COLS)}) "
                        f"SELECT %s, %s, {cols(P_REST)} FROM nhankhau "
                        "WHERE SERIAL = %s AND PERNUM = %s", (b, new_pernum, a, pernum))
            cur.execute("DELETE FROM nhankhau WHERE SERIAL = %s AND PERNUM = %s", (a, pernum))
            cur.execute("UPDATE hokhau SET PERSONS = PERSONS - 1 WHERE SERIAL = %s", (a,))
            cur.execute("UPDATE hokhau SET PERSONS = PERSONS + 1 WHERE SERIAL = %s", (b,))
            mysql_log(cur, a, pernum, "CHUYEN_DI", b)
            mysql_log(cur, b, new_pernum, "CHUYEN_DEN", a)
        mysql_tx(ctx, tx)
        return 1

    def mongo(self, ctx, p):
        a, b = p

        def tx(s):
            members = ctx.hk.find_one({"_id": a}, {"nhankhau": 1}, session=s)["nhankhau"]
            person = max(members, key=lambda m: m["PERNUM"])
            pernum = person["PERNUM"]
            target = ctx.hk.find_one({"_id": b}, {"nhankhau.PERNUM": 1}, session=s)["nhankhau"]
            new_pernum = max((m["PERNUM"] for m in target), default=0) + 1
            ctx.hk.update_one({"_id": a}, {"$pull": {"nhankhau": {"PERNUM": pernum}},
                                           "$inc": {"PERSONS": -1}}, session=s)
            ctx.hk.update_one({"_id": b}, {"$push": {"nhankhau": {**person, "PERNUM": new_pernum}},
                                           "$inc": {"PERSONS": 1}}, session=s)
            ctx.log.insert_many([mongo_log_doc(a, pernum, "CHUYEN_DI", b),
                                 mongo_log_doc(b, new_pernum, "CHUYEN_DEN", a)], session=s)
        mongo_tx(ctx, tx)
        return 1


class TachHo(Scenario):
    sid, name, runs = "KB3.3", "Tách hộ: thành viên PERNUM >= 3 lập hộ mới", 50

    def prepare(self, ctx):
        return [(a, ctx.new_serial()) for a in create_pool(ctx, self.runs)]

    def mysql(self, ctx, p):
        a, new = p
        copy_cols = ", ".join("%s" if c in ("SERIAL", "PERSONS") else f"`{c}`" for c in HH_COLS)

        def tx(cur):
            cur.execute("SELECT PERSONS FROM hokhau WHERE SERIAL = %s FOR UPDATE", (a,))
            cur.execute("SELECT COUNT(*) FROM nhankhau WHERE SERIAL = %s AND PERNUM >= 3", (a,))
            k = cur.fetchone()[0]
            serial_first = HH_COLS.index("SERIAL") < HH_COLS.index("PERSONS")
            cur.execute(f"INSERT INTO hokhau ({cols(HH_COLS)}) SELECT {copy_cols} "
                        "FROM hokhau WHERE SERIAL = %s",
                        ((new, k) if serial_first else (k, new)) + (a,))
            cur.execute(f"INSERT INTO nhankhau ({cols(NK_COLS)}) "
                        f"SELECT %s, ROW_NUMBER() OVER (ORDER BY PERNUM), {cols(P_REST)} "
                        "FROM nhankhau WHERE SERIAL = %s AND PERNUM >= 3", (new, a))
            cur.execute("UPDATE nhankhau SET RELATE = 1, RELATED = 1000 "
                        "WHERE SERIAL = %s AND PERNUM = 1", (new,))  # new head of household
            cur.execute("DELETE FROM nhankhau WHERE SERIAL = %s AND PERNUM >= 3", (a,))
            cur.execute("UPDATE hokhau SET PERSONS = PERSONS - %s WHERE SERIAL = %s", (k, a))
            mysql_log(cur, new, None, "TACH_HO", a)
        mysql_tx(ctx, tx)
        return 1

    def mongo(self, ctx, p):
        a, new = p

        def tx(s):
            doc = ctx.hk.find_one({"_id": a}, session=s)
            moving = [m for m in doc["nhankhau"] if m["PERNUM"] >= 3]
            for i, m in enumerate(moving, start=1):
                m["PERNUM"] = i
            moving[0].update(RELATE=1, RELATED=1000)
            new_doc = {**{c: doc[c] for c in HH_COLS}, "_id": new, "SERIAL": new,
                       "PERSONS": len(moving), "nhankhau": moving}
            ctx.hk.insert_one(new_doc, session=s)
            ctx.hk.update_one({"_id": a}, {"$pull": {"nhankhau": {"PERNUM": {"$gte": 3}}},
                                           "$inc": {"PERSONS": -len(moving)}}, session=s)
            ctx.log.insert_one(mongo_log_doc(new, None, "TACH_HO", a), session=s)
        mongo_tx(ctx, tx)
        return 1


class XoaDangKy(Scenario):
    sid, name, runs = "KB3.4", "Xóa đăng ký thường trú (khai tử / ra nước ngoài)", 50

    def prepare(self, ctx):
        return create_pool(ctx, self.runs)

    def mysql(self, ctx, serial):
        def tx(cur):
            cur.execute("SELECT PERSONS FROM hokhau WHERE SERIAL = %s FOR UPDATE", (serial,))
            cur.execute("SELECT MAX(PERNUM) FROM nhankhau WHERE SERIAL = %s", (serial,))
            pernum = cur.fetchone()[0]
            cur.execute("DELETE FROM nhankhau WHERE SERIAL = %s AND PERNUM = %s", (serial, pernum))
            cur.execute("UPDATE hokhau SET PERSONS = PERSONS - 1 WHERE SERIAL = %s", (serial,))
            mysql_log(cur, serial, pernum, "XOA_DANG_KY")
        mysql_tx(ctx, tx)
        return 1

    def mongo(self, ctx, serial):
        def tx(s):
            # One atomic update: drop the member with the highest PERNUM.
            before = ctx.hk.find_one_and_update(
                {"_id": serial},
                [{"$set": {
                    "nhankhau": {"$filter": {"input": "$nhankhau", "cond": {
                        "$ne": ["$$this.PERNUM", {"$max": "$nhankhau.PERNUM"}]}}},
                    "PERSONS": {"$subtract": ["$PERSONS", 1]},
                }}],
                projection={"nhankhau.PERNUM": 1},
                session=s,
            )
            pernum = max(m["PERNUM"] for m in before["nhankhau"])
            ctx.log.insert_one(mongo_log_doc(serial, pernum, "XOA_DANG_KY"), session=s)
        mongo_tx(ctx, tx)
        return 1


# Reads first, so the large KB2.2 update does not disturb their caches.
SCENARIOS = [TraCuuHo(), ViecLamTheoTuoi(), DoTuoiLaoDong(), NhapCu(), ThapDanSo(),
             DangKyHoMoi(), CapNhatCaNhan(), CapNhatHangLoat(),
             KhaiSinh(), ChuyenHoKhau(), TachHo(), XoaDangKy()]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def explain(ctx, sc, p, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    if ctx.my:
        with ctx.my.cursor() as cur:
            cur.execute("EXPLAIN ANALYZE " + sc.sql, sc.sql_params(p))
            (out_dir / f"{sc.sid}_mysql.txt").write_text(
                "\n".join(r[0] for r in cur.fetchall()), encoding="utf-8")
    if ctx.mg is not None:
        plan = ctx.mg.command("explain", {"aggregate": "hokhau", "pipeline": sc.pipeline(p),
                                          "cursor": {}}, verbosity="executionStats")
        (out_dir / f"{sc.sid}_mongo.json").write_text(
            json.dumps(plan, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


def run_scenario(ctx, sc, explain_dir):
    params = sc.prepare(ctx)
    result = {}
    for db in ctx.dbs:
        fn = sc.mysql if db == "mysql" else sc.mongo
        if sc.read:
            fn(ctx, params[0])  # warm-up, untimed
        times, first = [], None
        for p in params:
            start = time.perf_counter()
            out = fn(ctx, p)
            times.append((time.perf_counter() - start) * 1000)
            sc.restore(ctx, db, p)
            if first is None:
                first = out
        if sc.read:
            norm = sc.norm_mysql(first) if db == "mysql" else sc.norm_mongo(first)
            rows = len(norm)
        else:
            norm, rows = None, first
        result[db] = {"times": times, "norm": norm, "rows": rows}
    if sc.read and explain_dir:
        explain(ctx, sc, params[0], explain_dir)
    return result


def storage_report(ctx):
    lines = []
    if ctx.my:
        with ctx.my.cursor() as cur:
            cur.execute("SET SESSION information_schema_stats_expiry = 0")  # no 24 h stats cache
            cur.execute("SELECT TABLE_NAME, DATA_LENGTH, INDEX_LENGTH FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('hokhau', 'nhankhau')")
            for name, data, index in cur.fetchall():
                lines.append(f"MySQL   {name:<9} data {data / 2**20:8.0f} MB  index {index / 2**20:6.0f} MB")
    if ctx.mg is not None:
        s = ctx.mg.command("collStats", "hokhau")
        lines.append(f"MongoDB hokhau    data {s['storageSize'] / 2**20:8.0f} MB  "
                     f"index {s['totalIndexSize'] / 2**20:6.0f} MB  "
                     f"(BSON chưa nén {s['size'] / 2**20:.0f} MB)")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", default=[], help="scenario ids or groups, e.g. KB3 KB4.4")
    parser.add_argument("--db", choices=["both", "mysql", "mongo"], default="both")
    parser.add_argument("--province", type=int, default=704001, help="GEO1_VN dùng cho KB2.2, KB4.3, KB4.4")
    parser.add_argument("--quick", action="store_true", help="chạy ít lần hơn (chia 5)")
    parser.add_argument("--explain", action="store_true", help="lưu execution plan của các truy vấn đọc")
    parser.add_argument("--no-journal", action="store_true", help="MongoDB ghi không chờ journal (j=false)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    chosen = [sc for sc in SCENARIOS
              if not args.only or any(sc.sid == o or sc.sid.startswith(o + ".") for o in args.only)]
    if not chosen:
        sys.exit("Không có kịch bản nào khớp --only")
    if args.quick:
        for sc in chosen:
            sc.runs = max(1, sc.runs // 5)

    ctx = Ctx(args)
    if ctx.my and ctx.mg is not None:
        with ctx.my.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM hokhau WHERE SERIAL < %s", (T,))
            my_count = cur.fetchone()[0]
        mg_count = ctx.mg.hokhau.count_documents({"_id": {"$lt": T}})
        if my_count != mg_count:
            sys.exit(f"MySQL có {my_count:,} hộ nhưng MongoDB có {mg_count:,} hộ: "
                     "hãy nạp cùng một bộ dữ liệu trước khi so sánh.")
    if ctx.mg is not None and not ctx.txn:
        print("Lưu ý: MongoDB đang chạy standalone nên KHÔNG có transaction nhiều document;\n"
              "       các kịch bản KB1.2/KB3.x ghi nhiều document sẽ chạy không có transaction.\n")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    RESULTS_DIR.mkdir(exist_ok=True)
    explain_dir = RESULTS_DIR / f"explain_{stamp}" if args.explain else None
    out_rows = []
    cleanup(ctx)
    try:
        for sc in chosen:
            print(f"[{sc.sid}] {sc.name} ({sc.runs} lần) ...", flush=True)
            res = run_scenario(ctx, sc, explain_dir)
            match = ""
            if sc.read and len(res) == 2:
                match = "khớp" if res["mysql"]["norm"] == res["mongo"]["norm"] else "KHÔNG KHỚP"
            for db, r in res.items():
                t = sorted(r["times"])
                out_rows.append({
                    "kich_ban": sc.sid, "ten": sc.name,
                    "he_qtcsdl": "MySQL" if db == "mysql" else "MongoDB",
                    "so_lan": len(t), "median_ms": round(statistics.median(t), 3),
                    "p95_ms": round(t[min(len(t) - 1, int(len(t) * 0.95))], 3),
                    "min_ms": round(t[0], 3), "max_ms": round(t[-1], 3),
                    "so_dong": r["rows"], "ket_qua": match,
                    "mongo_transaction": ("có" if ctx.txn else "không") if db == "mongo" else "",
                    "ghi_chu": sc.note,
                })
    finally:
        cleanup(ctx)

    csv_path = RESULTS_DIR / f"bench_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"\n{'Kịch bản':<7} {'MySQL ms':>11} {'MongoDB ms':>11}  {'Nhanh hơn':<18} Kết quả")
    by_id = {}
    for r in out_rows:
        by_id.setdefault(r["kich_ban"], {})[r["he_qtcsdl"]] = r
    for sid, r in by_id.items():
        my = r.get("MySQL", {}).get("median_ms")
        mg = r.get("MongoDB", {}).get("median_ms")
        faster = ""
        if my and mg:
            faster = f"MongoDB x{my / mg:.1f}" if mg < my else f"MySQL x{mg / my:.1f}"
        match = next(iter(r.values()))["ket_qua"]
        print(f"{sid:<7} {my if my is not None else '-':>11} {mg if mg is not None else '-':>11}  "
              f"{faster:<18} {match}")
    print()
    for line in storage_report(ctx):
        print(line)
    print(f"\nĐã lưu {csv_path}" + (f" và {explain_dir}" if explain_dir else ""))
    ctx.close()


if __name__ == "__main__":
    main()
