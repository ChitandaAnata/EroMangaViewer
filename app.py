"""MangaShelf 启动入口。

用法：
    python app.py                 # 默认启动并自动打开浏览器
    python app.py --port 9000     # 指定端口
    python app.py --no-browser    # 不自动打开浏览器
    python app.py --scan-only     # 只扫描一次库然后退出
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import api, config, db, scanner, taglib  # noqa: E402

BANNER = r"""
  __  __                        ____  _          _ _ 
 |  \/  | __ _ _ __   __ _ ___  / ___|| |__   ___| | |
 | |\/| |/ _` | '_ \ / _` / __| \___ \| '_ \ / _ \ | |
 | |  | | (_| | | | | (_| \__ \  ___) | | | |  __/ | |
 |_|  |_|\__,_|_| |_|\__, |___/ |____/|_| |_|\___|_|_|
                     |___/                             
"""


def port_available(host: str, port: int) -> bool:
    # Windows 的 SO_REUSEADDR 允许重复绑定已占用的端口，不能靠 bind 判断。
    # 先试着连一下：连得上说明已经有人在监听。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        try:
            probe.connect((host, port))
            return False
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, preferred: int) -> int:
    for offset in range(0, 20):
        port = preferred + offset
        if port_available(host, port):
            return port
    raise SystemExit(f"端口 {preferred}~{preferred + 19} 全被占用，请用 --port 指定其他端口。")


def lan_ip() -> str:
    """取本机在局域网里的地址，只用来打印提示。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="MangaShelf - 本地漫画管理器")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    parser.add_argument("--host", default=None, help="监听地址")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--exact-port", action="store_true", help="端口被占用时直接退出，不自动换端口（桌面外壳用）")
    parser.add_argument("--scan-only", action="store_true", help="只扫描漫画库后退出")
    parser.add_argument("--force-scan", action="store_true", help="强制重新解析所有压缩包")
    args = parser.parse_args()

    config.ensure_dirs()
    db.init_db()
    print(BANNER)

    # 先把库根的标签目录读回来：换设备后数据库是空的，这一步让侧栏的标签先齐，
    # 不用等上千本漫画扫完才出现。
    try:
        taglib.merge()
    except Exception as exc:
        print(f"[标签目录] 读取失败，稍后扫描时会重试：{exc}")

    lib = config.library_path()
    if not lib.exists():
        lib.mkdir(parents=True, exist_ok=True)
        print(f"[库] 已创建漫画库目录：{lib}")
        print("     把 .zip / .cbz / .rar / .cbr / .7z / .cb7 / .pdf 漫画放进去即可。")

    if args.scan_only:
        print(f"[扫描] {lib}")
        scanner.scan_async(force=args.force_scan)
        scanner.wait(3600)
        st = scanner.STATE.snapshot()
        print(f"[完成] 新增 {st['added']}，更新 {st['updated']}，移除 {st['removed']}，失败 {st['failed']}")
        for err in st["errors"]:
            print(f"  ! {err['file']}: {err['error']}")
        return

    cfg = config.load_config()
    from server.api import build_server

    host = args.host or cfg.get("host") or "127.0.0.1"
    want = args.port or int(cfg.get("port") or 8765)
    if args.exact_port:
        # 桌面外壳已经挑好了空闲端口，这里必须用它；换端口会让外壳连不上。
        if not port_available(host, want):
            print(f"[错误] 端口 {want} 已被占用", file=sys.stderr)
            raise SystemExit(2)
        port = want
    else:
        port = pick_port(host, want)
    httpd = build_server(host, port)
    # 监听 0.0.0.0（给局域网看）时，自动打开的地址必须写回环地址：
    # 0.0.0.0 不是可访问的目的地址，浏览器（尤其挂了代理的）会直接 502。
    open_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    open_url = f"http://{open_host}:{port}"

    print(f"[库] {lib}")
    print(f"[服务] {open_url}")
    # 进程身份：排查「打不开资源管理器」这类权限问题（低完整性/受限令牌的进程驱不动 shell）
    _pi = api.proc_context()
    if "integrity" in _pi:
        print(f"[进程] 完整性级别 {_pi['integrity']}"
              f"{'（管理员）' if _pi.get('is_admin') else ''}  会话 {_pi.get('session', '?')}")
    if host in ("0.0.0.0", "::"):
        ip = lan_ip()
        if ip:
            print(f"[局域网] http://{ip}:{port}  （同一网络里的手机 / 平板可以直接打开）")
    print("[提示] 关闭此窗口或按 Ctrl+C 停止服务\n")

    # 启动后台扫描（首次运行会为每本漫画生成封面）
    scanner.scan_async(force=args.force_scan)
    # 不启动目录监听：新文件 / 删除 / 改名都由用户在界面上点「扫描」手动触发。
    # 需要恢复自动监听时，取消下面这行的注释即可。
    # scanner.start_watcher(15.0)

    if not args.no_browser and cfg.get("open_browser", True):
        threading.Thread(
            target=lambda: (time.sleep(0.9), webbrowser.open(open_url)),
            daemon=True,
        ).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[退出] 正在关闭服务…")
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
