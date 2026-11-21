"""Xuất kết quả A5 ra ảnh PNG + 1 file PDF để đưa vào báo cáo.

  python a5/bench/report.py          # dùng lần chạy bench mới nhất trong a5/results/

Đọc:  results/bench_<thời gian>.csv, results/bench_console.txt, results/kb1_1_*_load.txt,
      results/explain_<thời gian>/ (nếu có)
Ghi:  results/img/*.png  và  results/a5_report.pdf (mỗi ảnh 1 trang)
"""
import csv
import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import A5_DIR, RESULTS_DIR  # noqa: E402

IMG = RESULTS_DIR / "img"
COLORS = {"MySQL": "#2a78d6", "MongoDB": "#eb6834"}  # fixed per system, validated pair
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
SYSTEMS = ["MySQL", "MongoDB"]

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.labelsize": 10,
    "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 9, "ytick.labelsize": 9,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "font.family": "DejaVu Sans",
})


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def latest(pattern):
    found = sorted(RESULTS_DIR.glob(pattern))
    return found[-1] if found else None


def read_bench(path):
    rows = {}
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.setdefault(r["kich_ban"], {"ten": r["ten"]})[r["he_qtcsdl"]] = r
    return rows


def read_load(system):
    path = RESULTS_DIR / f"kb1_1_{'mysql' if system == 'MySQL' else 'mongo'}_load.txt"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    num = lambda pat: float(re.search(pat, text).group(1))  # noqa: E731
    return {"load": num(r"trong ([\d.]+) s"), "index": num(r"Tạo index trong ([\d.]+) s"),
            "data": num(r"data ([\d.]+) MB"), "index_mb": num(r"index ([\d.]+) MB"), "text": text}


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def fmt_ms(v):
    if v >= 1000:
        return f"{v / 1000:.2f} s"
    return f"{v:.1f} ms" if v >= 10 else f"{v:.2f} ms"


def grouped_barh(labels, values, title, xlabel, out, log=False, fmt=fmt_ms, note=None):
    """values: {system: [v per label]}; horizontal bars, one group per label."""
    n = len(labels)
    fig, ax = plt.subplots(figsize=(10, 0.75 * n + 1.6))
    h = 0.36
    for i, system in enumerate(SYSTEMS):
        ys = [k + (i - 0.5) * (h + 0.04) for k in range(n)]
        vals = values[system]
        ax.barh(ys, vals, height=h, color=COLORS[system], label=system, zorder=2)
        for y, v in zip(ys, vals):
            ax.text(v * (1.04 if log else 1) + (0 if log else max(max(values[s]) for s in SYSTEMS) * 0.01),
                    y, fmt(v), va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    if log:
        ax.set_xscale("log")
    ax.set_xlim(right=ax.get_xlim()[1] * (3 if log else 1.15))
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(xlabel)
    ax.set_title(title, loc="left")
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2)
    if note:
        fig.text(0.01, 0.005, note, fontsize=8, color=INK2, va="bottom")
    fig.tight_layout(rect=(0, 0.07 if note else 0, 1, 1))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def scenario_chart(bench, ids, title, out):
    labels = [f"{sid}  {bench[sid]['ten']}" for sid in ids]
    labels = [lb if len(lb) <= 62 else lb[:60] + "…" for lb in labels]
    values = {s: [float(bench[sid][s]["median_ms"]) for sid in ids] for s in SYSTEMS}
    return grouped_barh(labels, values, title, "Thời gian median mỗi lần (ms, thang log) - thấp hơn là nhanh hơn",
                        out, log=True)


# ---------------------------------------------------------------------------
# Text / table images (sharp PNG, monospace)
# ---------------------------------------------------------------------------
FONT_SIZE = 17


def font(bold=False):
    for name in (["consolab.ttf"] if bold else ["consola.ttf"]) + ["DejaVuSansMono.ttf"]:
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), FONT_SIZE)
        except OSError:
            try:
                return ImageFont.truetype(name, FONT_SIZE)
            except OSError:
                continue
    return ImageFont.load_default()


def render_columns(columns, title, out):
    """columns: list of (heading, lines, accent_hex or None) rendered side by side."""
    f, fb = font(), font(True)
    cw, lh, pad, gap = f.getbbox("M")[2], int(FONT_SIZE * 1.35), 24, 36
    widths = [max([len(h)] + [len(ln) for ln in lines]) * cw for h, lines, _ in columns]
    rows = max(len(lines) for _, lines, _ in columns)
    bar = lh + 18
    width = sum(widths) + gap * (len(columns) - 1) + 2 * pad
    width = max(width, len(title) * cw + 2 * pad)
    height = bar + lh + 14 + rows * lh + 2 * pad
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, bar], fill=(232, 238, 247))
    d.text((pad, 9), title, font=fb, fill=(10, 60, 140))
    x = pad
    for (heading, lines, accent), w in zip(columns, widths):
        y = bar + pad // 2
        d.text((x, y), heading, font=fb, fill=accent or INK)
        if accent:
            d.rectangle([x, y + lh, x + w, y + lh + 3], fill=accent)
        y += lh + 14
        for ln in lines:
            d.text((x, y), ln, font=f, fill=(20, 20, 20))
            y += lh
        x += w + gap
    d.rectangle([0, 0, width - 1, height - 1], outline=(180, 180, 180))
    img.save(out)
    return out


def table_lines(header, rows):
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths))  # noqa: E731
    return [line(header), "-" * len(line(header))] + [line(r) for r in rows]


def file_lines(rel):
    return (A5_DIR / rel).read_text(encoding="utf-8").replace("\t", "    ").splitlines()


# ---------------------------------------------------------------------------
# Explain parsing: server-side time and access path
# ---------------------------------------------------------------------------
def mysql_plan(path):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"actual time=[\d.]+\.\.([\d.]+)", text)
    access = []
    for kind in ("Index lookup", "Single-row index lookup", "Index range scan",
                 "Covering index", "Table scan", "Index scan"):
        for hit in re.finditer(rf"{kind}[^(]*?(?: using (\w+))?(?: over|\s\(|$)", text):
            label = kind + (f" ({hit.group(1)})" if hit.group(1) else "")
            if label not in access:
                access.append(label)
    return (float(m.group(1)) if m else None), ", ".join(access) or "-"


def mongo_plan(path):
    plan = json.loads(path.read_text(encoding="utf-8"))
    stages = []

    def walk(node):
        if isinstance(node, dict):
            if "stage" in node and isinstance(node["stage"], str):
                label = node["stage"] + (f"({node['indexName']})" if node.get("indexName") else "")
                if label not in stages:
                    stages.append(label)
            for k, v in node.items():
                if k not in ("rejectedPlans", "executionStats", "allPlansExecution"):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    if "stages" in plan:
        cursor = plan["stages"][0].get("$cursor", {})
        walk(cursor.get("queryPlanner", {}).get("winningPlan", {}))
        ms = max([s.get("executionTimeMillisEstimate", 0) for s in plan["stages"]] +
                 [cursor.get("executionStats", {}).get("executionTimeMillis", 0)])
        stages += [k for s in plan["stages"][1:] for k in s if k.startswith("$")][:3]
    else:
        walk(plan.get("queryPlanner", {}).get("winningPlan", {}))
        ms = plan.get("executionStats", {}).get("executionTimeMillis")
    return ms, " > ".join(stages[:5]) or "-"


# ---------------------------------------------------------------------------
def main():
    bench_csv = latest("bench_*.csv")
    if not bench_csv:
        sys.exit("Chưa có results/bench_*.csv - hãy chạy a5/bench/bench.py trước.")
    bench = read_bench(bench_csv)
    IMG.mkdir(parents=True, exist_ok=True)
    for old in IMG.glob("*.png"):
        old.unlink()
    pages = []

    # 1. Summary table
    rows = []
    for sid, r in bench.items():
        if not all(s in r for s in SYSTEMS):
            continue
        my, mg = float(r["MySQL"]["median_ms"]), float(r["MongoDB"]["median_ms"])
        faster = f"MongoDB x{my / mg:.1f}" if mg < my else f"MySQL x{mg / my:.1f}"
        rows.append([sid, r["ten"][:58], r["MySQL"]["so_lan"], fmt_ms(my), fmt_ms(float(r["MySQL"]["p95_ms"])),
                     fmt_ms(mg), fmt_ms(float(r["MongoDB"]["p95_ms"])), faster, r["MySQL"]["ket_qua"] or "-"])
    txn = next((r["MongoDB"]["mongo_transaction"] for r in bench.values() if "MongoDB" in r), "?")
    lines = table_lines(["Mã", "Kịch bản", "Lần", "MySQL med", "MySQL p95", "Mongo med", "Mongo p95",
                         "Nhanh hơn", "Kết quả"], rows)
    lines += ["", f"Nguồn: {bench_csv.name}.  Median/p95 đo từ client (Python), gồm cả thời gian nhận hết kết quả.",
              f"MongoDB transaction nhiều document: {txn}.  'Kết quả' = hai hệ trả về cùng dữ liệu (chỉ kịch bản đọc)."]
    pages.append(render_columns([("", lines, None)], "A5 - Tổng hợp so sánh MySQL vs MongoDB",
                                IMG / "a5_01_tong_hop.png"))

    # 2. Charts
    read_ids = [s for s in bench if s.startswith("KB4")]
    write_ids = [s for s in bench if not s.startswith("KB4")]
    if read_ids:
        pages.append(scenario_chart(bench, read_ids, "KB4 - Tra cứu & thống kê", IMG / "a5_02_kb4_tra_cuu.png"))
    if write_ids:
        pages.append(scenario_chart(bench, write_ids, "KB1-KB3 - Thu thập, cập nhật, đăng ký cư trú",
                                    IMG / "a5_03_kb1_kb3_ghi.png"))
    loads = {s: read_load(s) for s in SYSTEMS}
    if all(loads.values()):
        pages.append(grouped_barh(
            ["Nạp dữ liệu (LOAD DATA / insertMany)", "Tạo index"],
            {s: [loads[s]["load"], loads[s]["index"]] for s in SYSTEMS},
            "KB1.1 - Nạp hàng loạt 1.001.120 hộ / 3.550.482 nhân khẩu", "Thời gian (giây)",
            IMG / "a5_04_kb1_1_nap_du_lieu.png", fmt=lambda v: f"{v:.1f} s"))
        pages.append(grouped_barh(
            ["Dữ liệu", "Index"],
            {s: [loads[s]["data"], loads[s]["index_mb"]] for s in SYSTEMS},
            "Dung lượng lưu trữ sau khi nạp", "MB trên đĩa",
            IMG / "a5_05_dung_luong.png", fmt=lambda v: f"{v:.0f} MB",
            note="Đo ngay sau khi nạp. MySQL: DATA_LENGTH/INDEX_LENGTH (InnoDB, không nén; dao động 680-1040 MB tùy lịch sử bảng).\n"
                 "MongoDB: storageSize/totalIndexSize (WiredTiger, nén snappy; BSON chưa nén khoảng 1,8 GB)."))

    # 3. Explain: server-side time vs client-side time
    explain_dir = latest("explain_*")
    if explain_dir:
        rows = []
        for sid in read_ids:
            mp, gp = explain_dir / f"{sid}_mysql.txt", explain_dir / f"{sid}_mongo.json"
            if not (mp.is_file() and gp.is_file()):
                continue
            my_ms, my_acc = mysql_plan(mp)
            mg_ms, mg_acc = mongo_plan(gp)
            rows.append([sid, (f"{my_ms:.1f} ms" if my_ms >= 1 else f"{my_ms:.3f} ms") if my_ms is not None else "-",
                         my_acc[:60], f"{mg_ms} ms" if mg_ms is not None else "-", mg_acc[:95]])
        lines = table_lines(["Mã", "MySQL server", "MySQL truy cập", "Mongo server", "MongoDB kế hoạch"], rows)
        lines += ["", "Thời gian thực thi trên server (EXPLAIN ANALYZE / explain executionStats), không gồm",
                  "thời gian truyền và giải mã kết quả ở client. Chi tiết đầy đủ: " + explain_dir.name + "/"]
        pages.append(render_columns([("", lines, None)], "Kế hoạch thực thi các truy vấn đọc",
                                    IMG / "a5_06_explain.png"))

    index_choice = RESULTS_DIR / "index_choice.txt"
    if index_choice.is_file():
        lines = index_choice.read_text(encoding="utf-8").splitlines() + [
            "", "Thời gian server. KB4.3: hint/index ghép giúp MongoDB chỉ đọc các hộ của 1 tỉnh.",
            "KB4.2: MySQL tự chọn quét qua idx_nhankhau_age (truy cập ngẫu nhiên vào bảng) chậm hơn quét bảng."]
        pages.append(render_columns([("", lines, None)], "Thử nghiệm bổ sung: bộ tối ưu có chọn đúng index?",
                                    IMG / "a5_06b_chon_index.png"))

    # 4. Console output of the run
    console = RESULTS_DIR / "bench_console.txt"
    if console.is_file():
        load_lines = []
        for s in SYSTEMS:
            if loads.get(s):
                load_lines += [f"{s}: " + ln for ln in loads[s]["text"].splitlines()]
        pages.append(render_columns(
            [("", load_lines + [""] + console.read_text(encoding="utf-8").splitlines(), None)],
            "Kết quả chạy (console)", IMG / "a5_07_console.png"))

    # 5. Same business operation, side by side
    pairs = [
        ("KB1.2 Đăng ký hộ mới", "mysql/kb1_dang_ky_ho_moi.sql", "mongodb/kb1_dang_ky_ho_moi.js"),
        ("KB2 Cập nhật dữ liệu dân cư", "mysql/kb2_cap_nhat.sql", "mongodb/kb2_cap_nhat.js"),
        ("KB3 Đăng ký / quản lý cư trú", "mysql/kb3_cu_tru.sql", "mongodb/kb3_cu_tru.js"),
    ]
    for i, (title, sql, js) in enumerate(pairs, start=8):
        pages.append(render_columns(
            [(f"MySQL - a5/{sql}", file_lines(sql), COLORS["MySQL"]),
             (f"MongoDB - a5/{js}", file_lines(js), COLORS["MongoDB"])],
            f"{title}: cùng nghiệp vụ trên hai hệ", IMG / f"a5_{i:02d}_lenh_{Path(sql).stem}.png"))
    sql_reads = []
    for q in ("q1_point_lookup", "q2_aggregation", "q3_range", "q5_nhap_cu", "q6_thap_dan_so"):
        sql_reads += file_lines(f"mysql/{q}.sql") + [""]
    pages.append(render_columns(
        [("MySQL - a5/mysql/q1,q2,q3,q5,q6", sql_reads, COLORS["MySQL"]),
         ("MongoDB - a5/mongodb/kb4_tra_cuu.js", file_lines("mongodb/kb4_tra_cuu.js"), COLORS["MongoDB"])],
        "KB4 Tra cứu & thống kê: cùng truy vấn trên hai hệ", IMG / "a5_11_lenh_kb4_tra_cuu.png"))

    # 6. PDF, one image per page
    images = [Image.open(p).convert("RGB") for p in pages]
    pdf = RESULTS_DIR / "a5_report.pdf"
    images[0].save(pdf, save_all=True, append_images=images[1:], resolution=150)
    print(f"Đã ghi {len(pages)} ảnh vào {IMG} và {pdf}")


if __name__ == "__main__":
    main()
