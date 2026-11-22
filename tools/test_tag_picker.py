"""验证标签弹窗：每个分类一个下拉、分类内按标签名 A-Z 排序、新建标签（含显示名）。

只操作临时标签，不保存到作品，跑完自动清理。
用法：D:/python/python.exe tools/test_tag_picker.py
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
OUT = ROOT / "docs" / "screenshots"
TEST_DISPLAY = "下拉测试"
SORT_PREFIX = "__picker_sort_"

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


def cleanup():
    """删掉所有测试用标签（分类由上次选择的分类决定，不写死）。"""
    try:
        for t in api("/api/tags")["tags"]:
            if t["name"].endswith("__picker_test__") or SORT_PREFIX in t["name"]:
                api("/api/tags?name=" + urllib.parse.quote(t["name"]), "DELETE")
    except Exception:
        pass


# character:shigure 可能是库里本来就有的标签，测试前后要原样保留
def shigure_state():
    t = next((x for x in api("/api/tags")["tags"] if x["name"] == "character:shigure"), None)
    comics = [c["id"] for c in api("/api/comics")["items"]
              if any(x["name"] == "character:shigure" for x in (c.get("tags") or []))]
    return {"exists": t is not None, "display": (t or {}).get("display") or "", "comics": comics}


def restore_shigure(state):
    try:
        api("/api/tags?name=" + urllib.parse.quote("character:shigure"), "DELETE")
        if state["exists"]:
            api("/api/tags", "POST", {"name": "character:shigure", "display": state["display"]})
            for cid in state["comics"]:
                names = [x["name"] for x in api(f"/api/comics/{cid}").get("tags", [])]
                if "character:shigure" not in names:
                    names.append("character:shigure")
                    api(f"/api/comics/{cid}/tags", "PUT", {"tags": names})
        time.sleep(0.8)
    except Exception as e:
        print("  恢复 character:shigure 失败：" + str(e))


# 统计每个下拉里的条目（含占位项）
SELECTS_JS = """(() => [...document.querySelectorAll('#tagSelects select')].map(s => ({
    ns: s.dataset.ns,
    cat: s.closest('.tag-add-field').querySelector('.cat-name').textContent.trim(),
    placeholder: s.options.length ? s.options[0].textContent : '',
    values: [...s.options].slice(1).map(o => o.value),
    texts: [...s.options].slice(1).map(o => o.textContent),
})))()"""


def main():
    original_shigure = shigure_state()
    cleanup()
    if original_shigure["exists"]:
        api("/api/tags?name=" + urllib.parse.quote("character:shigure"), "PUT", {"display": "时雨"})
    else:
        api("/api/tags", "POST", {"name": "character:shigure", "display": "时雨"})
    # 同一分类的三条，故意让显示名与标签名反序，用来验证「按标签名排序、与显示名无关」
    api("/api/tags", "POST", {"name": "parody:" + SORT_PREFIX + "zzz", "display": "AAA"})
    api("/api/tags", "POST", {"name": "parody:" + SORT_PREFIX + "aaa", "display": "ZZZ"})
    api("/api/tags", "POST", {"name": "parody:" + SORT_PREFIX + "mmm", "display": "MMM"})

    comics = api("/api/comics")["items"]
    if not comics:
        raise SystemExit("库里没有作品，无法测试标签弹窗")
    # 选一本没有「时雨」的
    target = next((c for c in comics if "character:shigure" not in [t["name"] for t in c.get("tags", [])]), comics[0])
    cid = target["id"]

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
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#comic={cid}"})
        wait_for(cdp, "document.readyState === 'complete'")
        wait_for(cdp, "document.getElementById('detail').hidden === false", 20)
        wait_for(cdp, "document.querySelectorAll('.page-cell').length > 0", 25)
        for _ in range(3):
            cdp.js("(() => { const b = document.getElementById('detailEdit'); if (b.getAttribute('aria-pressed') !== 'true') b.click(); })()")
            cdp.js("document.getElementById('dAddTag').click()")
            if wait_for(cdp, "document.getElementById('tagModal').hidden === false", 5):
                break
            time.sleep(0.4)
        time.sleep(0.5)

        print("1) 每个分类一个下拉")
        sels = cdp.js(SELECTS_JS)
        check("有多个下拉（按分类拆分）", len(sels) >= 2, json.dumps(sels, ensure_ascii=False))
        check("每个下拉都带分类名", all(s["cat"] for s in sels),
              json.dumps([s["cat"] for s in sels], ensure_ascii=False))
        check("每个分类只出现一次", len({s["ns"] for s in sels}) == len(sels),
              json.dumps([s["ns"] for s in sels], ensure_ascii=False))
        check("下拉里不再有 optgroup", cdp.js("!document.querySelector('#tagSelects optgroup')"))
        check("占位文案点明分类", all(s["cat"][:2] in s["placeholder"] or s["placeholder"].startswith("选择")
                                for s in sels),
              json.dumps([s["placeholder"] for s in sels], ensure_ascii=False))

        all_values = [v for s in sels for v in s["values"]]
        check("目录里的标签都在下拉里", "character:shigure" in all_values,
              json.dumps(all_values, ensure_ascii=False))
        check("已加上的标签不在下拉里",
              not any(n in all_values for n in [t["name"] for t in target.get("tags", [])]),
              json.dumps(all_values, ensure_ascii=False))

        per = {}
        for s in sels:
            if s["ns"]:
                bad = [v for v in s["values"] if not v.startswith(s["ns"] + ":")]
            else:
                bad = [v for v in s["values"] if ":" in v]
            per[s["cat"]] = bad
        check("每个下拉只放本分类的标签", all(not v for v in per.values()),
              json.dumps(per, ensure_ascii=False))

        print("2) 分类内按标签名 A-Z 排序（与显示名无关）")
        parody = next((s for s in sels if s["ns"] == "parody"), None)
        order = [v.split(":", 1)[1] for v in (parody or {}).get("values", [])
                 if v.startswith("parody:" + SORT_PREFIX)]
        check("同分类标签按名排序", order == [SORT_PREFIX + "aaa", SORT_PREFIX + "mmm", SORT_PREFIX + "zzz"],
              json.dumps(order, ensure_ascii=False))
        texts = (parody or {}).get("texts", [])
        check("排序不受显示名影响", "AAA" in "".join(texts) and order[:1] == [SORT_PREFIX + "aaa"],
              json.dumps(texts, ensure_ascii=False))
        full = [v for v in (parody or {}).get("values", [])]
        check("整组也是升序", full == sorted(full, key=lambda v: v.split(":", 1)[1].lower()),
              json.dumps(full, ensure_ascii=False))

        print("3) 侧栏标签列表同样按名排序")
        side = cdp.js("""(() => {
            const rows = [...document.querySelectorAll('#tagList .tag-row')];
            return rows.map(r => r.dataset.tagName);
        })()""")
        parody_side = [n.split(":", 1)[1] for n in side if n.startswith("parody:")]
        check("侧栏原作组按名排序", parody_side == sorted(parody_side, key=str.lower),
              json.dumps(parody_side, ensure_ascii=False))

        print("4) 下拉选中即加入")
        cdp.js("""(() => {
            const s = [...document.querySelectorAll('#tagSelects select')]
              .find(x => [...x.options].some(o => o.value === 'character:shigure'));
            s.value = 'character:shigure';
            s.dispatchEvent(new Event('change', { bubbles: true }));
        })()""")
        time.sleep(0.3)
        chips = cdp.js("[...document.querySelectorAll('#tagChips .chip-text b')].map(e => e.textContent.trim())")
        check("选中后加入作品", "时雨" in chips, json.dumps(chips, ensure_ascii=False))
        left = cdp.js("""(() => [...document.querySelectorAll('#tagSelects select')]
            .flatMap(s => [...s.options].map(o => o.value)))()""")
        check("加入后从下拉里移除", "character:shigure" not in left, json.dumps(left, ensure_ascii=False))
        opt = cdp.js("""(() => {
            const s = [...document.querySelectorAll('#tagSelects select')]
              .find(x => x.dataset.ns === 'parody');
            return s ? s.value : 'missing';
        })()""")
        check("选中后下拉回到占位", opt == "", str(opt))

        print("5) 新建标签并填写显示名")
        cdp.js("document.getElementById('tagNewBtn').click()")
        check("打开新建弹窗", wait_for(cdp, "document.getElementById('newTagModal').hidden === false", 5))
        check("新建弹窗有显示名输入框", cdp.js("!!document.getElementById('newTagDisplay')"))
        cats = cdp.js("""[...document.querySelectorAll('#newTagCats .cat-btn')]
            .map(b => ({ ns: b.dataset.ns, label: b.textContent.trim() }))""")
        check("分类选择器含「其他」", any(c["ns"] == "other" and c["label"] == "其他" for c in cats),
              json.dumps(cats, ensure_ascii=False))
        cdp.js("document.querySelector('#newTagCats .cat-btn[data-ns=other]').click()")
        time.sleep(0.15)
        check("选「其他」后提示用 other: 前缀",
              "other:" in cdp.js("document.getElementById('newTagHint').textContent"),
              cdp.js("document.getElementById('newTagHint').textContent"))
        check("占位示例跟着分类变",
              cdp.js("document.getElementById('newTagName').placeholder") == "内容，例如 杂项",
              cdp.js("document.getElementById('newTagName').placeholder"))
        cdp.js("""(() => {
            const n = document.getElementById('newTagName');
            n.value = '__picker_test__';
            n.dispatchEvent(new Event('input', { bubbles: true }));
            const d = document.getElementById('newTagDisplay');
            d.value = '下拉测试';
            d.dispatchEvent(new Event('input', { bubbles: true }));
        })()""")
        time.sleep(0.2)
        hint = cdp.js("document.getElementById('newTagHint').textContent")
        check("提示行显示自定义显示名", "下拉测试" in hint, hint)
        cdp.js("document.getElementById('newTagSave').click()")
        check("创建后关闭弹窗", wait_for(cdp, "document.getElementById('newTagModal').hidden === true", 8))

        chips = cdp.js("[...document.querySelectorAll('#tagChips .chip-text b')].map(e => e.textContent.trim())")
        check("新标签自动加入当前作品", "下拉测试" in chips, json.dumps(chips, ensure_ascii=False))
        created = next((t for t in api("/api/tags")["tags"]
                        if t["name"].endswith("__picker_test__")), None)
        check("标签已写入目录", created is not None,
              json.dumps([t["name"] for t in api("/api/tags")["tags"]], ensure_ascii=False))
        check("新建时选的分类写进标签名", (created or {}).get("name") == "other:__picker_test__",
              str((created or {}).get("name")))
        check("显示名已保存", (created or {}).get("display") == TEST_DISPLAY,
              json.dumps(created, ensure_ascii=False))

        print("6) 移除标签后回到下拉")
        cdp.js("""(() => {
            const chips = [...document.querySelectorAll('#tagChips .tag-chip')];
            const hit = chips.find(c => c.querySelector('.chip-text b')?.textContent.trim() === '时雨');
            if (hit) hit.querySelector('[data-ri]').click();
        })()""")
        time.sleep(0.3)
        back = cdp.js("""(() => [...document.querySelectorAll('#tagSelects select')]
            .flatMap(s => [...s.options].map(o => o.value)))()""")
        check("移除后重新出现在下拉", "character:shigure" in back, json.dumps(back, ensure_ascii=False))

        OUT.mkdir(parents=True, exist_ok=True)
        cdp.shot(OUT / "tags-picker.png")
        cdp.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        cleanup()
        restore_shigure(original_shigure)

    print(f"\n结果：{_ok} 通过, {_bad} 失败")
    if _bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
