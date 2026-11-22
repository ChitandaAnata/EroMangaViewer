"""生成示例漫画，用于验证扫描与阅读流程。

用法：python tools/make_samples.py [输出目录]
"""
from __future__ import annotations

import io
import math
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import py7zr
    import pymupdf
except ImportError:  # 缺库时只生成 zip
    py7zr = None
    pymupdf = None

W, H = 800, 1200
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def load_font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_page(title: str, index: int, total: int, palette, subtitle: str = "") -> bytes:
    c1, c2 = palette
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)

    for y in range(H):
        d.line([(0, y), (W, y)], fill=lerp(c1, c2, y / H))

    # 装饰：同心圆环
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cx, cy = W * 0.5, H * 0.42
    for i in range(7):
        r = 90 + i * 62 + (index % 3) * 14
        od.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            outline=(255, 255, 255, 26 if i % 2 else 16),
            width=2,
        )
    od.ellipse([cx - 210, cy - 210, cx + 210, cy + 210], fill=(255, 255, 255, 12))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    d = ImageDraw.Draw(img)
    f_title = load_font(64)
    f_sub = load_font(28)
    f_page = load_font(96)
    f_small = load_font(22)

    # 页码大字
    d.text((W / 2, cy - 20), f"{index + 1:02d}", font=f_page,
           fill=(255, 255, 255), anchor="mm")

    # 标题
    d.text((W / 2, H - 250), title, font=f_title, fill=(255, 255, 255), anchor="mm")
    if subtitle:
        d.text((W / 2, H - 178), subtitle, font=f_sub,
               fill=(255, 255, 255), anchor="mm")

    d.text((W / 2, H - 96), f"{index + 1} / {total}", font=f_small,
           fill=(255, 255, 255), anchor="mm")

    # 顶部标记
    d.text((60, 70), "MangaShelf SAMPLE", font=f_small, fill=(255, 255, 255))

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


PALETTES = {
    "海边的午后": ((26, 58, 92), (232, 148, 96)),
    "机械之城": ((14, 18, 32), (86, 120, 168)),
    "夜行列车": ((18, 20, 40), (120, 92, 160)),
    "雨中的来信": ((22, 40, 46), (150, 176, 148)),
    "星屑收纳箱": ((30, 24, 54), (196, 108, 140)),
}

BOOKS = [
    ("海边的午后", 12, "cbz"),
    ("机械之城", 8, "zip"),
    ("夜行列车", 7, "7z"),
    ("雨中的来信", 10, "pdf"),
    ("星屑收纳箱", 6, "cbz_nested"),
]


def build_pages(title: str, count: int):
    palette = PALETTES.get(title, ((30, 30, 40), (90, 90, 120)))
    return [make_page(title, i, count, palette, "第一话 · 相遇") for i in range(count)]


def write_zip(path: Path, pages, nested: bool = False):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, data in enumerate(pages):
            name = f"{i + 1:03d}.jpg"
            if nested:
                name = f"{path.stem}/第1话/{name}"
            zf.writestr(name, data)
        # 加一些干扰项，测试过滤逻辑
        zf.writestr("__MACOSX/._001.jpg", b"junk")
        zf.writestr("Thumbs.db", b"junk")


def write_7z(path: Path, pages):
    with py7zr.SevenZipFile(path, "w") as z:
        for i, data in enumerate(pages):
            z.writef(io.BytesIO(data), f"{i + 1:03d}.jpg")


def write_pdf(path: Path, pages):
    doc = pymupdf.open()
    for data in pages:
        page = doc.new_page(width=W * 0.6, height=H * 0.6)
        page.insert_image(page.rect, stream=data)
    doc.save(str(path))
    doc.close()


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "sample_library"
    out_dir.mkdir(parents=True, exist_ok=True)

    for title, count, kind in BOOKS:
        pages = build_pages(title, count)
        if kind == "cbz":
            target = out_dir / f"{title}.cbz"
            write_zip(target, pages)
        elif kind == "zip":
            target = out_dir / f"{title}.zip"
            write_zip(target, pages)
        elif kind == "cbz_nested":
            target = out_dir / f"{title}.cbz"
            write_zip(target, pages, nested=True)
        elif kind == "7z" and py7zr:
            target = out_dir / f"{title}.7z"
            write_7z(target, pages)
        elif kind == "pdf" and pymupdf:
            target = out_dir / f"{title}.pdf"
            write_pdf(target, pages)
        else:
            continue
        print(f"  + {target.name}  ({count} 页, {target.stat().st_size // 1024} KB)")

    print(f"\n已生成到：{out_dir}")


if __name__ == "__main__":
    main()
