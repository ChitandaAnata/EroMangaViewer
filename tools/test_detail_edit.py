"""详情页「编辑模式」端到端验证。

覆盖：编辑开关只在详情页出现、默认关闭时分类下拉可看不可改 / ＋编辑标签隐藏，
打开后编辑入口一次性出现、旧的「管理标签」按钮已删。重置阅读进度只在有进度时出现，
且与编辑模式无关（只读态照样显示、照样能点），点了真的清库、离开详情页回到只读态。
跑完会把被测作品的阅读进度还原。
用法：D:/python/python.exe tools/test_detail_edit.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CDP, CHROME, PORT, USER_DIR  # noqa: E402

passed = 0
failed = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label} {extra}")


def api(path: str, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


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
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600,
    })
    return proc, cdp


def wait(cdp, expr: str, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cdp.js(expr):
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def wait_until(fn, timeout: float = 10.0, step: float = 0.25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


VIS_JS = """(() => {
    const vis = (el) => !!el && el.offsetParent !== null
        && getComputedStyle(el).display !== 'none'
        && getComputedStyle(el).visibility !== 'hidden';
    const b = document.getElementById('detailEdit');
    const cat = document.getElementById('detailCat');
    const catStyle = cat ? getComputedStyle(cat) : null;
    return {
        edit: vis(b),
        pressed: b ? b.getAttribute('aria-pressed') : null,
        label: b ? b.textContent.trim() : '',
        editingClass: document.getElementById('detail').classList.contains('editing'),
        cat: vis(cat),
        catDisabled: cat ? cat.disabled : null,
        catArrow: catStyle ? catStyle.backgroundImage : '',
        catPad: catStyle ? catStyle.paddingRight : '',
        add: vis(document.getElementById('dAddTag')),
        restart: vis(document.getElementById('detailRestart')),
        oldBtn: !!document.getElementById('detailEditTags'),
    };
})()"""


def main() -> None:
    items = [i for i in api("/api/comics")["items"] if (i.get("pages") or 0) > 0]
    if not items:
        raise SystemExit("库里没有可阅读的作品")
    target = max(items, key=lambda c: c.get("pages") or 0)
    cid = target["id"]
    original_page = int(target.get("lastPage") or 0)

    def restore():
        api(f"/api/comics/{cid}", "PUT", {"lastPage": original_page})

    # 造一个「有进度」的状态：重置阅读进度按钮才会出现
    api(f"/api/comics/{cid}", "PUT", {"lastPage": min(5, (target["pages"] or 1) - 1)})
    print(f"被测作品：{target['title']}（{target['pages']} 页，原进度 {original_page}）")

    proc = None
    try:
        proc, cdp = launch(1440, 900)
        # 1) 只在详情页出现
        cdp.cmd("Page.navigate", {"url": f"{BASE}/"})
        wait(cdp, "document.readyState === 'complete'")
        wait(cdp, "document.querySelectorAll('.card').length > 0", 20)
        time.sleep(0.3)
        shelf = cdp.js(VIS_JS)
        check("书架页没有编辑按钮", shelf["edit"] is False, json.dumps(shelf, ensure_ascii=False))

        print("1) 打开详情页：右上角出现编辑模式开关")
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#comic={cid}"})
        wait(cdp, "document.getElementById('detail').hidden === false", 20)
        wait(cdp, "document.querySelectorAll('.page-cell').length > 0", 25)
        time.sleep(0.4)

        box = cdp.js("""(() => {
            const b = document.getElementById('detailEdit');
            const back = document.getElementById('detailBack').getBoundingClientRect();
            const r = b.getBoundingClientRect();
            const panel = document.querySelector('.detail-panel').getBoundingClientRect();
            return {
                visible: b.offsetParent !== null,
                pressed: b.getAttribute('aria-pressed'),
                label: b.textContent.trim(),
                sameRow: Math.abs(r.top + r.height / 2 - (back.top + back.height / 2)) < 6,
                gapRight: Math.round(panel.right - r.right),
                vw: innerWidth,
                right: Math.round(r.right),
            };
        })()""")
        check("编辑按钮可见", box["visible"] is True, json.dumps(box, ensure_ascii=False))
        check("默认是关闭状态", box["pressed"] == "false", str(box["pressed"]))
        check("文案是「编辑模式」", box["label"] == "编辑模式", box["label"])
        check("和返回按钮同一行", box["sameRow"] is True, json.dumps(box, ensure_ascii=False))
        check("贴在页面右侧", box["gapRight"] <= 24, json.dumps(box, ensure_ascii=False))

        print("2) 关闭态：分类照旧显示，但不能点；编辑入口都不露")
        off = cdp.js(VIS_JS)
        check("作品分类仍然显示", off["cat"] is True, json.dumps(off, ensure_ascii=False))
        check("只读态没有下拉箭头", off["catArrow"] == "none", off["catArrow"])
        check("只读态右侧内边距收紧", off["catPad"] == "12px", str(off["catPad"]))
        check("只读态点不开下拉", off["catDisabled"] is True, str(off["catDisabled"]))
        check("＋编辑标签隐藏", off["add"] is False, json.dumps(off, ensure_ascii=False))
        check("只读态重置阅读进度照常显示", off["restart"] is True, json.dumps(off, ensure_ascii=False))
        check("只读态重置按钮没有被禁用", cdp.js(
            "document.getElementById('detailRestart').disabled") is False)
        check("没有 editing 类", off["editingClass"] is False)
        check("旧「管理标签」按钮已删除", off["oldBtn"] is False)
        check("阅读主按钮仍在", cdp.js(
            "document.getElementById('detailRead').offsetParent !== null") is True)
        check("分类文字没被禁用态冲淡", cdp.js(
            "getComputedStyle(document.getElementById('detailCat')).opacity") == "1",
            cdp.js("getComputedStyle(document.getElementById('detailCat')).opacity"))

        print("3) 打开编辑模式：编辑入口一次全部出现")
        cdp.js("document.getElementById('detailEdit').click()")
        wait(cdp, "document.getElementById('detail').classList.contains('editing')", 8)
        time.sleep(0.2)
        on = cdp.js(VIS_JS)
        check("开关变按下态", on["pressed"] == "true", str(on["pressed"]))
        check("文案变「完成编辑」", on["label"] == "完成编辑", on["label"])
        check("出现编辑态样式", on["editingClass"] is True)
        check("分类变回可点的下拉", on["cat"] is True and on["catDisabled"] is False,
              json.dumps(on, ensure_ascii=False))
        check("下拉箭头回来了", on["catArrow"] not in ("none", ""), on["catArrow"])
        check("下拉恢复完整内边距", on["catPad"] == "26px", str(on["catPad"]))
        check("＋编辑标签出现", on["add"] is True, json.dumps(on, ensure_ascii=False))
        check("重置阅读进度出现", on["restart"] is True, json.dumps(on, ensure_ascii=False))
        check("重置按钮文案正确", cdp.js(
            "document.getElementById('detailRestart').textContent.trim()") == "重置阅读进度",
            cdp.js("document.getElementById('detailRestart').textContent.trim()"))
        check("只剩一个标签编辑入口", cdp.js(
            "document.querySelectorAll('#detailTags #dAddTag').length") == 1,
            str(cdp.js("document.querySelectorAll('#detailTags #dAddTag').length")))

        print("4) 重置阅读进度：真的清库")
        before = api(f"/api/comics/{cid}")["lastPage"]
        check("重置前有进度", before > 0, str(before))
        cdp.js("document.getElementById('detailRestart').click()")
        check("接口进度归零", wait_until(lambda: api(f"/api/comics/{cid}")["lastPage"] == 0, 10),
              str(api(f"/api/comics/{cid}")["lastPage"]))
        wait(cdp, "document.getElementById('detailRestart').hidden === true", 8)
        state = cdp.js("""({
            restart: !!document.getElementById('detailRestart').offsetParent,
            progress: !!document.getElementById('detailProgress').offsetParent,
            read: document.getElementById('detailReadLabel').textContent.trim(),
        })""")
        check("重置按钮自己消失", state["restart"] is False, json.dumps(state, ensure_ascii=False))
        check("进度条收起", state["progress"] is False, json.dumps(state, ensure_ascii=False))
        check("主按钮回到「开始阅读」", state["read"] == "开始阅读", state["read"])
        check("书架卡片进度条同步清掉", cdp.js(
            f"!document.querySelector('.card[data-id=\"{cid}\"] .card-progress')") is True)

        print("5) 关闭编辑模式：恢复只读")
        cdp.js("document.getElementById('detailEdit').click()")
        wait(cdp, "!document.getElementById('detail').classList.contains('editing')", 8)
        time.sleep(0.2)
        back = cdp.js(VIS_JS)
        check("开关回弹", back["pressed"] == "false", str(back["pressed"]))
        check("文案回「编辑模式」", back["label"] == "编辑模式", back["label"])
        check("分类标记还在但箭头又不见了", back["cat"] is True and back["catArrow"] == "none",
              json.dumps(back, ensure_ascii=False))
        check("分类又变成不可点", back["catDisabled"] is True, str(back["catDisabled"]))
        check("＋编辑标签再次隐藏", back["add"] is False, json.dumps(back, ensure_ascii=False))

        print("6) 离开详情页后重置：下次进来仍是只读")
        cdp.js("document.getElementById('detailEdit').click()")
        wait(cdp, "document.getElementById('detail').classList.contains('editing')", 8)
        cdp.js("document.getElementById('detailBack').click()")
        wait(cdp, "document.getElementById('detail').hidden === true", 10)
        check("返回书架时编辑态已清掉", cdp.js(
            "!document.getElementById('detail').classList.contains('editing')") is True)
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#comic={cid}"})
        wait(cdp, "document.getElementById('detail').hidden === false", 20)
        time.sleep(0.5)
        again = cdp.js(VIS_JS)
        check("重新进详情仍是关闭态", again["pressed"] == "false" and again["editingClass"] is False,
              json.dumps(again, ensure_ascii=False))
        check("编辑类控件仍是只读形态",
              again["cat"] is True and again["catDisabled"] is True and again["add"] is False,
              json.dumps(again, ensure_ascii=False))

        check("无 JS 报错", cdp.js("window.__errors ? window.__errors.length : 0") in (0, None),
              json.dumps(cdp.js("window.__errors || []"), ensure_ascii=False))
        cdp.ws.close()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(5)
            except Exception:
                proc.kill()
        restore()
        wait_until(lambda: int(api(f"/api/comics/{cid}").get("lastPage") or 0) == original_page, 8)
        print(f"已还原阅读进度：第 {original_page} 页")

    print(f"\n{passed} 项通过，{failed} 项失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
