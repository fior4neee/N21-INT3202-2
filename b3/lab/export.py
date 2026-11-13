"""Export experiment output to images and PDF for the report.

    python -m lab.export e01            # one experiment
    python -m lab.export all            # every results/eNN folder

For results/eNN/ it writes:
  img/eNN_00_<section>.png   console.txt split at section headers, rendered as a terminal-style image
  img/eNN_tbl_<csv>.png      every small CSV (<= 60 rows) rendered as a table
  img/*.png                  charts written by the experiment itself are kept as they are
  eNN_report.pdf             all of the above, one image per page
PNG is used (not JPG) because text stays sharp; Word/Google Docs insert PNG directly.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .common import RESULTS

SEP = "=" * 78
LOGLINE = re.compile(r"^\[\d\d:\d\d:\d\d\] ")
MAX_LINES = 70          # lines per image before a section is split into parts
MAX_CSV_ROWS = 60
FONT_SIZE = 18
BG, FG, TITLE_FG, BAR = (255, 255, 255), (20, 20, 20), (10, 60, 140), (232, 238, 247)


def _font(bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["consolab.ttf", "courbd.ttf"] if bold else ["consola.ttf", "cour.ttf"]
    for n in names + ["DejaVuSansMono.ttf"]:
        for base in (Path("C:/Windows/Fonts"), Path("")):
            try:
                return ImageFont.truetype(str(base / n), FONT_SIZE)
            except OSError:
                continue
    return ImageFont.load_default()


def render_text(lines: list[str], title: str, out: Path) -> Path:
    font, bold = _font(), _font(True)
    lines = [ln.replace("\t", "    ").rstrip() for ln in lines] or [""]
    cw = font.getbbox("M")[2]
    lh = int(FONT_SIZE * 1.3)
    pad = 24
    width = max(80, max(len(ln) for ln in lines), len(title) + 2) * cw + 2 * pad
    bar_h = lh + 16
    height = bar_h + len(lines) * lh + 2 * pad
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, bar_h], fill=BAR)
    d.text((pad, 8), title, font=bold, fill=TITLE_FG)
    y = bar_h + pad
    for ln in lines:
        d.text((pad, y), ln, font=font, fill=FG)
        y += lh
    d.rectangle([0, 0, width - 1, height - 1], outline=(180, 180, 180))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def split_sections(text: str) -> list[tuple[str, list[str]]]:
    """Split console text at blocks of SEP / title / SEP."""
    lines = text.splitlines()
    sections, title, cur = [], "header", []
    i = 0
    while i < len(lines):
        if lines[i] == SEP and i + 2 < len(lines) and lines[i + 2] == SEP:
            if any(x.strip() for x in cur):
                sections.append((title, cur))
            title, cur = lines[i + 1].strip(), []
            i += 3
            continue
        cur.append(lines[i])
        i += 1
    if any(x.strip() for x in cur):
        sections.append((title, cur))
    # trim blank lines at the edges
    out = []
    for t, ls in sections:
        while ls and not ls[0].strip():
            ls = ls[1:]
        while ls and not ls[-1].strip():
            ls = ls[:-1]
        out.append((t, ls))
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "section"


def csv_to_lines(path: Path) -> list[str] | None:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or len(rows) - 1 > MAX_CSV_ROWS:
        return None
    w = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(len(rows[0]))]
    fmt = lambda r: "  ".join((r[i] if i < len(r) else "").ljust(w[i]) for i in range(len(w)))  # noqa: E731
    return [fmt(rows[0]), "  ".join("-" * x for x in w)] + [fmt(r) for r in rows[1:]]


def export(exp: str) -> list[Path]:
    d = RESULTS / exp
    img_dir = d / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    # remove images we generated before (keep charts written by the experiment)
    for p in img_dir.glob(f"{exp}_[0-9][0-9]_*.png"):
        p.unlink()
    for p in img_dir.glob(f"{exp}_tbl_*.png"):
        p.unlink()

    made: list[Path] = []
    console = d / "console.txt"
    if console.exists():
        n = 0
        for title, lines in split_sections(console.read_text(encoding="utf-8")):
            if all(LOGLINE.match(x) or not x.strip() for x in lines):
                continue  # only progress-log lines: not worth an image
            parts = [lines[i:i + MAX_LINES] for i in range(0, max(len(lines), 1), MAX_LINES)]
            for k, part in enumerate(parts):
                t = f"{exp.upper()}  {title}" + (f"  ({k + 1}/{len(parts)})" if len(parts) > 1 else "")
                made.append(render_text(part, t, img_dir / f"{exp}_{n:02d}_{_slug(title)}.png"))
                n += 1
    for c in sorted(d.glob("*.csv")):
        lines = csv_to_lines(c)
        if lines:
            made.append(render_text(lines, f"{exp.upper()}  {c.name}", img_dir / f"{exp}_tbl_{c.stem}.png"))
    charts = sorted(p for p in img_dir.glob("*.png") if p not in made)
    pages = made[: len([m for m in made if "_tbl_" not in m.name])] + charts + \
        [m for m in made if "_tbl_" in m.name]
    if pages:
        imgs = [Image.open(p).convert("RGB") for p in pages]
        pdf = d / f"{exp}_report.pdf"
        imgs[0].save(pdf, save_all=True, append_images=imgs[1:], resolution=150)
        print(f"[export] {exp}: {len(pages)} images -> {img_dir}  |  PDF -> {pdf}")
    return pages


def main(argv=None) -> int:
    args = (argv if argv is not None else sys.argv[1:]) or ["all"]
    exps = sorted(p.name for p in RESULTS.glob("e[0-9][0-9]") if p.is_dir()) if args == ["all"] else args
    for e in exps:
        export(e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
