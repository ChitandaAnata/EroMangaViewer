"""空格分隔多关键词搜索的端到端验证。

覆盖：
- q 按空白拆词取交集："7 9" 只留下同时含 7 和 9 的作品（不是字面量 "7 9"）
- 连续空格 / 全角空格都当分隔符；单关键词行为不变
- 普通文字搜索不靠标签命中（chi 不该搜出 noripachi 标签作品）
- 输入 chi 不点联想落空，点联想后才按标签筛选
- 页面搜索框输入多词后卡片、标题与副标题同步
用法：D:/python/python.exe tools/test_search_terms.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CDP, CHROME, PORT, USER_DIR, wait_for  # noqa: E402

passed = 0
failed = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}  {extra}")


def api(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def query(text: str):
    return api("/api/comics?q=" + urllib.parse.quote(text, safe=""))


def titles(res) -> list[str]:
    return [c["title"] for c in res.get("items", [])]


def launch(width: int, height: int):
    proc = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={USER_DIR}",
        f"--window-size={width},{height}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(80):
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
        proc.kill()
        raise SystemExit("无法连接 Chrome 调试端口")
    cdp = CDP(ws_url)
    cdp.cmd("Page.enable")
    cdp.cmd("Runtime.enable")
    cdp.cmd("Network.enable")
    cdp.cmd("Network.setCacheDisabled", {"cacheDisabled": True})
    cdp.cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "window.__errors=[];window.addEventListener('error',e=>window.__errors.push(String(e.message)));"
                  "window.addEventListener('unhandledrejection',e=>window.__errors.push(String(e.reason)));"
    })
    cdp.cmd("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
    })
    return proc, cdp


def type_search(cdp, text: str) -> None:
    cdp.js(f"""(() => {{
        const el = document.getElementById('searchInput');
        el.value = {json.dumps(text, ensure_ascii=False)};
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }})()""")


def main() -> None:
    all_items = api("/api/comics")["items"]
    c97 = next((c["title"] for c in all_items if c["title"].startswith("(C97)")), None)
    c96 = next((c["title"] for c in all_items if c["title"].startswith("(C96)")), None)
    c92 = next((c["title"] for c in all_items if c["title"].startswith("(C92)")), None)
    if not (c97 and c96 and c92):
        raise SystemExit("样例库不是预期的 C92 / C97 / C96 三本，无法验证关键词拆分")

    print("1) 单关键词行为不变")
    q7, q9 = titles(query("7")), titles(query("9"))
    check("q=7 只命中 C97", q7 == [c97], json.dumps(q7, ensure_ascii=False))
    check("q=9 命中三本", len(q9) == 3, json.dumps(q9, ensure_ascii=False))

    print("2) 空格分隔取交集")
    r = titles(query("7 9"))
    check("q='7 9' 只命中同时含 7 与 9 的 C97", r == [c97], json.dumps(r, ensure_ascii=False))
    r = titles(query("7   9"))
    check("连续空格同样拆开", r == [c97], json.dumps(r, ensure_ascii=False))
    r = titles(query("9　7"))
    check("全角空格同样拆开", r == [c97], json.dumps(r, ensure_ascii=False))
    r = titles(query("9 6"))
    check("q='9 6' 只命中 C96", r == [c96], json.dumps(r, ensure_ascii=False))
    r = titles(query("9 2"))
    check("q='9 2' 只命中 C92", r == [c92], json.dumps(r, ensure_ascii=False))
    r = titles(query("7 9 0"))
    check("三个词有一个不满足就落空", r == [], json.dumps(r, ensure_ascii=False))

    print("3) 普通文字搜索不靠标签命中")
    r = titles(query("chi"))
    check("q=chi 不因 noripachi / language:chinese 标签命中任何作品",
          all("chi" in t.lower() for t in r), json.dumps(r, ensure_ascii=False))
    check("样例库书名不含 chi → q=chi 落空", r == [], json.dumps(r, ensure_ascii=False))
    r = titles(query("chinese japanese"))
    check("标签名不是普通搜索词 → 落空", r == [], json.dumps(r, ensure_ascii=False))

    print("4) 页面搜索框")
    proc = None
    try:
        proc, cdp = launch(1440, 900)
        cdp.cmd("Page.navigate", {"url": BASE + "/#/"})
        check("首页加载完成", wait_for(cdp, "document.querySelectorAll('.card').length === 3", 25))

        type_search(cdp, "7   9")
        check("多词搜索只剩一张卡", wait_for(cdp, "document.querySelectorAll('.card').length === 1", 10))
        card = cdp.js("document.querySelector('.card').textContent")
        check("留下的是 C97", "(C97)" in card, card[:80])
        check("副标题回显搜索词", "7   9" in cdp.js("document.getElementById('viewSub').textContent"))
        check("带空格时不弹标签联想", cdp.js("document.getElementById('searchSuggest').hidden") is True)

        type_search(cdp, "9")
        check("改回单词搜索恢复三张卡", wait_for(cdp, "document.querySelectorAll('.card').length === 3", 10))
        type_search(cdp, "")
        check("清空搜索仍显示全部", wait_for(cdp, "document.querySelectorAll('.card').length === 3", 10))

        print("5) 输入 chi 不点联想 vs 点了联想")
        type_search(cdp, "chi")
        check("只输入 chi：搜不出名字里没 chi 的作品",
              wait_for(cdp, "document.querySelectorAll('.card').length === 0", 10))
        check("联想菜单照常弹出", wait_for(cdp, "!document.getElementById('searchSuggest').hidden", 6))
        cdp.js("""(() => {
            const b = [...document.querySelectorAll('#searchSuggest button[data-i]')]
              .find(x => x.querySelector('.sg-raw').textContent.trim() === 'language:chinese');
            if (b) b.click();
        })()""")
        check("点联想后才按标签筛出 C97",
              wait_for(cdp, "document.querySelectorAll('.card').length === 1", 10))
        check("留下的确实是 C97", "(C97)" in cdp.js("document.querySelector('.card').textContent"))

        errs = cdp.js("window.__errors || []")
        check("无 JS 报错", not errs, json.dumps(errs, ensure_ascii=False))
    finally:
        if proc:
            proc.terminate()

    print(f"\n通过 {passed} / {passed + failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
