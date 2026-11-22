"""RAR 退化路径验证：rarfile 解不动时，必须自动交给系统 bsdtar 继续取图。

背景：Python 的 rarfile 只内置了「未压缩存储(STORE)」条目的解码，其它条目要
调用外部 unrar。本机没有 unrar，于是这类包会「前一两页能看、后面全报
Cannot find working tool」——用户在《あなたのヤミ鎮守府5》上就撞到了。

这里不需要真的 RAR（本机也没有压缩器）：
  用 sys.modules 注入一个假的 rarfile，它列目录正常、但只有前两个条目能读，
  其余抛 RarCannotExec。包本体是一个 zip（libarchive 一样能读），
  这样就能精确复现「前半能读、后半要退路」的状态。

用法：D:/python/python.exe tools/test_rar_fallback.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import types
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from server import archive  # noqa: E402

BASE = Path(tempfile.mkdtemp(prefix="mangashelf-rar-fb-"))
PKG = BASE / "book.zip"
PAGES = 8

passed = 0
failed = 0
fake_open_calls = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label} {extra}")


def make_book() -> None:
    with zipfile.ZipFile(PKG, "w", zipfile.ZIP_STORED) as z:
        for i in range(PAGES):
            im = Image.new("RGB", (200, 280), (30 + i * 20, 40, 90))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70)
            z.writestr(f"dir/{i:03d}.jpg", buf.getvalue())


class _Info:
    def __init__(self, name: str, rank: int):
        self.filename = name
        self._rank = rank

    def isdir(self) -> bool:
        return False


class _FakeRar:
    """只有前 2 条能读，其余模拟「缺少 unrar」。"""

    def __init__(self, path: str):
        self._zf = zipfile.ZipFile(path)
        self._names = [i for i in self._zf.namelist() if not i.endswith("/")]

    def infolist(self):
        return [_Info(n, i) for i, n in enumerate(sorted(self._names))]

    def open(self, info):
        global fake_open_calls
        fake_open_calls += 1
        if info._rank >= 2:
            raise RuntimeError("Cannot find working tool")
        return self._zf.open(info.filename)

    def close(self):
        self._zf.close()


def install_fake_rarfile() -> None:
    mod = types.ModuleType("rarfile")
    mod.RarFile = _FakeRar
    sys.modules["rarfile"] = mod


def main() -> None:
    make_book()
    install_fake_rarfile()

    print("1) rarfile 能读的页仍走 rarfile（快路径不变）")
    src = archive.RarSource(PKG)
    check("页数对", src.page_count == PAGES, str(src.page_count))
    check("第 1 页取到", len(src.page_bytes(0)[0]) > 0)
    check("第 2 页取到", len(src.page_bytes(1)[0]) > 0)
    check("前两页没有触发 bsdtar", src._via_bsdtar is False)

    print("2) 第 3 页 rarfile 解不动 → 自动交给 bsdtar")
    data, mime = src.page_bytes(2)
    im = Image.open(io.BytesIO(data))
    im.load()
    check("第 3 页取到有效图片", im.size == (200, 280), str(im.size))
    check("mime 是 jpeg", mime == "image/jpeg", mime)
    check("已切到 bsdtar 模式", src._via_bsdtar is True)

    print("3) 后面几页不再白试 rarfile")
    before = fake_open_calls
    for i in (3, 4, 5):
        src.page_bytes(i)
    check("没有再多调 rarfile.open", fake_open_calls == before, f"{fake_open_calls} vs {before}")

    print("4) 全部页都能读")
    ok = 0
    for i in range(PAGES):
        data, _ = src.page_bytes(i)
        Image.open(io.BytesIO(data)).load()
        ok += 1
    check(f"{PAGES} 页全部有效", ok == PAGES, str(ok))
    src.close()

    print("5) 通配符卡死时会记住并改走整包解压")
    archive._arc_glob_slow.clear()
    archive._arc_path_mismatch.clear()
    key = archive._arc_key(PKG)
    archive._arc_path_mismatch.add(key)      # 假装原名对不上
    orig = archive._bsdtar_read
    calls = []

    def slow_read(path, tar, pattern, timeout=600):
        calls.append(pattern)
        if pattern.startswith("*/"):
            raise archive.subprocess.TimeoutExpired(cmd="tar", timeout=timeout)
        return orig(path, tar, pattern, timeout)

    archive._bsdtar_read = slow_read
    try:
        data = archive.page_from_bsdtar(PKG, "dir/002.jpg", 2, [f"dir/{i:03d}.jpg" for i in range(PAGES)])
        check("超时后仍然取到图", len(data) > 0)
        check("这个包被记成「通配符会卡」", key in archive._arc_glob_slow)
    finally:
        archive._bsdtar_read = orig

    calls.clear()
    archive._bsdtar_read = slow_read
    try:
        archive.page_from_bsdtar(PKG, "dir/003.jpg", 3, [f"dir/{i:03d}.jpg" for i in range(PAGES)])
        check("下次不再试通配符", not any(p.startswith("*/") for p in calls), str(calls))
    finally:
        archive._bsdtar_read = orig
        archive.clear_bulk_cache()

    print(f"\n结果：{passed} 通过，{failed} 失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        import shutil

        shutil.rmtree(BASE, ignore_errors=True)
