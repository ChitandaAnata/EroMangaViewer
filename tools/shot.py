"""用 Chrome DevTools Protocol 截取页面状态，便于检查 UI。

用法：python tools/shot.py <场景> [参数]
场景：shelf / list / tags / reader <id> / detail <id> / detail-mobile <id>
      / detail-light <id> / detail-edit <id> / detail-edit-mobile <id> / mobile / light
      / settings / settings-tags / settings-sidecar / settings-appearance
      / settings-mobile / settings-tags-mobile / newtag
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333
# 服务地址默认 8765；8765 被别的程序占着时，用 MANGASHELF_BASE 指到实际端口，
# 这样所有基于本模块的端到端测试都能照常跑（例：MANGASHELF_BASE=http://127.0.0.1:8845）
BASE = (os.environ.get("MANGASHELF_BASE") or "http://127.0.0.1:8765").rstrip("/")
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
USER_DIR = str(Path(tempfile.gettempdir()) / "mangashelf_chrome")


def library_dir() -> Path:
    """当前服务实际使用的库目录。

    测试要往库里塞临时样本书，不能再写死 sample_library —— 库可能被指到别的盘。
    拿不到时退回仓库自带的 sample_library。
    """
    try:
        with urllib.request.urlopen(BASE + "/api/state", timeout=10) as resp:
            lib = json.load(resp).get("library")
        if lib:
            return Path(lib)
    except Exception:
        pass
    return Path(__file__).resolve().parent.parent / "sample_library"


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.mid = 0

    def cmd(self, method: str, params=None):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.mid:
                return msg.get("result", {})

    def js(self, expr: str, await_promise: bool = False):
        res = self.cmd("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        return res.get("result", {}).get("value")

    def shot(self, path: Path):
        data = self.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path.write_bytes(base64.b64decode(data["data"]))
        print(f"  -> {path}  ({path.stat().st_size // 1024} KB)")


def wait_for(ws: CDP, expr: str, timeout: float = 15.0) -> bool:
    """轮询等待 JS 表达式为真，避免用固定 sleep。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if ws.js(expr):
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def main():
    scene = sys.argv[1] if len(sys.argv) > 1 else "shelf"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    width, height = 1440, 900
    if scene == "mobile" or scene.endswith("-mobile"):
        width, height = 390, 844

    target = BASE + "/"
    if scene.startswith("detail"):
        cid = arg
        if not cid:
            items = json.load(urllib.request.urlopen(BASE + "/api/comics?sort=pages"))["items"]
            cid = items[0]["id"]
        target = f"{BASE}/#comic={cid}"
    elif scene == "tags" and arg:
        target = f"{BASE}/#comic={arg}"
    elif scene == "reader" and arg:
        target = f"{BASE}/#read={arg}"

    proc = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={USER_DIR}",
        f"--window-size={width},{height}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                pages = [t for t in tabs if t.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if not ws_url:
            raise SystemExit("无法连接 Chrome 调试端口")

        cdp = CDP(ws_url)
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Network.enable")
        cdp.cmd("Network.setCacheDisabled", {"cacheDisabled": True})
        cdp.cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 2, "mobile": scene == "mobile",
        })

        cdp.cmd("Page.navigate", {"url": target})
        wait_for(cdp, "document.readyState === 'complete'")
        wait_for(cdp, "document.querySelectorAll('.card').length > 0", 20)

        if scene == "list":
            cdp.js("document.querySelector('[data-view=list]').click()")
            wait_for(cdp, "document.querySelector('#shelf').dataset.view === 'list'")
        elif scene == "tags":
            if arg:
                wait_for(cdp, "document.getElementById('detail').hidden === false", 20)
                wait_for(cdp, "document.querySelectorAll('.page-cell').length > 0", 25)
                for _ in range(3):
                    cdp.js("(() => { const b = document.getElementById('detailEdit'); if (b.getAttribute('aria-pressed') !== 'true') b.click(); })()")
                    cdp.js("document.getElementById('dAddTag').click()")
                    if wait_for(cdp, "document.getElementById('tagModal').hidden === false", 5):
                        break
                    time.sleep(0.4)
            else:
                cdp.js("document.querySelector('.card [data-act=tags]').click()")
                wait_for(cdp, "document.getElementById('tagModal').hidden === false")
            time.sleep(0.4)
        elif scene.startswith("settings"):
            page = ""
            if scene not in ("settings", "settings-mobile", "settings-light"):
                page = scene[len("settings-"):]
                if page.endswith("-mobile"):
                    page = page[:-len("-mobile")]
                if page.endswith("-light"):
                    page = page[:-len("-light")]
            cdp.js("document.getElementById('settingsBtn').click()")
            wait_for(cdp, "document.getElementById('settingsPage').hidden === false")
            if page:
                cdp.js(f"document.querySelector('.set-row[data-go=\"{page}\"]').click()")
                wait_for(cdp, f"!document.querySelector('.set-view[data-view=\"{page}\"]').hidden")
            time.sleep(0.35)
            if scene.endswith("-light"):
                # 设置加载完会把主题重置成保存值，所以放在最后再切
                cdp.js("document.body.dataset.theme = 'light'")
                time.sleep(0.25)
        elif scene == "newtag":
            cdp.js("document.getElementById('settingsBtn').click()")
            wait_for(cdp, "document.getElementById('settingsPage').hidden === false")
            cdp.js("document.querySelector('.set-row[data-go=\"tags\"]').click()")
            wait_for(cdp, "!document.querySelector('.set-view[data-view=\"tags\"]').hidden")
            cdp.js("document.getElementById('tagAddBtn').click()")
            wait_for(cdp, "document.getElementById('newTagModal').hidden === false")
        elif scene == "light":
            cdp.js("document.body.dataset.theme='light'")
        elif scene == "reader":
            wait_for(cdp, "document.getElementById('reader').hidden === false", 15)
        elif scene.startswith("detail"):
            wait_for(cdp, "document.getElementById('detail').hidden === false", 15)
            wait_for(cdp, "document.querySelectorAll('.page-cell img').length > 0", 20)
            if "edit" in scene:
                cdp.js("document.getElementById('detailEdit').click()")
                wait_for(cdp, "document.getElementById('detailEdit').getAttribute('aria-pressed') === 'true'")
                time.sleep(0.3)
            if scene == "detail-light":
                cdp.js("document.body.dataset.theme='light'")
            if scene.endswith("-pages"):
                cdp.js("document.querySelector('.pages-panel').scrollIntoView({block:'start'})")
                time.sleep(0.4)

        # 等图片解码完成
        if scene == "reader":
            wait_for(cdp, "(() => { const i = document.getElementById('rImg'); return i && i.complete && i.naturalWidth > 0; })()", 15)
        elif scene.startswith("detail"):
            expr = "[...document.querySelectorAll('.page-cell img')]"
            if scene.endswith("-pages"):
                expr += ".slice(0, 14)"
            wait_for(cdp, expr + ".every(i => i.complete && i.naturalWidth > 0)", 25)
        else:
            wait_for(cdp, "[...document.querySelectorAll('.card img')].every(i => i.complete)", 20)
        time.sleep(0.5)

        out = OUT_DIR / f"{scene}.png"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        cdp.shot(out)

        # 输出一些诊断信息
        if scene.startswith("settings") or scene == "newtag":
            info = cdp.js("""(() => {
                const page = document.getElementById('settingsPage');
                const view = [...document.querySelectorAll('.set-view')].find(v => !v.hidden);
                return {
                    open: !page.hidden,
                    title: document.getElementById('setTitle').textContent,
                    sub: document.getElementById('setSub').textContent,
                    back: document.getElementById('setBackLabel').textContent,
                    view: view ? view.dataset.view : '',
                    rows: [...document.querySelectorAll('.set-row')].map(r => r.dataset.go),
                    tagRows: document.querySelectorAll('#tagAdminList .tag-admin-row').length,
                    themeValue: document.getElementById('setThemeValue').textContent,
                    errors: window.__errors || []
                };
            })()""")
        elif scene.startswith("detail"):
            info = cdp.js("""(() => ({
                title: document.getElementById('detailTitle').textContent,
                crumb: document.getElementById('detailCrumb').textContent,
                tags: [...document.querySelectorAll('#detailTags .d-tag')].map(e => e.textContent.trim()),
                meta: [...document.querySelectorAll('#detailMeta .meta-item')].map(e => e.innerText.replace('\\n', ': ')),
                pagesCount: document.getElementById('pagesCount').textContent,
                thumbs: document.querySelectorAll('.page-cell img').length,
                thumbsOk: [...document.querySelectorAll('.page-cell img')].filter(i => i.naturalWidth > 0).length,
                detailVisible: !document.getElementById('detail').hidden,
                shelfHidden: document.getElementById('shelfView').hidden,
                errors: window.__errors || []
            }))()""")
        else:
            info = cdp.js("""(() => ({
                cards: document.querySelectorAll('.card').length,
                imgsOk: [...document.querySelectorAll('.card img')].filter(i => i.naturalWidth > 0).length,
                imgsTotal: [...document.querySelectorAll('.card img')].length,
                scanBarHidden: document.getElementById('scanBar').hidden,
                title: document.getElementById('viewTitle').textContent,
                sub: document.getElementById('viewSub').textContent,
                errors: window.__errors || []
            }))()""")
        if scene == "tags" and isinstance(info, dict):
            info["tagSelects"] = cdp.js("""(() => {
                const box = document.getElementById('tagSelects');
                if (!box) return null;
                return [...box.querySelectorAll('select')].map(s => ({
                    cat: s.closest('.tag-add-field').querySelector('.cat-name').textContent.trim(),
                    ns: s.dataset.ns,
                    placeholder: s.options.length ? s.options[0].textContent : '',
                    items: [...s.options].slice(1).map(o => o.textContent)
                }));
            })()""")
        print("  诊断:", json.dumps(info, ensure_ascii=False))
        cdp.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
