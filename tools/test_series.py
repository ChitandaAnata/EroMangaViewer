"""系列功能回归：接口 + 详情页入口 + 系列页排序。

用法：D:/python/python.exe tools/test_series.py

会往当前库里塞 3 本临时样本书（`__series_test_a/b/c.cbz`），跑完删掉。
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CDP, CHROME, PORT, USER_DIR, library_dir  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = library_dir()
NAMES = ["__series_test_a.cbz", "__series_test_b.cbz", "__series_test_c.cbz"]
SERIES = "テスト系列 / 三部作"
RENAMED = "改过名的系列"

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
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def wait_scan(timeout: float = 180.0) -> None:
    before = float(api("/api/scan/progress")["scan"].get("finished_at") or 0)
    api("/api/scan", "POST", {"force": False})
    end = time.time() + timeout
    while time.time() < end:
        snap = api("/api/scan/progress")["scan"]
        if snap["running"]:
            time.sleep(0.25)
            continue
        if float(snap.get("finished_at") or 0) > before:
            return
        time.sleep(0.2)


def make_samples() -> None:
    for i, name in enumerate(NAMES):
        out = LIB / name
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
            for p in range(3):
                im = Image.new("RGB", (400, 600), (30 + i * 60, 40 + p * 20, 90))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=70)
                z.writestr(f"{p + 1:03d}.jpg", buf.getvalue())
    print(f"已生成 3 本测试漫画：{', '.join(NAMES)}")


def find_ids() -> dict:
    """按标题找刚扫进来的三本样本书（标题就是文件名去掉扩展名）。"""
    out = {}
    for item in api("/api/comics?limit=2000")["items"]:
        title = str(item.get("title") or "")
        if title.startswith("__series_test_"):
            out[title] = item["id"]
    return out


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


def open_page(cdp, hash_: str) -> None:
    cdp.cmd("Page.navigate", {"url": BASE + "/" + hash_})
    wait(cdp, "document.readyState === 'complete'", 20)


def cleanup() -> None:
    for name in NAMES:
        p = LIB / name
        for target in (p, Path(str(p) + ".json"), Path(str(p) + ".mangashelf.json")):
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                pass
    wait_scan(120)


def main() -> None:
    print("0) 准备样本书")
    make_samples()
    wait_scan()
    ids = find_ids()
    check("三本样本都进了库", len(ids) == 3, json.dumps(ids, ensure_ascii=False))
    if len(ids) != 3:
        cleanup()
        raise SystemExit(1)

    a = ids["__series_test_a"]
    b = ids["__series_test_b"]
    c = ids["__series_test_c"]
    print("1) 接口：加入系列 / 顺序 / 目录")
    r = api(f"/api/comics/{a}", "PUT", {"series": SERIES})
    check("第一本成为第 1 话", r["comic"]["series"] == SERIES and r["comic"]["seriesOrder"] == 1,
          json.dumps(r["comic"].get("series")))
    api(f"/api/comics/{b}", "PUT", {"series": SERIES})
    api(f"/api/comics/{c}", "PUT", {"series": SERIES})
    got = api("/api/series")["items"]
    mine = [s for s in got if s["name"] == SERIES]
    check("系列目录里有它且计数为 3", bool(mine) and mine[0]["count"] == 3,
          json.dumps(mine, ensure_ascii=False))
    check("目录带封面", bool(mine and mine[0]["cover"].startswith("/api/comics/")))

    order = api("/api/series?name=" + urllib.parse.quote(SERIES))
    check("默认顺序 = 加入顺序", [i["id"] for i in order["items"]] == [a, b, c],
          json.dumps([i["id"] for i in order["items"]]))
    check("详情接口也带 series 字段",
          api(f"/api/comics/{b}")["series"] == SERIES)

    print("2) 接口：调整顺序")
    api("/api/series/order", "POST", {"name": SERIES, "ids": [c, a, b]})
    order = api("/api/series?name=" + urllib.parse.quote(SERIES))
    check("顺序已改成 C/A/B", [i["id"] for i in order["items"]] == [c, a, b],
          json.dumps([i["id"] for i in order["items"]]))
    check("序号连续 1..3",
          [i["seriesOrder"] for i in order["items"]] == [1, 2, 3])

    print("3) 旁挂 json 也记下了系列（跟着文件走）")
    side = LIB / (NAMES[0] + ".json")
    if not side.exists():
        side = LIB / (NAMES[0] + ".mangashelf.json")
    data = {}
    for _ in range(20):   # 旁挂写入有 0.6s 防抖，给它几秒
        for cand in (LIB / (NAMES[0] + ".json"), LIB / (NAMES[0] + ".mangashelf.json")):
            if cand.exists():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                side = cand
        if data.get("series"):
            break
        time.sleep(0.4)
    check("json 里有 series", data.get("series") == SERIES,
          json.dumps(data.get("series"), ensure_ascii=False) +
          " 文件=" + ", ".join(p.name for p in LIB.glob("__series_test*") if p.suffix == ".json"))
    check("json 里有 seriesOrder", int(data.get("seriesOrder") or 0) == 2, str(data.get("seriesOrder")))

    print("4) 接口：改名（合并到新名字）")
    api("/api/series/rename", "POST", {"from": SERIES, "to": RENAMED})
    order = api("/api/series?name=" + urllib.parse.quote(RENAMED))
    check("改名后仍 3 本且顺序不变", [i["id"] for i in order["items"]] == [c, a, b])
    check("旧名字已不存在",
          not [s for s in api("/api/series")["items"] if s["name"] == SERIES])

    print("5) 界面：详情页编辑模式的系列入口")
    proc, cdp = launch(1280, 900)
    try:
        open_page(cdp, "#comic=" + a)
        check("详情页打开", wait(cdp, "!document.getElementById('detail').hidden", 20))
        check("只读态显示系列名", wait(cdp, "!!document.querySelector('#detailSeries [data-series]')", 10))
        check("只读态没有编辑按钮",
              cdp.js("!document.getElementById('dSeriesBtn')") is True)

        cdp.js("document.getElementById('detailEdit').click()")
        check("编辑态出现「编辑系列」", wait(cdp, "!!document.getElementById('dSeriesBtn')", 8))
        cdp.js("document.getElementById('dSeriesBtn').click()")
        check("弹出菜单", wait(cdp, "!document.getElementById('ctxMenu').hidden", 8))
        labels = cdp.js("[...document.querySelectorAll('#ctxMenu .ctx-item')].map(b=>b.innerText.trim())")
        check("菜单里有新建 / 加入现有 / 移出",
              any("新建系列" in x for x in labels) and any("加入现有" in x for x in labels)
              and any("移出系列" in x for x in labels), json.dumps(labels, ensure_ascii=False))

        print("6) 界面：点系列名进系列页")
        cdp.js("document.getElementById('ctxMenu').hidden = true")
        cdp.js("document.querySelector('#detailSeries [data-series]').click()")
        check("系列页打开", wait(cdp, "!document.getElementById('seriesView').hidden", 12))
        check("标题就是系列名",
              wait(cdp, f"document.getElementById('seriesTitle').textContent === {json.dumps(RENAMED)}", 10))
        check("列出 3 本",
              wait(cdp, "document.querySelectorAll('#seriesList .series-row[data-id]').length === 3", 15))
        rows = cdp.js("document.querySelectorAll('#seriesList .series-row[data-id]').length")
        check("序号按顺序渲染",
              cdp.js("[...document.querySelectorAll('#seriesList .sr-index')].map(e=>e.textContent).join(',')")
              == "1,2,3", str(rows))
        check("第一行是 C 本",
              cdp.js("document.querySelector('#seriesList .series-row').dataset.id") == c)
        check("书架被让开", cdp.js("document.getElementById('shelfView').hidden") is True)

        print("7) 界面：↑↓ 按钮排序并落库")
        cdp.js("document.querySelectorAll('#seriesList .series-row')[2].querySelector('[data-move=\"-1\"]').click()")
        time.sleep(1.5)
        after = api("/api/series?name=" + urllib.parse.quote(RENAMED))
        check("下移一本后顺序变成 C/B/A",
              [i["id"] for i in after["items"]] == [c, b, a],
              json.dumps([i["id"] for i in after["items"]]))
        check("界面上的顺序也变了",
              cdp.js("document.querySelector('#seriesList .series-row').dataset.id") == c)

        print("8) 界面：返回上一屏（回到刚才那本的详情）")
        cdp.js("document.getElementById('seriesBack').click()")
        check("回到详情页", wait(cdp, "!document.getElementById('detail').hidden", 12))
        check("系列页已收起", cdp.js("document.getElementById('seriesView').hidden") is True)
        check("标题是 A 本",
              wait(cdp, "(document.getElementById('detailTitle').textContent || '').startsWith('__series_test_a')", 10))

        print("9) 界面：加载无 JS 报错")
        errs = cdp.js("window.__errors") or []
        check("无 JS 报错", errs == [], json.dumps(errs, ensure_ascii=False))
    finally:
        try:
            cdp.ws.close()
        except Exception:
            pass
        proc.kill()

    print("10) 解散系列：作品保留、系列清空")
    n = api("/api/series?name=" + urllib.parse.quote(RENAMED))["total"]
    check("解散前 3 本", n == 3, str(n))
    res = api("/api/series?name=" + urllib.parse.quote(RENAMED), "DELETE")
    check("解散返回条数", res.get("removed") == 3, json.dumps(res))
    check("系列目录里没有了",
          not [s for s in api("/api/series")["items"] if s["name"] == RENAMED])
    check("作品还在库里", len([i for i in api("/api/comics?limit=2000")["items"]
                            if i["id"] in (a, b, c)]) == 3)
    check("作品身上的系列已清空", api(f"/api/comics/{a}")["series"] == "")

    cleanup()
    print(f"\n通过 {passed} 项，失败 {failed} 项")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
