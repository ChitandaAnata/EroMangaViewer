"""压缩包读取抽象层。

对上层只暴露统一的 ComicSource 接口，屏蔽 ZIP / RAR / 7z / PDF 的差异。
所有实现都保证：图片按自然顺序排序（1,2,10 而不是 1,10,2）。
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional, Tuple

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".webp", ".gif",
    ".bmp", ".avif", ".jfif", ".tif", ".tiff",
}

ARCHIVE_EXTS = {
    ".zip": "zip", ".cbz": "zip",
    ".rar": "rar", ".cbr": "rar",
    ".7z": "7z", ".cb7": "7z",
}

# 打包时排除了 PDF 引擎（省 51 MB），这里单独给个说得清的提示。
# 想恢复 PDF 支持：把 ".pdf": "pdf" 加回 ARCHIVE_EXTS，
# 再从 tools/build_backend.py 的排除列表里删掉 pymupdf / fitz 即可，PdfSource 一直都在。
PDF_HINT = "这个版本不包含 PDF 支持（安装包省掉了 PDF 引擎）。请先用压缩工具把 PDF 包成 .cbz，或转成图片。"

MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg",
    ".jfif": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".bmp": "image/bmp", ".avif": "image/avif",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}

_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


class SourceError(Exception):
    """压缩包无法读取（损坏 / 加密 / 无图片）。"""


def natural_key(text: str):
    """自然排序 key：把连续数字当数字比较。"""
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", text)]


def is_image_name(name: str) -> bool:
    norm = name.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return False
    # 跳过 macOS 资源叉目录与隐藏文件
    if any(p.startswith(".") or p == "__MACOSX" for p in parts[:-1]):
        return False
    base = parts[-1]
    if base.startswith(".") or base.lower() in _JUNK_NAMES:
        return False
    return os.path.splitext(base)[1].lower() in IMAGE_EXTS


def mime_of(name: str) -> str:
    return MIME_MAP.get(os.path.splitext(name)[1].lower(), "image/jpeg")


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def find_bsdtar() -> str | None:
    # Windows 自带的 System32\tar.exe 就是 bsdtar(libarchive)，能读 rar/7z。
    # 必须先找它：PATH 里若排着 Git 的 GNU tar，它既不认 rar 还会把 "G:\..." 
    # 当成远程主机（报 "Cannot connect to G:"）。
    win = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if win.is_file():
        return str(win)
    for cand in ("bsdtar", "tar"):
        p = shutil.which(cand)
        if p:
            return p
    return None


class ComicSource:
    """一本漫画的图片序列。"""

    kind = "unknown"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.names: List[str] = []

    @property
    def page_count(self) -> int:
        return len(self.names)

    def page_bytes(self, index: int) -> Tuple[bytes, str]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class ZipSource(ComicSource):
    kind = "zip"

    def __init__(self, path: Path):
        super().__init__(path)
        try:
            self._zf = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as exc:
            raise SourceError(f"ZIP 文件损坏或格式不正确：{exc}") from exc
        except OSError as exc:
            raise SourceError(f"无法打开文件：{exc}") from exc
        try:
            infos = [i for i in self._zf.infolist() if not i.is_dir() and is_image_name(i.filename)]
            infos.sort(key=lambda i: natural_key(i.filename))
            self._infos = infos
            self.names = [i.filename for i in infos]
        except Exception as exc:
            raise SourceError(f"解析 ZIP 目录失败：{exc}") from exc
        if not self.names:
            raise SourceError("压缩包内没有找到图片")

    def page_bytes(self, index: int) -> Tuple[bytes, str]:
        info = self._infos[index]
        if info.flag_bits & 0x1:
            raise SourceError("该压缩包已加密，无法读取")
        try:
            with self._zf.open(info) as fh:
                return fh.read(), mime_of(info.filename)
        except RuntimeError as exc:
            raise SourceError(f"读取图片失败（可能已加密）：{exc}") from exc

    def close(self) -> None:
        try:
            self._zf.close()
        except Exception:
            pass


class MaskedZipSource(ComicSource):
    """读取被伪装成 .cbz/.zip 的其他格式（少数下载站会这样打包）。"""

    kind = "zip"

    def __init__(self, path: Path, inner: ComicSource):
        super().__init__(path)
        self._inner = inner
        self.names = inner.names

    def page_bytes(self, index: int) -> Tuple[bytes, str]:
        return self._inner.page_bytes(index)

    def close(self) -> None:
        self._inner.close()


def bsdtar_extract(path: Path, name: str) -> bytes:
    """用系统 bsdtar 提取单个条目（rar / 7z 兜底方案）。"""
    tar = find_bsdtar()
    if not tar:
        raise SourceError("系统缺少可用的解压工具（未找到 bsdtar / unrar / 7z）")
    try:
        res = subprocess.run(
            [tar, "-xOf", str(path), name], capture_output=True, timeout=180,
        )
    except Exception as exc:
        raise SourceError(f"调用系统解压工具失败：{exc}") from exc
    if res.returncode != 0 or not res.stdout:
        raise SourceError(f"提取图片失败：{_decode(res.stderr)[:200]}")
    return res.stdout


# --------------------------------------------------------------------------
# RAR / 7z 的文件名匹配难题
# --------------------------------------------------------------------------
# 有些压缩包（尤其在国内机器上打的 RAR）文件名按本地代码页（GBK / Shift-JIS）
# 存，bsdtar 列目录时吐的是原始字节，我们解码后拿到的是正确 Unicode；但再拿这个
# 名字当参数交给 bsdtar，它内部按另一套编码比对，就报 "Not found in archive"。
# 现象：目录能列、页数正确，却一页都取不出来。
#
# 两条退路，代价从低到高：
#   1) 用只含 ASCII 的通配符匹配（*/001.png）——绝大多数包一次就中；
#   2) 整包解到临时目录，之后按文件名 / 自然序取（最慢但一定能出来），
#      解出来的目录按（路径+修改时间）缓存，不会每题都重解。

_arc_path_mismatch: "set[Tuple[str, float]]" = set()
_arc_glob_slow: "set[Tuple[str, float]]" = set()  # 通配符会卡死的包，直接整包解
_GLOB_TIMEOUT = 5  # 通配符最多等这么久，再慢就换整包解（通常零点几秒）

_bulk_lock = threading.Lock()
_bulk_cache: "OrderedDict[Tuple[str, float], Tuple[Path, List[Path]]]" = OrderedDict()
_BULK_KEEP = 2  # 同时保留几份整包解压结果（每份可能不小）


def _arc_key(path: Path) -> Tuple[str, float]:
    try:
        return (str(path), float(path.stat().st_mtime))
    except OSError:
        return (str(path), 0.0)


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _bulk_extract(path: Path) -> Tuple[Path, List[Path]]:
    """整个压缩包解一次到临时目录，返回 (目录, 按自然序排好的图片列表)。"""
    key = _arc_key(path)
    with _bulk_lock:
        hit = _bulk_cache.get(key)
        if hit is not None:
            _bulk_cache.move_to_end(key)
            return hit
    tar = find_bsdtar()
    if not tar:
        raise SourceError("系统缺少可用的解压工具（未找到 bsdtar / unrar / 7z）")
    tmp = Path(tempfile.mkdtemp(prefix="mangashelf-arc-"))
    try:
        # 不带任何匹配模式 = 全解，彻底绕开文件名编码问题
        res = subprocess.run(
            [tar, "-xf", str(path), "-C", str(tmp)],
            capture_output=True, timeout=1800,
        )
        if res.returncode != 0:
            raise SourceError(f"解压压缩包失败：{_decode(res.stderr)[:200]}")
        files = [p for p in tmp.rglob("*") if p.is_file() and is_image_name(p.name)]
        if not files:
            raise SourceError("压缩包内没有找到图片")
        files.sort(key=lambda p: natural_key(str(p.relative_to(tmp)).replace("\\", "/")))
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    with _bulk_lock:
        _bulk_cache[key] = (tmp, files)
        while len(_bulk_cache) > _BULK_KEEP:
            _k, (old_dir, _files) = _bulk_cache.popitem(last=False)
            shutil.rmtree(old_dir, ignore_errors=True)
    return tmp, files


def clear_bulk_cache() -> None:
    with _bulk_lock:
        while _bulk_cache:
            _k, (old_dir, _f) = _bulk_cache.popitem(last=False)
            shutil.rmtree(old_dir, ignore_errors=True)
        _arc_path_mismatch.clear()
        _arc_glob_slow.clear()


atexit.register(clear_bulk_cache)  # 进程退出时清掉 %TEMP% 里解出来的目录


def _bsdtar_read(path: Path, tar: str, pattern: str, timeout: int = 600) -> Optional[bytes]:
    """bsdtar 取一个条目；取不到返回 None（不抛错，留给下一级退路）。

    调不动工具抛 SourceError；仅仅是太慢（TimeoutExpired）会原样冒泡，
    调用方据此换更快的那条退路。
    """
    try:
        res = subprocess.run(
            [tar, "-xOf", str(path), pattern], capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise
    except Exception as exc:
        raise SourceError(f"调用系统解压工具失败：{exc}") from exc
    if res.returncode != 0 or not res.stdout:
        return None
    return res.stdout


def page_from_bsdtar(path: Path, name: str, index: int, names: List[str]) -> bytes:
    """用系统 bsdtar 取第 index 页，三级退路：原名 → ASCII 通配符 → 整包解压。

    RAR / 7z 的「压缩条目」必须有外部解压器，Python 的 rarfile 只能读未压缩
    存储的条目（现象就是前几页能看、后面全报 Cannot find working tool）。
    这里统一走 bsdtar，名字编码对不上时按 basename 通配或整包解一次。

    ⚠️ 通配符这一级必须带短超时：RAR 里用 ``*/002.jpg`` 匹配一条压缩条目时
    某些包会卡到几分钟（libarchive 在纯顺序流里匹配失败后反复重试），
    而同样的包整包解压只要零点几秒。超时一次就记住，后面直接整包解。
    """
    tar = find_bsdtar()
    if not tar:
        raise SourceError("系统缺少可用的解压工具（未找到 bsdtar / unrar / 7z）")
    key = _arc_key(path)
    base = _basename(name)

    # 1) 原名。取不到说明这个包的名字编码和 bsdtar 对不上，后面别再白试
    if key not in _arc_path_mismatch:
        data = _bsdtar_read(path, tar, name)
        if data is not None:
            return data
        _arc_path_mismatch.add(key)

    # 2) 纯 ASCII 通配符：同名的条目只有一个时才敢用
    if key not in _arc_glob_slow and sum(1 for n in names if _basename(n) == base) == 1:
        for pat in (f"*/{base}", base):
            try:
                data = _bsdtar_read(path, tar, pat, timeout=_GLOB_TIMEOUT)
            except subprocess.TimeoutExpired:
                _arc_glob_slow.add(key)
                break
            if data is not None:
                return data

    # 3) 整包解一次到临时目录（有缓存）
    _tmp, files = _bulk_extract(path)
    same = [p for p in files if p.name == base]
    picked = same[0] if len(same) == 1 else None
    if picked is None and len(files) == len(names):
        # 解出来的文件名被系统转码弄乱了，就按同样的自然序对号入座
        picked = files[index]
    if picked is None:
        raise SourceError("提取图片失败：压缩包内文件名无法匹配")
    try:
        return picked.read_bytes()
    except OSError as exc:
        raise SourceError(f"读取解压出的图片失败：{exc}") from exc



class SevenZipSource(ComicSource):
    kind = "7z"

    def __init__(self, path: Path):
        super().__init__(path)
        try:
            import py7zr
            from py7zr.io import BytesIOFactory  # noqa: F401
        except ImportError as exc:
            raise SourceError("缺少 py7zr 库，无法读取 7z（请运行 pip install py7zr）") from exc
        self._py7zr = py7zr
        try:
            self._zf = py7zr.SevenZipFile(self.path, "r")
            names = self._zf.getnames()
        except Exception as exc:
            msg = str(exc)
            if "assword" in msg or "encrypt" in msg.lower():
                raise SourceError("该压缩包已加密，无法读取") from exc
            raise SourceError(f"7z 文件损坏或格式不正确：{exc}") from exc
        names = [n for n in names if is_image_name(n)]
        names.sort(key=natural_key)
        self.names = names
        if not names:
            self.close()
            raise SourceError("压缩包内没有找到图片")

    def page_bytes(self, index: int) -> Tuple[bytes, str]:
        name = self.names[index]
        try:
            from py7zr.io import BytesIOFactory

            factory = BytesIOFactory(limit=256 * 1024 * 1024)
            # py7zr 1.1+ 用 extract(factory=...) 把内容解到内存
            self._zf.reset()
            self._zf.extract(targets=[name], factory=factory)
            buf = factory.get(name)
            buf.seek(0)
            return buf.read(), mime_of(name)
        except Exception:
            pass  # 交给 bsdtar 兜底
        try:
            return bsdtar_extract(self.path, name), mime_of(name)
        except SourceError:
            # 名字编码对不上时再走通配符 / 整包解压，别一页都取不出来
            return page_from_bsdtar(self.path, name, index, self.names), mime_of(name)
        except Exception as exc:
            raise SourceError(f"读取 7z 内文件失败：{exc}") from exc

    def close(self) -> None:
        try:
            self._zf.close()
        except Exception:
            pass


class RarSource(ComicSource):
    kind = "rar"

    def __init__(self, path: Path):
        super().__init__(path)
        try:
            import rarfile
        except ImportError as exc:
            raise SourceError("缺少 rarfile 库，无法读取 RAR（请运行 pip install rarfile）") from exc
        self._rf = rarfile.RarFile(str(self.path))
        self._via_bsdtar = False  # 一旦发现 rarfile 解不动，后面几页直接走 bsdtar
        try:
            infos = [i for i in self._rf.infolist() if not i.isdir() and is_image_name(i.filename)]
            infos.sort(key=lambda i: natural_key(i.filename))
            self._infos = infos
            self.names = [i.filename for i in infos]
        except Exception as exc:
            raise SourceError(f"解析 RAR 目录失败：{exc}") from exc
        if not self.names:
            raise SourceError("压缩包内没有找到图片")

    def page_bytes(self, index: int) -> Tuple[bytes, str]:
        info = self._infos[index]
        if not self._via_bsdtar:
            try:
                with self._rf.open(info) as fh:
                    return fh.read(), mime_of(info.filename)
            except Exception:
                # rarfile 只是「目录读取器」：条目用 RAR 压缩算法存的时候它得借
                # 外部 unrar，本机没装就报 Cannot find working tool（未压缩存储的
                # 条目它自己能读，所以现象是前几页正常、后面全挂）。
                # 交给系统 bsdtar(libarchive) 兜底。
                self._via_bsdtar = True
        try:
            return page_from_bsdtar(self.path, info.filename, index, self.names), mime_of(info.filename)
        except SourceError as exc:
            raise SourceError(f"读取 RAR 内图片失败：{exc}") from exc

    def close(self) -> None:
        try:
            self._rf.close()
        except Exception:
            pass


class BsdtarSource(ComicSource):
    """兜底实现：用系统 bsdtar(libarchive) 读取 RAR / 7z。"""

    kind = "tar"

    def __init__(self, path: Path, kind: str = "rar"):
        super().__init__(path)
        self.kind = kind
        self._tar = find_bsdtar()
        if not self._tar:
            raise SourceError("系统缺少可用的解压工具（未找到 bsdtar / unrar / 7z）")
        try:
            res = subprocess.run(
                [self._tar, "-tf", str(self.path)],
                capture_output=True, timeout=120,
            )
        except Exception as exc:
            raise SourceError(f"调用系统解压工具失败：{exc}") from exc
        if res.returncode != 0:
            raise SourceError(f"无法列出压缩包内容：{_decode(res.stderr)[:200]}")
        names = [n for n in _decode(res.stdout).splitlines() if is_image_name(n.strip())]
        names = [n.strip() for n in names if n.strip()]
        names.sort(key=natural_key)
        self.names = names
        if not names:
            raise SourceError("压缩包内没有找到图片")

    def page_bytes(self, index: int) -> Tuple[bytes, str]:
        # 原名叫不上就试 ASCII 通配符，再不行整包解一次，逻辑统一在 page_from_bsdtar
        name = self.names[index]
        return page_from_bsdtar(self.path, name, index, self.names), mime_of(name)


class PdfSource(ComicSource):
    kind = "pdf"

    def __init__(self, path: Path):
        super().__init__(path)
        try:
            import pymupdf
        except ImportError:
            try:
                import fitz as pymupdf  # 旧版命名
            except ImportError as exc:
                raise SourceError("缺少 pymupdf 库，无法读取 PDF（请运行 pip install pymupdf）") from exc
        self._fitz = pymupdf
        try:
            self._doc = pymupdf.open(str(self.path))
        except Exception as exc:
            raise SourceError(f"PDF 文件损坏：{exc}") from exc
        if getattr(self._doc, "needs_pass", False):
            raise SourceError("该 PDF 已加密，无法读取")
        self.names = [f"page-{i + 1:04d}" for i in range(self._doc.page_count)]
        if not self.names:
            raise SourceError("PDF 内没有页面")

    def page_bytes(self, index: int) -> Tuple[bytes, str]:
        try:
            page = self._doc.load_page(index)
            zoom = 150 / 72.0
            rect = page.rect
            # 超大页面限制像素，避免单页几百 MB 内存
            max_w = 2200
            if rect.width * zoom > max_w:
                zoom = max_w / rect.width
            mat = self._fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            return pix.tobytes("png"), "image/png"
        except Exception as exc:
            raise SourceError(f"渲染 PDF 页面失败：{exc}") from exc

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:
            pass


def open_source(path: Path) -> ComicSource:
    """按扩展名打开对应解析器，失败时尝试兜底方案。"""
    path = Path(path)
    suffix = path.suffix.lower()
    kind = ARCHIVE_EXTS.get(suffix)
    if kind is None:
        if suffix == ".pdf":
            raise SourceError(PDF_HINT)
        raise SourceError(f"不支持的文件类型：{path.suffix}")

    errors: List[str] = []

    if kind == "zip":
        try:
            return ZipSource(path)
        except SourceError as exc:
            errors.append(str(exc))
        # 有些压缩包实际是 rar/7z 改了后缀
        for alt in (RarSource, SevenZipSource):
            try:
                return alt(path)
            except Exception:
                continue
        raise SourceError(errors[0] if errors else "无法读取该压缩包")

    if kind == "7z":
        try:
            return SevenZipSource(path)
        except SourceError as exc:
            errors.append(str(exc))
        try:
            return BsdtarSource(path, "7z")
        except SourceError as exc:
            errors.append(str(exc))
        raise SourceError(errors[0])

    if kind == "rar":
        try:
            return RarSource(path)
        except Exception as exc:
            errors.append(str(exc))
        try:
            return BsdtarSource(path, "rar")
        except SourceError as exc:
            errors.append(str(exc))
        raise SourceError(errors[-1] if errors else "无法读取 RAR")


# --------------------------------------------------------------------------
# 页面字节的内存缓存：翻页时避免反复解压同一个文件
# --------------------------------------------------------------------------

_cache_lock = threading.Lock()
_page_cache: "OrderedDict[Tuple[str, int], Tuple[bytes, str]]" = OrderedDict()
_cache_bytes = 0
_CACHE_LIMIT = 160 * 1024 * 1024


def read_page(path: Path, index: int) -> Tuple[bytes, str]:
    """读取某本漫画第 index 页（从 0 开始），带内存缓存。"""
    key = (str(path), int(index))
    with _cache_lock:
        hit = _page_cache.get(key)
        if hit is not None:
            _page_cache.move_to_end(key)
            return hit

    with open_source(path) as src:
        data, mime = src.page_bytes(index)

    global _cache_bytes
    with _cache_lock:
        if len(data) <= 8 * 1024 * 1024:  # 超大图不入缓存
            _page_cache[key] = (data, mime)
            _cache_bytes += len(data)
            while _cache_bytes > _CACHE_LIMIT and _page_cache:
                _, (old, _m) = _page_cache.popitem(last=False)
                _cache_bytes -= len(old)
    return data, mime


def clear_page_cache() -> None:
    global _cache_bytes
    with _cache_lock:
        _page_cache.clear()
        _cache_bytes = 0
