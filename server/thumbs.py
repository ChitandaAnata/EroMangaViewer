"""封面 / 内页缩略图生成与缓存。"""
from __future__ import annotations

import io
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image

from . import config

# 漫画扫描件偶尔非常大，放宽 Pillow 的解压炸弹限制
Image.MAX_IMAGE_PIXELS = None

COVER_WIDTH = int(config.load_config().get("cover_width") or 480)
PAGE_WIDTH = int(config.load_config().get("page_thumb_width") or 400)


def cover_path(comic_id: str) -> Path:
    return config.COVER_DIR / f"{comic_id}.jpg"


def write_cover(data: bytes, comic_id: str, width: int = COVER_WIDTH) -> Optional[str]:
    """把首图压成书架用缩略图，返回文件名；失败返回 None。"""
    config.ensure_dirs()
    out = cover_path(comic_id)
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (18, 20, 28))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")

            if im.width > width:
                ratio = width / im.width
                im = im.resize((width, max(1, int(im.height * ratio))), Image.LANCZOS)

            im.save(out, "JPEG", quality=84, optimize=True, progressive=True)
        return out.name
    except Exception as exc:
        print(f"[cover] 生成封面失败 {comic_id}: {exc}")
        try:
            if out.exists():
                out.unlink()
        except OSError:
            pass
        return None


def remove_cover(comic_id: str) -> None:
    p = cover_path(comic_id)
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- 内页缩略图
def pages_dir(comic_id: str) -> Path:
    return config.PAGE_DIR / comic_id


def page_thumb_path(comic_id: str, index: int) -> Path:
    return pages_dir(comic_id) / f"{index}.jpg"


def write_page_thumb(data: bytes, comic_id: str, index: int, width: int = PAGE_WIDTH) -> Optional[Path]:
    """把某一页压成详情页预览用的小图，返回文件路径；失败返回 None。"""
    target = page_thumb_path(comic_id, index)
    if target.is_file():
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")

            if im.width > width:
                ratio = width / im.width
                im = im.resize((width, max(1, int(im.height * ratio))), Image.LANCZOS)

            # 先生成临时文件再替换：多个请求同时生成同一页时不会互相写坏
            tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
            im.save(tmp, "JPEG", quality=80, optimize=True, progressive=True)
            os.replace(tmp, target)
        return target
    except Exception as exc:
        print(f"[thumb] 生成内页缩略图失败 {comic_id}#{index}: {exc}")
        return None


def remove_pages(comic_id: str) -> None:
    d = pages_dir(comic_id)
    try:
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def prune_page_cache(valid_ids) -> int:
    """清掉已不存在作品的内页缩略图目录（含删除瞬间被请求重建的残留）。"""
    if not config.PAGE_DIR.exists():
        return 0
    valid = set(valid_ids)
    n = 0
    for d in config.PAGE_DIR.iterdir():
        if not d.is_dir() or d.name in valid:
            continue
        shutil.rmtree(d, ignore_errors=True)
        n += 1
    return n


def clear_covers() -> int:
    n = 0
    if config.COVER_DIR.exists():
        for f in config.COVER_DIR.glob("*.jpg"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n
