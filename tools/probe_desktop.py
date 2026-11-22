"""启动桌面程序，检查它真的把网页端渲染出来了，并走一遍「打开漫画 → 阅读」。

做的事情：
  1. 带 WebView2 远程调试端口启动 dist/MangaShelf/MangaShelf.exe
  2. 用 CDP 读窗口里的真实 DOM（卡片数、封面是否解码、侧栏栏目）
  3. 模拟点开第一本、点「开始阅读」，确认阅读器出图
  4. 用内核截图（不受窗口遮挡影响），另存一张窗口区域图
  5. 结束时关掉进程

用法：
    D:\\python\\python.exe tools/probe_desktop.py            # 检查完关闭
    D:\\python\\python.exe tools/probe_desktop.py --keep     # 留着窗口手动点
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "MangaShelf" / "MangaShelf.exe"
# --exe <路径>：验证别处的程序，比如根目录那个开发版（MangaShelf.exe）
for _i, _a in enumerate(sys.argv):
    if _a == "--exe" and _i + 1 < len(sys.argv):
        EXE = Path(sys.argv[_i + 1]).expanduser().resolve()
SHOT = ROOT / "docs" / "screenshots" / "desktop_window.png"
SHOT_READER = ROOT / "docs" / "screenshots" / "desktop_reader.png"
CDP_PORT = 9333

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()


def find_window(pid: int):
    found: list = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value != pid:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        found.append((hwnd, buf.value, r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(proc(cb), 0)
    if not found:
        return None
    return max(found, key=lambda x: (x[4] - x[2]) * (x[5] - x[3]))


class Cdp:
    def __init__(self, ws_url: str):
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=20)
        self.mid = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.mid:
                return msg.get("result", {})

    def js(self, expr: str):
        res = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return res.get("result", {}).get("value")

    def shot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png"})["data"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def main() -> int:
    if not EXE.exists():
        print(f"没有程序：{EXE}\n先跑 python tools/build_desktop.py", file=sys.stderr)
        return 1

    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={CDP_PORT}"

    print("启动桌面程序…", flush=True)
    proc = subprocess.Popen([str(EXE)], env=env, cwd=str(EXE.parent))

    win = None
    for _ in range(60):
        time.sleep(0.5)
        win = find_window(proc.pid)
        if win:
            break
        if proc.poll() is not None:
            print(f"程序已退出，exit={proc.returncode}", file=sys.stderr)
            print("看看 data\\startup.log", file=sys.stderr)
            return 1
    if not win:
        print("没等到窗口", file=sys.stderr)
        proc.terminate()
        return 1

    hwnd, title, l, t, r, b = win
    print(f"窗口：{title!r} {r - l}x{b - t}")

    pages = []
    for _ in range(100):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=2))
            pages = [x for x in tabs if x.get("type") == "page"]
            if pages:
                break
        except Exception:
            pass
        time.sleep(0.25)

    if not pages:
        print("CDP 没就绪（WebView2 可能没起来），只能截窗口图", file=sys.stderr)
        _grab(hwnd, l, t, r, b, SHOT)
        _finish(proc)
        return 1

    cdp = Cdp(pages[0]["webSocketDebuggerUrl"])
    cdp.call("Page.enable")

    for _ in range(100):
        if cdp.js('document.querySelectorAll(".card").length'):
            break
        time.sleep(0.25)

    shelf = cdp.js(
        """(() => ({
          标题: document.title,
          卡片数: document.querySelectorAll('.card').length,
          封面已解码: [...document.querySelectorAll('.card img')].filter(i => i.complete && i.naturalWidth > 0).length,
          侧栏: [...document.querySelectorAll('.nav-item')].map(e => e.textContent.trim().split('\\n')[0]),
          搜索框: !!document.getElementById('searchInput'),
          视口: innerWidth + 'x' + innerHeight,
          缩放: devicePixelRatio,
          横向滚动条: document.documentElement.scrollWidth > document.documentElement.clientWidth
        }))()"""
    )
    print("书架：", json.dumps(shelf, ensure_ascii=False))
    cdp.shot(SHOT)

    # 点开第一本 → 详情页
    cdp.js("document.querySelector('.card')?.click()")
    time.sleep(1.5)
    detail = cdp.js(
        """(() => {
          const d = document.getElementById('detail');
          const read = document.getElementById('detailReadLabel');
          return {
            详情可见: d ? !d.hidden : false,
            标题: document.getElementById('detailTitle')?.textContent?.trim(),
            分类: document.getElementById('detailCat')?.value,
            阅读按钮: read?.textContent?.trim(),
            整本预览图: document.querySelectorAll('#detailPages img, .detail-pages img').length
          };
        })()"""
    )
    print("详情页：", json.dumps(detail, ensure_ascii=False))

    # 开始阅读
    cdp.js("document.getElementById('detailRead')?.click()")
    ok = False
    for _ in range(40):
        time.sleep(0.25)
        reader = cdp.js(
            """(() => {
              const box = document.getElementById('reader');
              const imgs = [...document.querySelectorAll('#reader img')];
              return {
                阅读器可见: box ? !box.hidden : false,
                页面图: imgs.length,
                已解码: imgs.filter(i => i.complete && i.naturalWidth > 0).length,
                单页宽: imgs[0]?.naturalWidth || 0
              };
            })()"""
        )
        if reader and reader.get("已解码"):
            ok = True
            break
    print("阅读器：", json.dumps(reader if 'reader' in dir() else {}, ensure_ascii=False))
    cdp.shot(SHOT_READER)
    print("阅读器出图：" + ("是" if ok else "否"))

    _grab(hwnd, l, t, r, b, SHOT.with_name("desktop_frame.png"))
    print(f"截图：{SHOT} / {SHOT_READER}")
    cdp.close()
    _finish(proc)
    return 0 if ok else 2


def _grab(hwnd, l, t, r, b, path: Path) -> None:
    from PIL import ImageGrab

    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.2)
    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=(l, t, r, b)).save(path)


def _finish(proc) -> None:
    if "--keep" in sys.argv:
        print("保留窗口，不关闭")
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()
    print("已关闭")


if __name__ == "__main__":
    raise SystemExit(main())
