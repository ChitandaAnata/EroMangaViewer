"""生成 MangaShelf.ico —— 和网页 favicon 同一套图形（深底 + 三条书脊）。

用法：python tools/make_icon.py
输出：assets/MangaShelf.ico
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "MangaShelf.ico"

BG = (11, 13, 18, 255)
BARS = [(255, 95, 126, 255), (124, 92, 255, 255), (78, 205, 196, 255)]


def render(size: int) -> Image.Image:
    """按 100x100 的原始比例放大，边缘用 4 倍超采样再缩回来，圆角不会有锯齿。"""
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    unit = s / 100.0
    radius = int(22 * unit)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=BG)

    # (x, y, w, h) 取自 favicon 的 SVG
    bars = [(22, 18, 16, 64), (42, 24, 16, 58), (62, 30, 16, 52)]
    for (x, y, w, h), color in zip(bars, BARS):
        d.rounded_rectangle(
            [int(x * unit), int(y * unit), int((x + w) * unit) - 1, int((y + h) * unit) - 1],
            radius=max(1, int(4 * unit)),
            fill=color,
        )

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sizes = [256, 128, 64, 48, 32, 16]
    frames = [render(n) for n in sizes]
    frames[0].save(OUT, format="ICO", sizes=[(n, n) for n in sizes], append_images=frames[1:])
    print(f"已生成 {OUT}（{', '.join(str(n) for n in sizes)}）")


if __name__ == "__main__":
    main()
