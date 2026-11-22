"""把组装好的桌面程序目录做成一个真正的 Windows 安装包。

做的事：
  1. 把 dist/MangaShelf 打成 installer/payload.zip（排除 data，用户数据不进安装包）
  2. dotnet publish 安装器（目标是 .NET Framework 4.8，Win10 1903+/Win11 自带，产物约 60 KB）
  3. 输出根目录 MangaShelf-Setup.exe

为什么自己写安装器而不用 Inno Setup / NSIS：
  那两个都要先把自身解压到 %TEMP% 才能运行，而这台机器的 %TEMP% 是短名路径，
  Inno 用长名路径写入会被拒绝（报“无法创建 is-XXXX.tmp，错误 5”）。
  自写安装器把 payload 直接嵌进 exe 资源，解压到用户选的安装目录，完全不碰 %TEMP%。

用法：
    python tools/build_installer.py            # 用现有 dist 组装安装包
    python tools/build_installer.py --desktop  # 先重新组装桌面程序，再打安装包
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "MangaShelf"
INSTALLER = ROOT / "installer"
PAYLOAD = INSTALLER / "payload.zip"
SETUP_OUT = ROOT / "build" / "setup"
SETUP_EXE = ROOT / "MangaShelf-Setup.exe"


def log(msg: str) -> None:
    print(f"• {msg}", flush=True)


def archive(path: Path) -> None:
    """不直接删旧产物：归档改名，既能回滚也不会触发删除保护。"""
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}-old-{stamp}")
    try:
        path.rename(target)
    except OSError:
        pass


def build_payload() -> int:
    exe = DIST / "MangaShelf.exe"
    if not exe.exists():
        print(f"没有可打包的程序目录：{DIST}\n先跑 python tools/build_desktop.py", file=sys.stderr)
        return 1

    for required in ("backend", "web"):
        if not (DIST / required).is_dir():
            print(f"程序目录缺少 {required}/，请重新组装", file=sys.stderr)
            return 1

    if PAYLOAD.exists():
        PAYLOAD.unlink()

    files = 0
    raw = 0
    with zipfile.ZipFile(PAYLOAD, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(DIST.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(DIST).as_posix()
            # data 是用户数据（配置、数据库、缩略图），绝不进安装包
            if rel == "data" or rel.startswith("data/"):
                continue
            zf.write(path, rel)
            files += 1
            raw += path.stat().st_size

    log(f"payload.zip：{files} 个文件，原始 {raw / 1024 / 1024:.0f} MB，"
        f"压缩后 {PAYLOAD.stat().st_size / 1024 / 1024:.0f} MB")
    return 0


def publish() -> int:
    archive(SETUP_OUT)
    SETUP_OUT.mkdir(parents=True, exist_ok=True)

    # net48 不需要 self-contained / 单文件：输出就是一个几十 KB 的 exe，
    # 运行靠系统自带的 .NET Framework 4.8（Win10 1903+ / Win11 都有）。
    log("编译安装器（.NET Framework 4.8，目标机器无需安装任何运行时）…")
    cmd = [
        "dotnet", "publish", str(INSTALLER / "MangaShelfSetup.csproj"),
        "-c", "Release",
        "-p:DebugType=none",
        "-o", str(SETUP_OUT),
        "-v", "q",
        "--nologo",
    ]
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    if rc != 0:
        print("安装器编译失败", file=sys.stderr)
        return rc

    built = SETUP_OUT / "MangaShelf-Setup.exe"
    if not built.exists():
        print(f"没找到 {built}", file=sys.stderr)
        return 1

    shutil.copy2(built, SETUP_EXE)
    log(f"完成：{SETUP_EXE}（{SETUP_EXE.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


def main() -> int:
    if "--desktop" in sys.argv:
        log("先重新组装桌面程序…")
        rc = subprocess.run([sys.executable, str(ROOT / "tools" / "build_desktop.py")],
                            cwd=str(ROOT)).returncode
        if rc != 0:
            return rc

    rc = build_payload()
    if rc != 0:
        return rc
    return publish()


if __name__ == "__main__":
    raise SystemExit(main())
