"""侧栏收尾与顶部搜索联想的端到端验证。

覆盖：
- 标签分类标题可折叠 / 展开，刷新后保持；折叠组里已勾选的标签仍有计数提示
- 侧栏底部「X 本 / 库路径 / 打开漫画文件夹」整块已经移除
- 顶部搜索输入 chi 会弹出「汉语 / language:chinese」等标签联想，点选后按该标签筛选
- 键盘上下 + 回车可选中联想项，Esc 关闭；后端 q 也能命中标签
用法：D:/python/python.exe tools/test_ui_polish.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
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


SUGGEST_JS = """(() => ({
    hidden: document.getElementById('searchSuggest').hidden,
    items: [...document.querySelectorAll('#searchSuggest button[data-i]')].map(b => ({
        label: b.querySelector('.sg-label').textContent.trim(),
        raw: b.querySelector('.sg-raw').textContent.trim(),
        count: Number(b.querySelector('.sg-count').textContent.trim()),
    })),
}))()"""


def main() -> None:
    comics = api("/api/comics")["items"]
    tags = api("/api/tags")["tags"]
    zh = next((t for t in tags if t["name"] == "language:chinese"), None)
    if not zh:
        raise SystemExit("样例库里缺少 language:chinese，无法验证标签联想")
    expect = [c["id"] for c in comics
              if any(t["name"] == "language:chinese" for t in (c.get("tags") or []))]
    if not expect:
        raise SystemExit("样例库里没有打了汉语标签的作品")

    proc = None
    try:
        proc, cdp = launch(1440, 900)
        cdp.cmd("Page.navigate", {"url": BASE + "/#"})
        check("首页加载完成", wait_for(cdp, "document.querySelectorAll('.card').length > 0", 25))
        cdp.js("localStorage.removeItem('tagGroupsCollapsed')")
        cdp.cmd("Page.reload", {"ignoreCache": True})
        check("刷新后首页正常", wait_for(cdp, "document.querySelectorAll('.card').length > 0", 25))

        print("1) 侧栏底部信息块已移除")
        foot = cdp.js("""(() => ({
            foot: !!document.querySelector('.side-foot'),
            openBtn: !!document.getElementById('openLibBtn'),
            stat: !!document.getElementById('statTotal'),
            libPath: !!document.getElementById('libPath'),
            aboutLib: !!document.getElementById('aboutLib'),
        }))()""")
        check("side-foot 整块不在了", foot["foot"] is False, json.dumps(foot))
        check("打开漫画文件夹按钮不在了", foot["openBtn"] is False, json.dumps(foot))
        check("旧的计数 / 路径节点不在了", not foot["stat"] and not foot["libPath"], json.dumps(foot))
        check("设置页里的库路径仍在", foot["aboutLib"] is True, json.dumps(foot))

        print("2) 标签分类折叠 / 展开")
        heads = cdp.js("""[...document.querySelectorAll('#tagList .tag-group-head')].map(h => ({
            ns: h.dataset.ns, label: h.childNodes[0].textContent.trim(),
            expanded: h.getAttribute('aria-expanded'),
        }))""")
        check("分组标题是按钮且默认展开", len(heads) >= 2 and all(
            h["expanded"] == "true" and h["ns"] and h["label"] for h in heads),
            json.dumps(heads, ensure_ascii=False))
        first = heads[0]
        cdp.js(f"""document.querySelector('#tagList .tag-group-head[data-ns="{first['ns']}"]').click()""")
        check("点标题后折叠", wait_for(
            cdp, f"""document.querySelector('#tagList .tag-group[data-ns="{first['ns']}"]').classList.contains('collapsed')""", 5))
        state = cdp.js(f"""(() => {{
            const g = document.querySelector('#tagList .tag-group[data-ns="{first['ns']}"]');
            return {{
                expanded: g.querySelector('.tag-group-head').getAttribute('aria-expanded'),
                rowVisible: g.querySelector('.tag-row').offsetParent !== null,
                saved: localStorage.getItem('tagGroupsCollapsed'),
            }};
        }})()""")
        check("折叠时 aria-expanded=false", state["expanded"] == "false", json.dumps(state))
        check("折叠后标签行不可见", state["rowVisible"] is False, json.dumps(state))
        check("折叠状态写进 localStorage", first["ns"] in (state["saved"] or ""), json.dumps(state))

        cdp.cmd("Page.reload", {"ignoreCache": True})
        check("刷新后首页再次加载", wait_for(cdp, "document.querySelectorAll('.card').length > 0", 25))
        check("刷新后仍保持折叠", cdp.js(f"""document.querySelector('#tagList .tag-group[data-ns="{first['ns']}"]').classList.contains('collapsed')""") is True)
        cdp.js(f"""document.querySelector('#tagList .tag-group-head[data-ns="{first['ns']}"]').click()""")
        check("再点一次展开", cdp.js(f"""document.querySelector('#tagList .tag-group-head[data-ns="{first['ns']}"]').getAttribute('aria-expanded')""") == "true")

        print("3) 折叠组里已勾选项仍有提示")
        cdp.js("""document.querySelector('#tagList .tag-row[data-tag-name="language:chinese"]').click()""")
        check("汉语标签勾上", wait_for(
            cdp, """document.querySelectorAll('.card').length === """ + str(len(expect)), 10))
        cdp.js("""document.querySelector('#tagList .tag-group-head[data-ns="language"]').click()""")
        folded = cdp.js("""(() => {
            const g = document.querySelector('#tagList .tag-group[data-ns="language"]');
            return { collapsed: g.classList.contains('collapsed'),
                     count: g.querySelector('.tag-group-head em').textContent.trim(),
                     rowVisible: g.querySelector('.tag-row').offsetParent !== null };
        })()""")
        check("语言组已折叠", folded["collapsed"] is True and folded["rowVisible"] is False, json.dumps(folded))
        check("标题显示已选数", folded["count"].startswith("1 /"), json.dumps(folded))
        check("折叠不影响筛选结果", cdp.js("document.querySelectorAll('.card').length") == len(expect))
        cdp.js("document.querySelector('.nav-item[data-tag=\"\"]').click()")
        check("回到全部作品", wait_for(cdp, f"document.querySelectorAll('.card').length === {len(comics)}", 10))

        print("4) 顶部搜索联想：chi → 汉语 / language:chinese")
        check("占位文案已更新", cdp.js("document.getElementById('searchInput').placeholder") == "搜索书名或标签…")
        type_search(cdp, "chi")
        check("弹出联想菜单", wait_for(cdp, "!document.getElementById('searchSuggest').hidden", 6))
        sug = cdp.js(SUGGEST_JS)
        zh_hit = [s for s in sug["items"] if s["raw"] == "language:chinese"]
        check("联想里有 汉语 / language:chinese",
              bool(zh_hit) and zh_hit[0]["label"] == "汉语" and zh_hit[0]["count"] == zh["count"],
              json.dumps(sug, ensure_ascii=False))

        cdp.js("""(() => {
            const b = [...document.querySelectorAll('#searchSuggest button[data-i]')]
                .find(x => x.querySelector('.sg-raw').textContent.trim() === 'language:chinese');
            b.click();
        })()""")
        check("点选后联想收起", cdp.js("document.getElementById('searchSuggest').hidden") is True)
        check("点选后搜索框清空", cdp.js("document.getElementById('searchInput').value") == "")
        check("侧栏汉语标签勾上", cdp.js("""document.querySelector('#tagList .tag-row[data-tag-name="language:chinese"]').getAttribute('aria-checked')""") == "true")
        check("只显示带汉语标签的作品", wait_for(
            cdp, f"document.querySelectorAll('.card').length === {len(expect)}", 10))
        check("汉语组被自动展开", cdp.js("""document.querySelector('#tagList .tag-group[data-ns="language"]').classList.contains('collapsed')""") is False)
        check("标题变成汉语", "汉语" in cdp.js("document.getElementById('viewTitle').textContent"))

        print("5) 键盘选中、Esc 关闭与后端 q 只搜书名")
        cdp.js("document.querySelector('.nav-item[data-tag=\"\"]').click()")
        check("回到全部作品", wait_for(cdp, f"document.querySelectorAll('.card').length === {len(comics)}", 10))
        type_search(cdp, "chi")
        check("再次弹出联想", wait_for(cdp, "!document.getElementById('searchSuggest').hidden", 6))
        top = cdp.js(SUGGEST_JS)["items"][0]["raw"]
        cdp.js("""document.getElementById('searchInput').dispatchEvent(
            new KeyboardEvent('keydown', {key:'ArrowDown', bubbles:true, cancelable:true}))""")
        check("第一项被键盘选中", cdp.js("document.querySelector('#searchSuggest button[data-i]').classList.contains('sel')") is True)
        cdp.js("""document.getElementById('searchInput').dispatchEvent(
            new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true}))""")
        check("回车按第一个联想项筛选", cdp.js(
            f"""document.querySelector('#tagList .tag-row[data-tag-name="{top}"]')?.getAttribute('aria-checked')""") == "true")

        cdp.js("document.querySelector('.nav-item[data-tag=\"\"]').click()")
        type_search(cdp, "chi")
        check("第三次弹出联想", wait_for(cdp, "!document.getElementById('searchSuggest').hidden", 6))
        cdp.js("""document.getElementById('searchInput').dispatchEvent(
            new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}))""")
        check("Esc 关闭联想", cdp.js("document.getElementById('searchSuggest').hidden") is True)

        q_ids = {c["id"] for c in api("/api/comics?q=chi")["items"]}
        check("后端 q=chi 不再靠标签命中汉语作品", not q_ids.intersection(expect),
              json.dumps(sorted(q_ids)))
        c96 = next(c for c in comics if c["title"].startswith("(C96)"))
        check("noripachi 标签含 chi 但书名不含 → 不命中 C96", c96["id"] not in q_ids,
              json.dumps(sorted(q_ids)))

        errs = cdp.js("window.__errors || []")
        check("无 JS 报错", not errs, json.dumps(errs, ensure_ascii=False))
    finally:
        if proc:
            proc.terminate()

    print(f"\n通过 {passed} 项，失败 {failed} 项")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
