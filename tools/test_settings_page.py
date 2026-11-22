"""设置页信息架构：菜单首页 + 独立子页（标签管理收进子页）。

覆盖：
- 点顶栏设置按钮打开的是覆盖全屏的独立页面，而不是弹窗
- 首页只有菜单条目，路径输入、标签列表这类重控件都在子页里
- 进入漫画库 / 标签管理子页，标题与返回按钮跟着切换
- 子页返回先回菜单，再返回才离开设置
- 标签管理页可按标签名 / 显示名过滤
- 深链 #settings=tags 冷启动直接落在子页
- Esc 逐级返回；从详情页进入设置，关闭后回到详情
用法：D:/python/python.exe tools/test_settings_page.py
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CHROME, PORT, USER_DIR, CDP, wait_for  # noqa: E402

_ok = 0
_bad = 0


def check(name, cond, detail=""):
    global _ok, _bad
    if cond:
        _ok += 1
        print(f"  ✓ {name}")
    else:
        _bad += 1
        print(f"  ✗ {name}  {detail}")


def api(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_until(fn, timeout=10.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def main():
    comics = api("/api/comics")["items"]
    if not comics:
        raise SystemExit("库里没有作品，无法测试")
    cid = comics[0]["id"]

    tags = api("/api/tags")["tags"]
    if tags:
        first = tags[0]
        needle = first.get("label") or first.get("name")
        expect_hits = sum(
            1 for t in tags
            if needle.lower() in f'{t["name"]} {t.get("label", "")}'.lower())
        if expect_hits > 3:  # 命中的太多就不适合做精确断言
            needle, expect_hits = tags[0]["name"], 1
    else:
        needle, expect_hits = "", 0

    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={USER_DIR}",
         "--window-size=1440,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        cdp.cmd("Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 900, "deviceScaleFactor": 2, "mobile": False})
        cdp.cmd("Page.addScriptToEvaluateOnNewDocument", {"source":
                 "window.__errors=[];"
                 "window.addEventListener('error',e=>window.__errors.push(String(e.message)));"
                 "window.addEventListener('unhandledrejection',e=>window.__errors.push(String(e.reason)));"})
        cdp.cmd("Page.navigate", {"url": f"{BASE}/"})
        wait_for(cdp, "document.readyState === 'complete'")
        wait_for(cdp, "document.querySelectorAll('.card').length > 0", 25)
        time.sleep(0.5)

        print("1) 设置是独立页面，不是弹窗")
        cdp.js("document.getElementById('settingsBtn').click()")
        ok = wait_for(cdp, "!document.getElementById('settingsPage').hidden", 8)
        check("设置页已打开", ok)
        time.sleep(0.45)  # 等入场动画结束，再量尺寸
        box = cdp.js("""(() => {
            const p = document.getElementById('settingsPage');
            const r = p.getBoundingClientRect();
            return {top: r.top, left: r.left, w: r.width, h: r.height,
                    vw: innerWidth, vh: innerHeight,
                    pos: getComputedStyle(p).position,
                    cards: document.querySelectorAll('#settingsPage .modal-card').length};
        })()""")
        check("铺满整个视口", box["pos"] == "fixed" and box["top"] == 0 and box["left"] == 0
              and round(box["w"]) == box["vw"] and round(box["h"]) == box["vh"],
              json.dumps(box))
        check("不是弹窗结构", box["cards"] == 0)

        print("2) 首页只有菜单条目，重控件收在子页")
        home = cdp.js("""(() => ({
            rows: [...document.querySelectorAll('.set-view[data-view=home] .set-row')].map(r => ({
                go: r.dataset.go,
                title: r.querySelector('.set-row-main b').textContent,
                sub: r.querySelector('.set-row-main em').textContent
            })),
            inputs: document.querySelectorAll('.set-view[data-view=home] input').length,
            tagsHidden: document.querySelector('.set-view[data-view=tags]').hidden,
            libHidden: document.querySelector('.set-view[data-view=library]').hidden,
            title: document.getElementById('setTitle').textContent,
            back: document.getElementById('setBackLabel').textContent
        }))()""")
        check("菜单含五类设置", [r["go"] for r in home["rows"]]
              == ["library", "tags", "sidecar", "appearance", "about"],
              json.dumps(home["rows"], ensure_ascii=False))
        check("每行都有摘要", all(r["sub"] for r in home["rows"]),
              json.dumps(home["rows"], ensure_ascii=False))
        check("首页没有输入控件", home["inputs"] == 0)
        check("标签列表与路径输入都收在子页", home["tagsHidden"] and home["libHidden"])
        check("首页标题为「设置」、返回书架", home["title"] == "设置" and home["back"] == "返回书架")

        print("3) 进入漫画库子页")
        cdp.js("document.querySelector('.set-row[data-go=\"library\"]').click()")
        wait_for(cdp, "!document.querySelector('.set-view[data-view=library]').hidden", 8)
        lib = cdp.js("""(() => ({
            value: document.getElementById('libPathInput').value,
            visible: !!document.getElementById('libPathInput').offsetParent,
            title: document.getElementById('setTitle').textContent,
            back: document.getElementById('setBackLabel').textContent,
            save: !!document.getElementById('saveSettingsBtn').offsetParent
        }))()""")
        check("路径已填充且可见", bool(lib["value"]) and lib["visible"], json.dumps(lib, ensure_ascii=False))
        check("标题切到子页、返回指向设置",
              lib["title"] == "漫画库文件夹" and lib["back"] == "设置", json.dumps(lib, ensure_ascii=False))
        check("保存并扫描按钮在子页", lib["save"])

        print("4) 返回先回菜单，不直接退出")
        cdp.js("document.getElementById('setBack').click()")
        wait_for(cdp, "!document.querySelector('.set-view[data-view=home]').hidden", 8)
        check("回到菜单首页", cdp.js("document.getElementById('setTitle').textContent") == "设置")
        check("设置页仍然开着", cdp.js("!document.getElementById('settingsPage').hidden"))

        print("5) 标签管理子页：搜索过滤")
        cdp.js("document.querySelector('.set-row[data-go=\"tags\"]').click()")
        wait_for(cdp, "!document.querySelector('.set-view[data-view=tags]').hidden", 8)
        wait_until(lambda: cdp.js("document.querySelectorAll('#tagAdminList .tag-admin-row').length") == len(tags), 8)
        check("列出全部标签",
              cdp.js("document.querySelectorAll('#tagAdminList .tag-admin-row').length") == len(tags),
              cdp.js("document.querySelectorAll('#tagAdminList .tag-admin-row').length"))
        check("子页标题为标签管理、返回指向设置",
              cdp.js("document.getElementById('setTitle').textContent") == "标签管理"
              and cdp.js("document.getElementById('setBackLabel').textContent") == "设置")

        if needle:
            cdp.js(f"""(() => {{
                const i = document.getElementById('tagSearch');
                i.value = {json.dumps(needle, ensure_ascii=False)};
                i.dispatchEvent(new Event('input', {{bubbles: true}}));
            }})()""")
            hits = wait_until(
                lambda: cdp.js("document.querySelectorAll('#tagAdminList .tag-admin-row').length") == expect_hits, 6)
            check(f"搜索「{needle}」筛出 {expect_hits} 个", hits,
                  cdp.js("document.querySelectorAll('#tagAdminList .tag-admin-row').length"))

            cdp.js("""(() => {
                const i = document.getElementById('tagSearch');
                i.value = '__不存在的标签__';
                i.dispatchEvent(new Event('input', {bubbles: true}));
            })()""")
            empty = wait_until(
                lambda: cdp.js("document.querySelectorAll('#tagAdminList .tag-admin-row').length") == 0, 6)
            check("无匹配时给出空状态",
                  empty and cdp.js("!!document.querySelector('#tagAdminList .tag-admin-empty')"))

            cdp.js("""(() => {
                const i = document.getElementById('tagSearch');
                i.value = '';
                i.dispatchEvent(new Event('input', {bubbles: true}));
            })()""")
            back = wait_until(
                lambda: cdp.js("document.querySelectorAll('#tagAdminList .tag-admin-row').length") == len(tags), 6)
            check("清空搜索恢复全部", back)

        print("6) 深链 #settings=tags 直接落在子页")
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#settings=tags"})
        wait_for(cdp, "document.readyState === 'complete'")
        ok = wait_for(cdp, "!document.getElementById('settingsPage').hidden", 12)
        check("冷启动打开设置页", ok)
        check("直接显示标签管理", wait_for(
            cdp, "document.getElementById('setTitle').textContent === '标签管理'", 8),
            cdp.js("document.getElementById('setTitle').textContent"))

        print("7) Esc 逐级返回")
        cdp.js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        ok = wait_for(cdp, "!document.querySelector('.set-view[data-view=home]').hidden", 6)
        check("Esc 先回菜单", ok)
        check("设置页还在", cdp.js("!document.getElementById('settingsPage').hidden"))
        cdp.js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        ok = wait_for(cdp, "document.getElementById('settingsPage').hidden", 6)
        check("再按 Esc 关闭设置", ok)
        check("hash 不再是 settings", not cdp.js("location.hash.startsWith('#settings')"))

        print("8) 从详情页进入设置，关闭后回到详情")
        cdp.js(f"location.hash = 'comic={cid}'")
        wait_for(cdp, "document.getElementById('detail').hidden === false", 12)
        cdp.js("document.getElementById('settingsBtn').click()")
        wait_for(cdp, "!document.getElementById('settingsPage').hidden", 8)
        check("返回按钮写明回详情", wait_for(
            cdp, "document.getElementById('setBackLabel').textContent === '返回详情'", 6),
            cdp.js("document.getElementById('setBackLabel').textContent"))
        cdp.js("document.getElementById('setBack').click()")
        ok = wait_for(cdp, "document.getElementById('settingsPage').hidden", 6)
        check("设置已关闭", ok)
        check("回到原来的详情页", cdp.js("location.hash") == f"#comic={cid}",
              cdp.js("location.hash"))
        check("详情页仍然打开", cdp.js("document.getElementById('detail').hidden === false"))

        print("9) 没有 JS 报错")
        errs = cdp.js("window.__errors || []")
        check("无 JS 报错", not errs, json.dumps(errs, ensure_ascii=False))

        cdp.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()

    print(f"\n结果：{_ok} 通过，{_bad} 失败")
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
