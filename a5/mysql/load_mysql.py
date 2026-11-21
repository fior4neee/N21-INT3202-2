"""KB1.1 - Thu thập dữ liệu dân cư (MySQL): chạy hokhau.sql + nhankhau.sql + index và đo thời gian.

Đây là bản tương ứng của a5/mongodb/load_mongo.py, để so sánh thời gian nạp hàng loạt.
Mặc định nạp vào database RIÊNG (ipums_vn2019_load) rồi xóa đi, nên không đụng tới
dữ liệu đang dùng trong ipums_vn2019.

  python a5/mysql/load_mysql.py            # đo rồi xóa database tạm
  python a5/mysql/load_mysql.py --keep     # giữ lại database tạm
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import A5_DIR, DATA_DIR, mysql_connect  # noqa: E402

INDEXES = [
    "CREATE INDEX idx_hokhau_geo1_vn ON hokhau (GEO1_VN)",
    "CREATE INDEX idx_nhankhau_age ON nhankhau (AGE)",
    "CREATE INDEX idx_nhankhau_migrate5 ON nhankhau (MIGRATE5)",
]


def statements(sql_file, db):
    sql = (A5_DIR / "mysql" / sql_file).read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql).replace("ipums_vn2019", db)
    sql = re.sub(r"'[^']*/hokhau\.csv'", f"'{(DATA_DIR / 'hokhau.csv').as_posix()}'", sql)
    sql = re.sub(r"'[^']*/nhankhau\.csv'", f"'{(DATA_DIR / 'nhankhau.csv').as_posix()}'", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="ipums_vn2019_load")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    if args.db == "ipums_vn2019":
        sys.exit("Không nạp đè lên ipums_vn2019; chọn tên database khác.")

    conn = mysql_connect()
    cur = conn.cursor()
    cur.execute("SET GLOBAL local_infile = 1")
    cur.execute(f"DROP DATABASE IF EXISTS `{args.db}`")

    timings = {}
    for name in ("hokhau.sql", "nhankhau.sql"):
        start = time.perf_counter()
        for stmt in statements(name, args.db):
            cur.execute(stmt)
        timings[name] = time.perf_counter() - start

    start = time.perf_counter()
    for stmt in INDEXES:
        cur.execute(stmt)
    index_s = time.perf_counter() - start

    cur.execute(f"SELECT COUNT(*) FROM `{args.db}`.hokhau")
    households = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM `{args.db}`.nhankhau")
    people = cur.fetchone()[0]
    cur.execute(f"ANALYZE TABLE `{args.db}`.hokhau, `{args.db}`.nhankhau")
    cur.fetchall()
    cur.execute("SET SESSION information_schema_stats_expiry = 0")  # no 24 h stats cache
    cur.execute("SELECT SUM(DATA_LENGTH), SUM(INDEX_LENGTH) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s", (args.db,))
    data, index = cur.fetchone()
    cur.execute("SELECT SUM(FILE_SIZE) FROM information_schema.INNODB_TABLESPACES "
                "WHERE NAME LIKE %s", (f"{args.db}/%",))
    files = cur.fetchone()[0]

    load_s = sum(timings.values())
    print(f"Nạp {households:,} hộ / {people:,} nhân khẩu trong {load_s:.1f} s "
          f"(hokhau {timings['hokhau.sql']:.1f} s, nhankhau {timings['nhankhau.sql']:.1f} s)")
    print(f"Tạo index trong {index_s:.1f} s")
    print(f"Dung lượng: data {data / 2**20:.0f} MB, index {index / 2**20:.0f} MB "
          f"(file .ibd trên đĩa {files / 2**20:.0f} MB)")
    if not args.keep:
        cur.execute(f"DROP DATABASE `{args.db}`")
    conn.close()


if __name__ == "__main__":
    main()
