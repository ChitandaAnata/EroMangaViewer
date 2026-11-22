"""侧栏模式与标签勾选叠加筛选的端到端验证。

覆盖：
- 先选 Favorite 再勾标签：只显示「Favorite ∩ 该标签」，不是所有该标签作品
- 叠加时侧栏栏目保持选中、标题/副标题/计数跟着收窄
- 已经勾了标签再切上面四个栏目：下面的勾选被清空
- 跑完把作品喜爱状态还原成测试前的值
用法：D:/python/python.exe tools/test_mode_tag_filter.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from collections import Counter
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


def api(path: str, method: str = "GET", body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


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


SNAP_JS = """(() => ({
    cards: [...document.querySelectorAll('.card')].map(c => c.dataset.id),
    title: document.getElementById('viewTitle').textContent,
    sub: document.getElementById('viewSub').textContent,
    checked: [...document.querySelectorAll('#tagList .tag-row.checked')].map(r => r.dataset.tagName),
    activeNav: [...document.querySelectorAll('.nav-item.active')].map(b => b.dataset.tag),
    counts: [...document.querySelectorAll('.nav-item em')].map(e => e.textContent.trim()),
}))()"""

def main() -> None:
    items = api("/api/comics")["items"]
    if len(items) < 3:
        raise SystemExit("至少要有三本作品才能验证叠加筛选")

    names = {c["id"]: [t["name"] for t in (c.get("tags") or [])] for c in items}
    counts = Counter(n for ns in names.values() for n in ns)
    # 选一个「有人打、也有人没打」且自身至少两本的标签，才能区分交集和单标签结果
    tag = next((t for t, n in counts.items() if 1 < n < len(items)), None)
    if not tag:
        raise SystemExit("样例库里没有适合测交集的标签")
    with_tag = [c for c in items if tag in names[c["id"]]]
    without = [c for c in items if tag not in names[c["id"]]]
    fav_ids = {with_tag[0]["id"], without[0]["id"]}

    originals = {c["id"]: bool(c.get("favorite")) for c in items}
    try:
        for c in items:
            want = c["id"] in fav_ids
            if bool(c.get("favorite")) != want:
                api(f"/api/comics/{c['id']}", "PUT", {"favorite": want})
        time.sleep(0.8)

        proc = None
        try:
            proc, cdp = launch(1440, 900)
            cdp.cmd("Page.navigate", {"url": BASE + "/#"})
            check("首页加载完成", wait_for(cdp, "document.querySelectorAll('.card').length > 0", 25))

            print("1) Favorite ∩ 标签：只留同时满足的作品")
            cdp.js("document.querySelector('.nav-item[data-tag=\"__favorite__\"]').click()")
            check("Favorite 模式生效", wait_for(
                cdp, f"document.querySelectorAll('.card').length === {len(fav_ids)}", 10))
            row = cdp.js(f"""(() => {{
                const r = document.querySelector('#tagList .tag-row[data-tag-name="{tag}"]');
                return r ? {{ label: r.querySelector('.tname').textContent.trim() }} : null;
            }})()""")
            check("被测标签在侧栏里", row is not None, str(row))

            cdp.js(f"""document.querySelector('#tagList .tag-row[data-tag-name="{tag}"]').click()""")
            check("交集只剩一本", wait_for(cdp, "document.querySelectorAll('.card').length === 1", 10))
            snap = cdp.js(SNAP_JS)
            check("留下的是 Favorite 中带该标签的那本", snap["cards"] == [with_tag[0]["id"]],
                  json.dumps(snap, ensure_ascii=False))
            check("Favorite 栏目依然选中", snap["activeNav"] == ["__favorite__"],
                  json.dumps(snap["activeNav"], ensure_ascii=False))
            check("标题写成 Favorite + 标签", snap["title"].startswith("Favorite") and row["label"] in snap["title"],
                  snap["title"])
            check("副标题也带 Favorite", "Favorite" in snap["sub"], snap["sub"])
            check("该标签是勾选态", snap["checked"] == [tag], json.dumps(snap["checked"], ensure_ascii=False))
            check("Favorite 计数收窄到交集", snap["counts"][3] == "1", json.dumps(snap["counts"]))

            print("2) 先勾标签再切栏目：勾选被清空")
            cdp.js("document.querySelector('.nav-item[data-tag=\"__updating__\"]').click()")
            check("切到更新中", wait_for(
                cdp, "document.getElementById('viewTitle').textContent === '更新中'", 10))
            snap = cdp.js(SNAP_JS)
            check("标签勾选已清空", snap["checked"] == [], json.dumps(snap["checked"], ensure_ascii=False))
            check("标题回到栏目名", snap["title"] == "更新中", snap["title"])

            print("3) 反向：勾标签后再点 Favorite，同样清空标签")
            cdp.js("document.querySelector('.nav-item[data-tag=\"\"]').click()")
            check("回到全部作品", wait_for(
                cdp, "document.querySelectorAll('.card').length === " + str(len(items)), 10))
            cdp.js(f"""document.querySelector('#tagList .tag-row[data-tag-name="{tag}"]').click()""")
            check("单标签筛出 " + str(len(with_tag)) + " 本", wait_for(
                cdp, f"document.querySelectorAll('.card').length === {len(with_tag)}", 10))
            cdp.js("document.querySelector('.nav-item[data-tag=\"__favorite__\"]').click()")
            check("Favorite 恢复 " + str(len(fav_ids)) + " 本", wait_for(
                cdp, f"document.querySelectorAll('.card').length === {len(fav_ids)}", 10))
            snap = cdp.js(SNAP_JS)
            check("这一路切栏目也会清空勾选", snap["checked"] == [], json.dumps(snap["checked"], ensure_ascii=False))
            check("Favorite 计数恢复", snap["counts"][3] == str(len(fav_ids)), json.dumps(snap["counts"]))

            errs = cdp.js("window.__errors || []")
            check("无 JS 报错", not errs, json.dumps(errs, ensure_ascii=False))
        finally:
            if proc:
                proc.terminate()
    finally:
        print("4) 还原喜爱状态")
        for cid, fav in originals.items():
            api(f"/api/comics/{cid}", "PUT", {"favorite": fav})
        time.sleep(0.8)

    print(f"\n通过 {passed} 项，失败 {failed} 项")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
