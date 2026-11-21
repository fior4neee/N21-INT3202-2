"""Shared config for the A5 MongoDB loader and the MySQL-vs-MongoDB benchmark.

Connection settings come from environment variables (or a5/.env) so nothing secret is committed:
  MYSQL_HOST (127.0.0.1)  MYSQL_PORT (3306)  MYSQL_USER (root)  MYSQL_PASSWORD ("")
  MYSQL_DB (ipums_vn2019) MONGO_URI (mongodb://localhost:27017)  MONGO_DB (ipums_vn2019)
"""
import os
import sys
from pathlib import Path

A5_DIR = Path(__file__).resolve().parent
DATA_DIR = A5_DIR / "data"
RESULTS_DIR = A5_DIR / "results"
sys.path.insert(0, str(A5_DIR))
# Optional a5/.env (git-ignored) with lines like MYSQL_PASSWORD=...; real env vars win.
_env_file = A5_DIR / ".env"
if _env_file.is_file():
    for _line in _env_file.read_text(encoding="utf-8-sig").splitlines():
        _key, _sep, _value = _line.strip().partition("=")
        if _sep and _key and not _key.startswith("#"):
            os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

# Vietnamese output must not crash when stdout is a pipe/file using a Windows code page.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from split_ipums_csv import HOUSEHOLD_COLUMNS, PERSON_COLUMNS  # noqa: E402

FLOAT_COLUMNS = {
    "SAMPFRCT", "POPDENSGEO1", "POPDENSGEO2", "AREAMOLLWGEO1", "AREAMOLLWGEO2", "PERWT",
}

# Test households created by the benchmark live at SERIAL >= TEST_SERIAL_BASE,
# far above the real IPUMS serials, so they never collide and are easy to clean up.
TEST_SERIAL_BASE = 900_000_000_000


def convert(column, value):
    return float(value) if column in FLOAT_COLUMNS else int(value)


def mysql_connect():
    import pymysql
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DB", "ipums_vn2019"),
        autocommit=True,
        local_infile=True,
    )


def mongo_db(name=None):
    from pymongo import MongoClient
    client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    return client, client[name or os.environ.get("MONGO_DB", "ipums_vn2019")]


def mongo_supports_transactions(client):
    hello = client.admin.command("hello")
    return bool(hello.get("setName") or hello.get("msg") == "isdbgrid")
