"""组装可运行的桌面程序目录。

步骤：
  1. dotnet publish 外壳（自包含单文件，用户机器不用装 .NET）
  2. 把 dist/backend 里打好的后端复制进来
  3. 复制网页前端 web/
  4. 输出 dist/MangaShelf/MangaShelf.exe

用法：
    python tools/build_desktop.py            # 只组装（后端已经打好时）
    python tools/build_desktop.py --backend   # 先重新打后端再组装
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "MangaShelf"
SHELL_OUT = ROOT / "build" / "shell"
BACKEND_SRC = ROOT / "dist" / "backend" / "MangaShelfServer"


def log(msg: str) -> None:
    print(f"• {msg}", flush=True)


def archive(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path.rename(path.with_name(f"{path.name}-old-{stamp}"))


def main() -> int:
    if "--backend" in sys.argv:
        log("重新打包后端…")
        rc = subprocess.run([sys.executable, str(ROOT / "tools" / "build_backend.py")], cwd=str(ROOT)).returncode
        if rc != 0:
            return rc

    if not BACKEND_SRC.exists():
        print(f"没有后端产物：{BACKEND_SRC}\n先跑 python tools/build_backend.py", file=sys.stderr)
        return 1

    log("发布外壳（自包含单文件）…")
    archive(SHELL_OUT)
    SHELL_OUT.mkdir(parents=True, exist_ok=True)
    rc = subprocess.run(
        [
            "dotnet", "publish", "desktop/MangaShelf.csproj",
            "-c", "Release",
            "-r", "win-x64",
            "--self-contained", "true",
            "-p:PublishSingleFile=true",
            "-p:EnableCompressionInSingleFile=true",
            "-p:DebugType=none",
            "-o", str(SHELL_OUT),
            "-v", "q",
            "--nologo",
        ],
        cwd=str(ROOT),
    ).returncode
    if rc != 0:
        print("发布外壳失败", file=sys.stderr)
        return rc

    exe = SHELL_OUT / "MangaShelf.exe"
    if not exe.exists():
        print(f"没找到 {exe}", file=sys.stderr)
        return 1

    log("组装程序目录…")
    archive(DIST)
    DIST.mkdir(parents=True, exist_ok=True)

    shutil.copy2(exe, DIST / "MangaShelf.exe")
    loader = SHELL_OUT / "WebView2Loader.dll"
    if loader.exists():
        shutil.copy2(loader, DIST / "WebView2Loader.dll")

    shutil.copytree(BACKEND_SRC, DIST / "backend", dirs_exist_ok=True)
    shutil.copytree(ROOT / "web", DIST / "web", dirs_exist_ok=True)

    # --demo：给验收用的目录预置一份指向示例库的配置；正式安装包不带 data
    if "--demo" in sys.argv:
        import json

        data = DIST / "data"
        data.mkdir(exist_ok=True)
        cfg = {
            "library_path": str((ROOT / "sample_library").resolve()),
            "host": "127.0.0.1",
            "port": 8791,
            "theme": "dark",
            "cover_width": 480,
            "page_thumb_width": 400,
            "default_reader_mode": "paged",
            "open_browser": False,
            "sidecar": True,
        }
        (data / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        log("已写入验收用配置（指向 sample_library）")

    total = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    log(f"完成：{DIST}")
    log(f"体积 {total / 1024 / 1024:.0f} MB，入口 {DIST / 'MangaShelf.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
