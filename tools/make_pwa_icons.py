"""生成 PWA 需要的 PNG 图标。

Android 的「添加到主屏幕」不认 SVG 图标，必须给 192 / 512 的 PNG；
再加一张 maskable（图案留在中心安全区内，否则会被圆形或方圆裁掉边）。

用法：python tools/make_pwa_icons.py
输出：web/icons/
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "icons"

sys.path.insert(0, str(ROOT / "tools"))
from make_icon import BG, render  # noqa: E402


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    for size in (192, 512):
        render(size).save(OUT / f"icon-{size}.png")
        print(f"• icon-{size}.png")

    # maskable：Android 会按圆形 / 方圆裁切，图案缩到 78% 保证不被切边
    size = 512
    canvas = Image.new("RGBA", (size, size), BG)
    inner = render(int(size * 0.78))
    canvas.alpha_composite(inner, ((size - inner.width) // 2, (size - inner.height) // 2))
    canvas.save(OUT / "maskable-512.png")
    print("• maskable-512.png")

    # iOS 的 apple-touch-icon 不吃透明底，转成不透明的深色底
    touch = Image.new("RGBA", (180, 180), BG)
    art = render(180)
    touch.alpha_composite(art)
    touch.convert("RGB").save(OUT / "apple-touch-icon.png")
    print("• apple-touch-icon.png")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
