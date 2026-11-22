"""侧栏多选筛选 + 设置里的标签管理（增 / 改 / 删）端到端验证。

覆盖：
- 侧栏标签行不再有「＋」和「×」，每行右侧是勾选格
- 勾一个标签按单标签筛选，勾两个按交集（同时拥有）筛选，取消勾选能回到上一个状态
- 设置里的标签管理能新建、改显示名、改名、删除
- 改名后打了这个标签的作品跟着改名；删除后作品上不再残留，旁挂 json 也一起清掉
跑完删掉测试标签，并把被测作品的标签还原。
用法：D:/python/python.exe tools/test_tag_filter_admin.py
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CHROME, PORT, USER_DIR, CDP, wait_for  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TMP = "mixed:__adm_test"
TMP_RENAMED = "mixed:__adm_test2"

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


def api(path, method="GET", body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"__status": e.code, **json.loads(e.read() or b"{}")}


def wait_until(fn, timeout=10.0, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def q(name):
    return urllib.parse.quote(name)


def sidecar_tags(cid):
    root = Path(api("/api/state")["library"])
    rel = api(f"/api/comics/{cid}")["path"]
    try:
        data = json.loads((root / (rel + ".json")).read_text(encoding="utf-8"))
    except Exception:
        return None
    return [t["name"] for t in data.get("tags") or []]


ROWS_JS = """(() => [...document.querySelectorAll('#tagList .tag-row')].map(r => ({
  name: r.dataset.tagName,
  checked: r.classList.contains('checked'),
  aria: r.getAttribute('aria-checked'),
  hasCheck: !!r.querySelector('.tcheck'),
  hasDel: !!r.querySelector('.tdel'),
})))()"""


def main():
    comics = api("/api/comics")["items"]
    if not comics:
        raise SystemExit("库里没有作品，无法测试")

    # 找一本至少有两个标签的作品来做交集测试
    target = max(comics, key=lambda c: len(c.get("tags") or []))
    if len(target.get("tags") or []) < 2:
        raise SystemExit("需要一本至少有两个标签的作品才能测交集")
    cid = target["id"]
    original = [t["name"] for t in target["tags"]]
    a, b = original[0], original[1]

    # 准备工作：清掉上次残留 + 建一个临时标签
    api(f"/api/tags?name={q(TMP)}", "DELETE")
    api(f"/api/tags?name={q(TMP_RENAMED)}", "DELETE")
    api(f"/api/tags?name={q(TMP)}", "DELETE")
    api("/api/tags", "POST", {"name": TMP, "display": "管理测试"})

    import subprocess

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
        cdp.cmd("Page.navigate", {"url": f"{BASE}/"})
        wait_for(cdp, "document.readyState === 'complete'")
        wait_for(cdp, "document.querySelectorAll('.card').length > 0", 25)
        time.sleep(0.6)

        print("1) 侧栏只做筛选：没有加号、没有删除叉，每行一个勾选格")
        check("侧栏不再有新建标签的 +", cdp.js("!document.getElementById('addTagBtn')") is True)
        rows = cdp.js(ROWS_JS)
        check("每行都有勾选格", rows and all(r["hasCheck"] for r in rows),
              json.dumps(rows, ensure_ascii=False)[:160])
        check("每行都没有删除叉", all(not r["hasDel"] for r in rows),
              json.dumps([r["hasDel"] for r in rows]))
        check("默认都未勾选", all(not r["checked"] for r in rows))

        print("2) 勾一个标签 → 单标签筛选")
        cdp.js(f"""(() => {{ const r = document.querySelector('#tagList .tag-row[data-tag-name="{a}"]');
                    r.click(); }})()""")
        wait_until(lambda: cdp.js("document.querySelectorAll('.card').length") ==
                   len([c for c in comics if a in [t["name"] for t in c.get("tags") or []]]), 8)
        expect_a = len([c for c in comics if a in [t["name"] for t in c.get("tags") or []]])
        got = cdp.js("document.querySelectorAll('.card').length")
        check("结果数量与单标签一致", got == expect_a, f"{got} vs {expect_a}")
        check("被点的标签是勾选态",
              cdp.js(f"document.querySelector('#tagList .tag-row[data-tag-name=\"{a}\"]').classList.contains('checked')") is True)
        check("标题显示这个标签",
              cdp.js("document.getElementById('viewTitle').textContent") ==
              cdp.js(f"""(() => {{ const r = document.querySelector('#tagList .tag-row[data-tag-name="{a}"]');
                          return r.querySelector('.tname').textContent; }})()"""))

        print("3) 再勾一个 → 取交集（同时拥有）")
        cdp.js(f"""(() => {{ const r = document.querySelector('#tagList .tag-row[data-tag-name="{b}"]');
                    r.click(); }})()""")
        expect_ab = len([c for c in comics
                         if {a, b} <= {t["name"] for t in c.get("tags") or []}])
        ok = wait_until(lambda: cdp.js("document.querySelectorAll('.card').length") == expect_ab, 8)
        got = cdp.js("document.querySelectorAll('.card').length")
        check("结果等于交集数量", ok, f"{got} vs {expect_ab}")
        check("标题显示已选数量",
              cdp.js("document.getElementById('viewTitle').textContent") == "已选 2 个标签",
              cdp.js("document.getElementById('viewTitle').textContent"))
        sub = cdp.js("document.getElementById('viewSub').textContent")
        check("副标题写明同时拥有", "同时拥有" in sub, sub)
        check("与接口结果一致",
              api(f"/api/comics?tag={q(a)}&tag={q(b)}")["total"] == expect_ab,
              str(api(f"/api/comics?tag={q(a)}&tag={q(b)}")["total"]))

        print("4) 取消一个勾选 → 回到单标签")
        cdp.js(f"""(() => {{ const r = document.querySelector('#tagList .tag-row[data-tag-name="{b}"]');
                    r.click(); }})()""")
        check("回到单标签结果",
              wait_until(lambda: cdp.js("document.querySelectorAll('.card').length") == expect_a, 8))
        check("勾选状态同步", cdp.js(f"document.querySelector('#tagList .tag-row[data-tag-name=\"{b}\"]').getAttribute('aria-checked')") == "false")

        print("5) 「全部作品」清空筛选")
        cdp.js("document.querySelector('.nav-item[data-tag=\"\"]').click()")
        check("回到全部作品",
              wait_until(lambda: cdp.js("document.getElementById('viewTitle').textContent") == "全部作品", 8),
              cdp.js("document.getElementById('viewTitle').textContent"))
        check("所有勾选被清空",
              cdp.js("[...document.querySelectorAll('#tagList .tag-row.checked')].length") == 0)

        print("6) 设置 → 标签管理：增 / 改 / 删")
        cdp.js("document.getElementById('settingsBtn').click()")
        wait_for(cdp, "!document.getElementById('settingsPage').hidden", 8)
        check("设置是独立页面（菜单首页）",
              cdp.js("!document.querySelector('.set-view[data-view=home]').hidden"))
        check("标签列表不在设置首页，收在子页里",
              cdp.js("document.querySelector('.set-view[data-view=tags]').hidden"))

        cdp.js("document.querySelector('.set-row[data-go=\"tags\"]').click()")
        wait_for(cdp, "!document.querySelector('.set-view[data-view=tags]').hidden", 8)
        check("进入标签子页后标题切换",
              cdp.js("document.getElementById('setTitle').textContent") == "标签管理")
        check("返回按钮变成「设置」",
              cdp.js("document.getElementById('setBackLabel').textContent") == "设置")
        wait_for(cdp, "document.querySelectorAll('#tagAdminList .tag-admin-row').length > 0", 8)
        admin = cdp.js("""(() => ({
            rows: [...document.querySelectorAll('#tagAdminList .tag-admin-row')].map(r => r.dataset.adminName),
            edits: document.querySelectorAll('#tagAdminList [data-act=edit]').length,
            dels: document.querySelectorAll('#tagAdminList [data-act=remove]').length,
            addBtn: !!document.getElementById('tagAddBtn'),
        }))()""")
        total_tags = len(api("/api/tags")["tags"])
        check("管理列表列出全部标签", len(admin["rows"]) == total_tags,
              f'{len(admin["rows"])} vs {total_tags}')
        check("每行都有编辑和删除", admin["edits"] == len(admin["rows"]) and admin["dels"] == len(admin["rows"]),
              json.dumps(admin, ensure_ascii=False))
        check("有新建标签入口", admin["addBtn"] is True)

        # 修改显示名
        print("7) 改显示名")
        cdp.js(f"""(() => {{
            const row = document.querySelector('#tagAdminList .tag-admin-row[data-admin-name="{TMP}"]');
            row.querySelector('[data-act=edit]').click();
            row.querySelector('[data-field=display]').value = '改过的显示名';
            row.querySelector('[data-act=save]').click();
        }})()""")
        ok = wait_until(lambda: any(t["label"] == "改过的显示名" for t in api("/api/tags")["tags"]), 8)
        check("显示名已保存", ok, json.dumps([t["label"] for t in api("/api/tags")["tags"]], ensure_ascii=False))
        check("侧栏跟着换显示名",
              wait_until(lambda: "改过的显示名" in cdp.js(
                  "[...document.querySelectorAll('#tagList .tname')].map(e=>e.textContent).join('|')"), 6),
              cdp.js("[...document.querySelectorAll('#tagList .tname')].map(e=>e.textContent).join('|')"))

        # 把临时标签挂到作品上，再验证改名 / 删除是否一并生效
        print("8) 改名：作品跟着变")
        tags_now = [t["name"] for t in api(f"/api/comics/{cid}")["tags"]]
        api(f"/api/comics/{cid}/tags", "PUT", {"tags": [{"name": n} for n in tags_now + [TMP]]})
        check("临时标签已加到作品上", TMP in [t["name"] for t in api(f"/api/comics/{cid}")["tags"]])
        time.sleep(1.8)
        check("旁挂 json 里也有它", TMP in (sidecar_tags(cid) or []), str(sidecar_tags(cid)))

        cdp.js(f"""(() => {{
            const row = document.querySelector('#tagAdminList .tag-admin-row[data-admin-name="{TMP}"]');
            row.querySelector('[data-act=edit]').click();
            row.querySelector('[data-field=name]').value = '{TMP_RENAMED}';
            row.querySelector('[data-act=save]').click();
        }})()""")
        ok = wait_until(lambda: TMP_RENAMED in [t["name"] for t in api(f"/api/comics/{cid}")["tags"]], 8)
        names = [t["name"] for t in api(f"/api/comics/{cid}")["tags"]]
        check("作品上的标签跟着改名", ok, json.dumps(names, ensure_ascii=False))
        check("旧名字不再残留在作品上", TMP not in names, json.dumps(names, ensure_ascii=False))
        ok = wait_until(lambda: TMP_RENAMED in (sidecar_tags(cid) or []), 8)
        check("旁挂 json 同步成新名字", ok, str(sidecar_tags(cid)))
        check("作品数量不变（仍挂在这本上）",
              next((t["count"] for t in api("/api/tags")["tags"] if t["name"] == TMP_RENAMED), 0) == 1,
              str([(t["name"], t["count"]) for t in api("/api/tags")["tags"]]))

        print("9) 删除：作品上不残留")
        # headless 里 confirm 会阻塞，直接接管成 true
        cdp.js("window.confirm = () => true;")
        cdp.js(f"""(() => {{
            const row = document.querySelector('#tagAdminList .tag-admin-row[data-admin-name="{TMP_RENAMED}"]');
            row.querySelector('[data-act=remove]').click();
        }})()""")
        # 浏览器 confirm 会阻塞，测试里直接回调 confirm 为 true
        time.sleep(0.5)
        ok = wait_until(lambda: TMP_RENAMED not in [t["name"] for t in api(f"/api/comics/{cid}")["tags"]], 8)
        names = [t["name"] for t in api(f"/api/comics/{cid}")["tags"]]
        check("作品上不再有这个标签", ok, json.dumps(names, ensure_ascii=False))
        check("作品本身还在", api(f"/api/comics/{cid}").get("id") == cid)
        ok = wait_until(lambda: TMP_RENAMED not in (sidecar_tags(cid) or []), 8)
        check("旁挂 json 也不再残留", ok, str(sidecar_tags(cid)))
        check("标签目录里没有它",
              all(t["name"] != TMP_RENAMED for t in api("/api/tags")["tags"]))

        print("10) 扫描一遍，删掉的标签不会被读回来")
        api("/api/scan", "POST", {"force": True})
        time.sleep(4)
        names = [t["name"] for t in api(f"/api/comics/{cid}")["tags"]]
        check("重新扫描后依然没有残留", TMP_RENAMED not in names and TMP not in names,
              json.dumps(names, ensure_ascii=False))
        check("原有标签完好", all(n in names for n in original), json.dumps(names, ensure_ascii=False))
        cdp.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        # 还原：把作品的标签恢复成测试前，删掉临时标签
        try:
            api(f"/api/comics/{cid}/tags", "PUT", {"tags": [{"name": n} for n in original]})
            api(f"/api/tags?name={q(TMP)}", "DELETE")
            api(f"/api/tags?name={q(TMP_RENAMED)}", "DELETE")
        except Exception:
            pass

    print(f"\n结果：{_ok} 通过，{_bad} 失败")
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
