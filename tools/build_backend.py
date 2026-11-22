"""把 Python 后端打包成一个独立目录（PyInstaller onedir）。

和以前那版不同：这里只打后端，不带 Qt / WebEngine，产物几十 MB，
网页前端（web/）单独放在程序根目录，改前端不需要重新打包。

用法：
    D:\\python\\python.exe tools\\build_backend.py

产物：dist/backend/MangaShelfServer/{MangaShelfServer.exe, _internal/}
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "backend"
BUILD = ROOT / "build" / "backend"
SPEC = ROOT / "build" / "backend_spec"
NAME = "MangaShelfServer"


def log(msg: str) -> None:
    print(f"• {msg}", flush=True)


def main() -> int:
    for name in ("PyInstaller", "PIL"):
        try:
            __import__(name)
        except ImportError:
            print(f"缺少 {name}，请用装了它的解释器运行，例如 D:\\python\\python.exe", file=sys.stderr)
            return 1

    # 归档上一次产物：直接 rmtree 容易被安全策略拦，改名更稳
    if DIST.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        DIST.rename(DIST.with_name(f"backend-old-{stamp}"))
        log(f"旧产物已归档为 backend-old-{stamp}")

    DIST.parent.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    SPEC.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir", "--console",
        "--name", NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(SPEC),
        # 设置页的「浏览…」会弹系统选目录框
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.filedialog",
        "--hidden-import", "tkinter.messagebox",
        # 明显用不到的大件，排掉能让产物小一圈
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide2",
        "--exclude-module", "PySide6",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "scipy",
        "--exclude-module", "IPython",
        "--exclude-module", "pytest",
        # 不读 PDF：这一项连同它的 cryptography 依赖能省下 60 MB，是最大的一刀
        "--exclude-module", "pymupdf",
        "--exclude-module", "fitz",
        "--exclude-module", "pymupdfb",
        # 代码里一次都没 import，是某条依赖链顺带扫进来的
        "--exclude-module", "win32com",
        "--exclude-module", "pythonwin",
        str(ROOT / "app.py"),
    ]

    log("PyInstaller 打包后端…")
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        print("打包失败", file=sys.stderr)
        return proc.returncode

    exe = DIST / NAME / f"{NAME}.exe"
    if not exe.exists():
        print(f"没找到产物：{exe}", file=sys.stderr)
        return 1

    # 自检：让打好的后端起一次，确认 /api/identity 能通
    log("自检：启动打包后的后端…")
    test = subprocess.Popen(
        [str(exe), "--port", "8796", "--host", "127.0.0.1", "--no-browser", "--exact-port"],
        cwd=str(ROOT),
        env={**__import__("os").environ, "MANGASHELF_HOME": str(ROOT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import urllib.request

        ok = False
        for _ in range(40):
            time.sleep(0.25)
            try:
                with urllib.request.urlopen("http://127.0.0.1:8796/api/identity", timeout=1) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if data.get("app") == "MangaShelf":
                    ok = True
                    break
            except Exception:
                pass
        if not ok:
            print("自检失败：后端没起来", file=sys.stderr)
            return 1

        with urllib.request.urlopen("http://127.0.0.1:8796/api/comics", timeout=5) as resp:
            items = json.loads(resp.read().decode("utf-8")).get("items", [])
        log(f"自检通过：identity 正常，作品 {len(items)} 本")
    finally:
        test.terminate()
        try:
            test.wait(timeout=5)
        except Exception:
            test.kill()

    total = sum(p.stat().st_size for p in (DIST / NAME).rglob("*") if p.is_file())
    log(f"完成：{DIST / NAME}（{total / 1024 / 1024:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
