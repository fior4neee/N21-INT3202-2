"""Thử nghiệm bổ sung: bộ tối ưu chọn index có đúng không? (chương 2 Index, chương 8 Tối ưu truy vấn)

KB4.3 MongoDB:  planner tự chọn  vs  hint idx_geo1_vn  vs  index ghép {GEO1_VN, nhankhau.AGE}
KB4.2 MySQL:    optimizer tự chọn (quét idx_nhankhau_age)  vs  IGNORE INDEX (quét bảng)
                vs  index ghép (EMPSTAT, AGE) - covering index

Thời gian là thời gian server (explain executionStats / EXPLAIN ANALYZE). Index tạm được xóa ở cuối.
  python a5/bench/index_choice.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RESULTS_DIR, mongo_db, mysql_connect  # noqa: E402

PROVINCE = 704001
KB43 = [
    {"$match": {"GEO1_VN": PROVINCE, "nhankhau.AGE": {"$gte": 18, "$lte": 60}}},
    {"$unwind": "$nhankhau"},
    {"$match": {"nhankhau.AGE": {"$gte": 18, "$lte": 60}}},
    {"$replaceRoot": {"newRoot": {"$mergeObjects": ["$nhankhau", {"SERIAL": "$SERIAL", "GEO1_VN": "$GEO1_VN"}]}}},
    {"$count": "n"},
]
KB42 = "SELECT AGE, COUNT(*) FROM nhankhau {hint} WHERE EMPSTAT = 1 GROUP BY AGE ORDER BY AGE"


def mongo_run(db, label, hint=None):
    cmd = {"aggregate": "hokhau", "pipeline": KB43, "cursor": {}}
    if hint:
        cmd["hint"] = hint
    plan = db.command("explain", cmd, verbosity="executionStats")
    stages = plan.get("stages", [])
    cursor = stages[0]["$cursor"] if stages else plan
    stats = cursor["executionStats"]
    ms = max([s.get("executionTimeMillisEstimate", 0) for s in stages] + [stats["executionTimeMillis"]])
    used = re.findall(r"'indexName': '(\w+)'", str(cursor["queryPlanner"]["winningPlan"]))
    n = list(db.hokhau.aggregate(KB43, **({"hint": hint} if hint else {})))[0]["n"]
    return f"MongoDB KB4.3  {label:<40} {ms:>7} ms  docsExamined={stats['totalDocsExamined']:>7,}  index={used}  kết quả={n:,}"


def mysql_run(cur, label, hint=""):
    cur.execute("EXPLAIN ANALYZE " + KB42.format(hint=hint))
    text = cur.fetchone()[0]
    ms = float(re.search(r"actual time=[\d.]+\.\.([\d.]+)", text).group(1))
    access = re.findall(r"(Table scan|Covering index scan|Index scan|Index range scan)[^(]*", text)
    return f"MySQL   KB4.2  {label:<40} {ms:>7.0f} ms  truy cập={[a.strip() for a in access]}"


def main():
    lines = []
    client, db = mongo_db()
    try:
        lines.append(mongo_run(db, "planner tự chọn"))
        lines.append(mongo_run(db, "hint idx_geo1_vn", "idx_geo1_vn"))
        db.hokhau.create_index([("GEO1_VN", 1), ("nhankhau.AGE", 1)], name="tmp_geo_age")
        lines.append(mongo_run(db, "index ghép {GEO1_VN, nhankhau.AGE}", "tmp_geo_age"))
    finally:
        if "tmp_geo_age" in db.hokhau.index_information():
            db.hokhau.drop_index("tmp_geo_age")
        client.close()

    conn = mysql_connect()
    cur = conn.cursor()
    try:
        lines.append(mysql_run(cur, "optimizer tự chọn"))
        lines.append(mysql_run(cur, "IGNORE INDEX (idx_nhankhau_age)", "IGNORE INDEX (idx_nhankhau_age)"))
        cur.execute("CREATE INDEX tmp_empstat_age ON nhankhau (EMPSTAT, AGE)")
        lines.append(mysql_run(cur, "index ghép (EMPSTAT, AGE)", "FORCE INDEX (tmp_empstat_age)"))
    finally:
        cur.execute("SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = 'nhankhau' AND INDEX_NAME = 'tmp_empstat_age'")
        if cur.fetchone()[0]:
            cur.execute("DROP INDEX tmp_empstat_age ON nhankhau")
        conn.close()

    text = "\n".join(lines)
    print(text)
    (RESULTS_DIR / "index_choice.txt").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
