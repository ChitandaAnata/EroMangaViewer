"""编译「当前版本」的桌面外壳并启动，用来边改边看效果。

产物就放在仓库根目录：
    MangaShelf.exe        双击即开，跑的是源码（后端用 python app.py）
    WebView2Loader.dll    原生加载器，必须在 exe 旁边

和正式安装版完全隔离：各自的程序目录、各自的 data\\、各自的单实例锁。

⚠️ 历史坑：这个工作区所在的 H:\\Work 曾被宿主显式打上 **Low 完整性标签**，
目录里编译出来的 exe 会继承它。低完整性进程能读盘、能起普通子进程，却驱不动
资源管理器（右键「打开文件夹」失败，自己起 explorer.exe 还会 0xC0000142）。
标签已用 tools/fix_low_integrity.ps1 改回 Medium；如果哪天症状复发，
先 `icacls H:\\Work` 看看 Mandatory Label 是不是又变回 Low。

用法：
    python tools/build_dev.py              # 外壳代码变了才重编，然后启动
    python tools/build_dev.py --force      # 强制重编
    python tools/build_dev.py --no-start   # 只编译，不启动
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "desktop" / "MangaShelf.csproj"
OUT = ROOT / "build" / "dev"

EXE = ROOT / "MangaShelf.exe"
LOADER = ROOT / "WebView2Loader.dll"
HOMEFILE = ROOT / "dev-home.txt"

# 早期版本为了绕开 Low 标签，把外壳放到了用户目录 + 根目录快捷方式。
# 标签已修好，这些都清掉，免得你点到两份不同的「当前版本」。
LEGACY_DEV_HOME = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "MangaShelfDev"
LEGACY = [
    ROOT / "MangaShelf.lnk",
    ROOT / "MangaShelf.dev.bak",
]


def newest_source_mtime() -> float:
    newest = 0.0
    for p in list((ROOT / "desktop").rglob("*.cs")) + [PROJECT]:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            pass
    return newest


def needs_build(force: bool) -> bool:
    if force or not EXE.exists():
        return True
    return EXE.stat().st_mtime < newest_source_mtime()


def build() -> None:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    cmd = [
        "dotnet", "publish", str(PROJECT),
        "-c", "Debug",
        "-r", "win-x64",
        "--self-contained", "false",
        "-p:PublishSingleFile=true",
        "-p:DebugType=none",
        "-p:SatelliteResourceLanguages=zh-Hans",
        "-o", str(OUT),
        "-v", "q",
        "--nologo",
    ]
    print("编译开发版外壳…", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        print("编译失败，检查上面的输出。", file=sys.stderr)
        raise SystemExit(proc.returncode)

    exe = OUT / "MangaShelf.exe"
    if not exe.exists():
        print(f"没找到产物：{exe}", file=sys.stderr)
        raise SystemExit(1)

    # 先删再拷：正在运行的 exe 删不掉，会抛 PermissionError，正好提示「先关窗口」
    try:
        shutil.copy2(exe, EXE)
    except PermissionError:
        print(f"{EXE.name} 正在运行，先关掉它的窗口再执行。", file=sys.stderr)
        raise SystemExit(1)

    # WebView2Loader.dll 是原生的，单文件宿主不会把它塞进 exe；不放在 exe 旁边
    # 窗口会报 DllNotFoundException（ikuyoMail 的工作区也是这样摆的）
    loader = OUT / "WebView2Loader.dll"
    if loader.exists():
        shutil.copy2(loader, LOADER)

    print(f"已更新 {EXE.name}（{EXE.stat().st_size / 1048576:.1f} MB，{time.time() - t0:.0f} 秒）")


def cleanup_legacy() -> None:
    for old in LEGACY:
        try:
            old.unlink()
        except OSError:
            pass
    if LEGACY_DEV_HOME.is_dir():
        try:
            shutil.rmtree(LEGACY_DEV_HOME)
            print(f"已清理旧的 {LEGACY_DEV_HOME}")
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="不管有没有改动都重编")
    ap.add_argument("--no-start", action="store_true", help="只编译，不启动")
    args = ap.parse_args()

    if needs_build(args.force):
        build()
    else:
        print("外壳代码没变，直接用现成的 exe。")

    # 外壳靠它确认「源码就在这一层」（app.py + server + web），
    # 万一把 exe 复制到别处也能找回源码
    HOMEFILE.write_text(str(ROOT), encoding="utf-8")

    cleanup_legacy()

    if args.no_start:
        return

    print("启动开发版…")
    # os.startfile 是真正的「双击」：程序不挂在当前窗口下，关掉这个黑框也不影响它
    os.startfile(str(EXE))


if __name__ == "__main__":
    main()
