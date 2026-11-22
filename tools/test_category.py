"""作品分类（Dojinshi / Manga / ArtistCG / GameCG / Non-H / ImageSet）端到端验证。

覆盖：接口读写与别名归一化、旁挂文件持久化、换设备后从旁挂文件读回、
书架卡片放在书名前的分类标记、详情页标题与标签之间的分类下拉。
跑完会把被测作品还原成原来的分类。
用法：D:/python/python.exe tools/test_category.py
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CHROME, PORT, USER_DIR, CDP, wait_for  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

_ok = 0
_bad = 0

EXPECT = [
    ("dojinshi", "Dojinshi"),
    ("manga", "Manga"),
    ("artistcg", "ArtistCG"),
    ("gamecg", "GameCG"),
    ("non-h", "Non-H"),
    ("imageset", "ImageSet"),
]


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


def comic(cid):
    return api(f"/api/comics/{cid}")


def set_cat(cid, value):
    api(f"/api/comics/{cid}", "PUT", {"category": value})
    return comic(cid)


def sidecar_file(rel_path: str) -> Path:
    root = Path(api("/api/state")["library"])
    return root / (rel_path + ".json")


def read_sidecar(rel_path: str):
    p = sidecar_file(rel_path)
    try:
        return p, json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return p, None


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


def main():
    items = api("/api/comics")["items"]
    if not items:
        raise SystemExit("库里没有作品，无法测试分类")
    target = items[0]
    cid = target["id"]
    original = target.get("category") or ""

    print("1) 接口：分类表与返回字段")
    state = api("/api/state")
    cats = [(c["key"], c["label"]) for c in state.get("categories", [])]
    check("分类表是固定的 6 个", cats == EXPECT, json.dumps(cats, ensure_ascii=False))
    check("作品带 category 字段", "category" in target, json.dumps(target, ensure_ascii=False)[:120])
    check("作品带 categoryLabel 字段", "categoryLabel" in target)

    print("2) 设置分类：大小写 / 别名 / 非法值")
    got = set_cat(cid, "Dojinshi")
    check("Dojinshi 存成 dojinshi", got["category"] == "dojinshi", got.get("category"))
    check("返回显示名", got["categoryLabel"] == "Dojinshi", got.get("categoryLabel"))

    got = set_cat(cid, "doujinshi")
    check("别名 doujinshi → dojinshi", got["category"] == "dojinshi", got.get("category"))
    got = set_cat(cid, "NonH")
    check("NonH → non-h", got["category"] == "non-h", got.get("category"))
    check("Non-H 显示名", got["categoryLabel"] == "Non-H", got.get("categoryLabel"))
    got = set_cat(cid, "ImageSet")
    check("ImageSet 存成 imageset", got["category"] == "imageset", got.get("category"))

    got = set_cat(cid, "乱七八糟")
    check("非法值按未分类处理", got["category"] == "" and got["categoryLabel"] == "",
          json.dumps([got.get("category"), got.get("categoryLabel")], ensure_ascii=False))

    print("3) 分类写进旁挂文件（跟着作品走）")
    got = set_cat(cid, "gamecg")
    path, data = read_sidecar(got["path"])
    ok = wait_until(lambda: (read_sidecar(got["path"])[1] or {}).get("category") == "gamecg", 8)
    path, data = read_sidecar(got["path"])
    check("旁挂 json 里有分类", ok and (data or {}).get("category") == "gamecg",
          json.dumps((data or {}).get("category"), ensure_ascii=False))

    print("4) 模拟换设备：从旁挂文件读回分类")
    if data is not None:
        data["category"] = "imageset"
        data["updatedAt"] = int(time.time()) + 120  # 比数据库新，扫描时以文件为准
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    api("/api/scan", "POST", {"force": False})
    ok = wait_until(lambda: comic(cid).get("category") == "imageset", 30)
    check("扫描后分类从文件恢复", ok, comic(cid).get("category"))

    print("5) 书架卡片：分类标记在书名前")
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

        card = cdp.js(f"""(() => {{
            const el = document.querySelector('.card[data-id="{cid}"]');
            if (!el) return null;
            const chip = el.querySelector('.card-cat');
            const title = el.querySelector('.card-title');
            return {{
                chip: chip ? chip.textContent.trim() : '',
                title: title ? title.textContent.trim() : '',
                before: !!(chip && title && (chip.compareDocumentPosition(title) & 4)),
                color: chip ? getComputedStyle(chip).color : '',
                headIsRow: !!el.querySelector('.card-head'),
            }};
        }})()""")
        check("书名前有分类标记", bool(card) and card["chip"] == "ImageSet",
              json.dumps(card, ensure_ascii=False))
        check("标记排在书名之前", bool(card) and card["before"], json.dumps(card, ensure_ascii=False))
        check("标记带分类配色", bool(card) and card["color"] not in ("", "rgb(139, 147, 167)"),
              (card or {}).get("color", ""))

        print("6) 详情页：标题与标签之间的分类下拉")
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#comic={cid}"})
        wait_for(cdp, "document.getElementById('detail').hidden === false", 20)
        wait_for(cdp, "document.querySelectorAll('.page-cell').length > 0", 25)
        time.sleep(0.4)
        # 分类下拉属于编辑类控件，先开编辑模式才看得到
        cdp.js("document.getElementById('detailEdit').click()")
        wait_for(cdp, "document.getElementById('detail').classList.contains('editing')", 8)
        time.sleep(0.2)

        palette = cdp.js("Object.fromEntries(ComicCat.LIST.map(c => [c.key, c.color]))")
        check("分类配色：红/橙黄/浅黄/绿/天蓝/紫", palette == {
            "dojinshi": "#ff6b9d", "manga": "#ff9f43", "artistcg": "#ffd166",
            "gamecg": "#2fd39a", "non-h": "#38bdf8", "imageset": "#a06bff",
        }, json.dumps(palette, ensure_ascii=False))

        sel = cdp.js("""(() => {
            const s = document.getElementById('detailCat');
            const t = document.getElementById('detailTitle');
            const tags = document.getElementById('detailTags');
            const r = (e) => { const b = e.getBoundingClientRect(); return {t: b.top, b: b.bottom}; };
            return {
                values: [...s.options].map(o => o.value),
                labels: [...s.options].map(o => o.textContent.trim()),
                value: s.value,
                disabled: s.disabled,
                between: r(s).t >= r(t).b - 1 && r(s).b <= r(tags).t + 1,
            };
        })()""")
        check("下拉含 6 个分类 + 未分类", sel["values"] == [""] + [k for k, _ in EXPECT],
              json.dumps(sel["values"], ensure_ascii=False))
        check("下拉用官方写法", sel["labels"] == ["未分类"] + [v for _, v in EXPECT],
              json.dumps(sel["labels"], ensure_ascii=False))
        check("当前值就是这本的分类", sel["value"] == "imageset", sel["value"])
        check("位置在标题和标签之间", sel["between"], json.dumps(sel, ensure_ascii=False))

        print("7) 下拉改分类：立刻保存并同步书架")
        cdp.js("""(() => { const s = document.getElementById('detailCat');
            s.value = 'manga'; s.dispatchEvent(new Event('change', { bubbles: true })); })()""")
        ok = wait_until(lambda: comic(cid).get("category") == "manga", 8)
        check("选择后立即写库", ok, comic(cid).get("category"))
        check("下拉配色跟着变",
              cdp.js("getComputedStyle(document.getElementById('detailCat')).color") == "rgb(255, 159, 67)",
              cdp.js("getComputedStyle(document.getElementById('detailCat')).color"))
        check("书架卡片同步更新",
              wait_until(lambda: "Manga" in cdp.js(
                  f"(() => {{ const c = document.querySelector('.card[data-id=\"{cid}\"] .card-cat');"
                  " return c ? c.textContent : ''; })()"), 6),
              cdp.js(f"document.querySelector('.card[data-id=\"{cid}\"] .card-cat')?.textContent"))

        print("8) 清空分类回到未分类")
        cdp.js("""(() => { const s = document.getElementById('detailCat');
            s.value = ''; s.dispatchEvent(new Event('change', { bubbles: true })); })()""")
        ok = wait_until(lambda: comic(cid).get("category") == "", 8)
        check("清空后存成未分类", ok, comic(cid).get("category"))
        check("未分类显示虚线样式",
              cdp.js("document.getElementById('detailCat').dataset.cat") == "none",
              cdp.js("document.getElementById('detailCat').dataset.cat"))
        check("书架上的标记消失",
              cdp.js(f"!document.querySelector('.card[data-id=\"{cid}\"] .card-cat')") is True)

        check("无 JS 报错", cdp.js("window.__errors ? window.__errors.length : 0") in (0, None))
        cdp.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()

    print("9) 还原")
    set_cat(cid, original)
    if original:
        wait_until(lambda: comic(cid).get("category") == original, 8)
    # 旁挂文件也写回原值，别留下测试痕迹
    rel = comic(cid)["path"]
    p, data = read_sidecar(rel)
    if data is not None:
        data["category"] = comic(cid)["category"]
        data["updatedAt"] = int(time.time())
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    check("已还原成原来的分类", comic(cid).get("category") == original, comic(cid).get("category"))

    print(f"\n结果：{_ok} 通过，{_bad} 失败")
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
