"""更新状态 / 喜爱标记 / 侧栏三个新筛选的端到端验证。

覆盖：status/favorite 写进数据库与旁挂书签、强制重扫不丢、旧版书签（没有这两个字段）
不会把已有标记清掉；侧栏四项的名称/计数/筛选；详情页状态下拉只在编辑模式出现、
爱心按钮常驻且切换立刻反映到侧栏列表与计数。
跑完清理临时作品与旁挂文件。
用法：D:/python/python.exe tools/test_status_favorite.py
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CDP, CHROME, PORT, USER_DIR, wait_for, library_dir  # noqa: E402

LIB = library_dir()
NAME = "__status_fav_test__.cbz"
SIDECAR = LIB / (NAME + ".json")

passed = 0
failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
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


def wait_scan(force: bool = False) -> None:
    before = float(api("/api/scan/progress")["scan"].get("finished_at") or 0)
    api("/api/scan", "POST", {"force": force})
    end = time.time() + 120
    while time.time() < end:
        snap = api("/api/scan/progress")["scan"]
        if snap["running"]:
            time.sleep(0.25)
            continue
        if float(snap.get("finished_at") or 0) > before:
            return
        time.sleep(0.2)
    raise SystemExit("扫描超时")


def make_archive() -> None:
    out = LIB / NAME
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for i in range(2):
            im = Image.new("RGB", (120, 180), (50 + i * 60, 90, 150))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70)
            z.writestr(f"{i + 1:03d}.jpg", buf.getvalue())


def lib_counts() -> dict:
    """侧栏三项的计数（与 renderCounts 同口径：整库过滤，不看标签勾选）。"""
    items = api("/api/comics?limit=100000")["items"]
    return {
        "untagged": sum(1 for c in items if not (c.get("tags") or [])),
        "updating": sum(1 for c in items if c.get("status") == "updating"),
        "favorite": sum(1 for c in items if c.get("favorite")),
    }


def find_id() -> str:
    for item in api("/api/comics")["items"]:
        if item["title"] == NAME[:-4]:
            return item["id"]
    raise SystemExit("找不到临时作品")


def side() -> dict:
    for p in (SIDECAR, LIB / (NAME + ".mangashelf.json")):
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


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


def wait(fn, timeout: float = 10.0, step: float = 0.25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


NAV_JS = """(() => [...document.querySelectorAll('.nav-item')].map(b => ({
    tag: b.dataset.tag,
    text: b.querySelector('span').textContent.trim(),
    count: b.querySelector('em').textContent.trim(),
    active: b.classList.contains('active'),
})))()"""


DETAIL_JS = """(() => {
    const st = document.getElementById('detailStatus');
    const fav = document.getElementById('detailFav');
    const vis = (el) => !!el && el.offsetParent !== null;
    return {
        statusVisible: vis(st),
        statusValue: st ? st.value : null,
        statusDisabled: st ? st.disabled : null,
        favVisible: vis(fav),
        favPressed: fav ? fav.getAttribute('aria-pressed') : null,
        editing: document.getElementById('detail').classList.contains('editing'),
    };
})()"""


def main() -> None:
    for p in (LIB / NAME, SIDECAR, LIB / (NAME + ".mangashelf.json")):
        p.unlink(missing_ok=True)
    make_archive()
    wait_scan()

    cid = find_id()
    print(f"被测作品 id={cid}")
    # 上一轮异常中断时数据库可能残留标记，这里显式归零保证测试可重复跑
    api(f"/api/comics/{cid}", "PUT", {"status": "", "favorite": False})
    api("/api/sidecar/sync", "POST", {"force": True})
    # 用户真实库里可能已经有标了状态/喜爱的作品，断言一律按「基线 + 增量」比，不动别人的数据
    base = lib_counts()
    print(f"基线计数 {base}")

    print("1) 数据库 + 旁挂书签同步")
    row = api(f"/api/comics/{cid}")
    check("默认更新完毕", row.get("status") == "", str(row.get("status")))
    check("默认非喜爱", row.get("favorite") is False, str(row.get("favorite")))

    api(f"/api/comics/{cid}", "PUT", {"status": "updating"})
    api(f"/api/comics/{cid}", "PUT", {"favorite": True})
    api("/api/sidecar/sync", "POST", {"force": True})
    check("旁挂文件已生成", SIDECAR.is_file(), str(SIDECAR))
    check("旁挂写入 status", side().get("status") == "updating", str(side().get("status")))
    check("旁挂写入 favorite", side().get("favorite") is True, str(side().get("favorite")))

    print("2) 强制重扫不丢")
    wait_scan(force=True)
    time.sleep(0.4)
    row = api(f"/api/comics/{cid}")
    check("重扫后仍是更新中", row.get("status") == "updating", str(row.get("status")))
    check("重扫后仍是喜爱", row.get("favorite") is True, str(row.get("favorite")))
    check("重扫后旁挂仍在", SIDECAR.is_file())

    print("3) 旧版书签（没有新字段）不会清掉已有标记")
    data = side()
    data.pop("status", None)
    data.pop("favorite", None)
    data["updatedAt"] = time.time() + 60  # 强制让书签看起来更新
    SIDECAR.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    wait_scan(force=True)
    time.sleep(0.4)
    row = api(f"/api/comics/{cid}")
    check("旧书签不覆盖更新状态", row.get("status") == "updating", str(row.get("status")))
    check("旧书签不覆盖喜爱", row.get("favorite") is True, str(row.get("favorite")))
    # 让数据库重新成为最新，后面的 UI 操作才不会被扫描拉回去
    api(f"/api/comics/{cid}", "PUT", {"status": "updating", "favorite": True})
    api("/api/sidecar/sync", "POST", {"force": True})

    print("4) 侧栏四项：名称 / 计数 / 筛选")
    proc = None
    try:
        proc, cdp = launch(1440, 900)
        cdp.cmd("Page.navigate", {"url": BASE + "/#"})
        wait_for(cdp, "document.querySelectorAll('.card').length > 0", 20)
        time.sleep(0.4)
        nav = cdp.js(NAV_JS)
        check("侧栏共四项", len(nav) == 4, json.dumps(nav, ensure_ascii=False))
        check("第二项叫未进行标记", nav[1]["text"] == "未进行标记", str(nav[1]))
        check("第三项叫更新中", nav[2]["text"] == "更新中", str(nav[2]))
        check("第四项叫 Favorite", nav[3]["text"] == "Favorite", str(nav[3]))
        check("未进行标记计数正确", nav[1]["count"] == str(base["untagged"]), str(nav[1]))
        check("更新中计数 +1", nav[2]["count"] == str(base["updating"] + 1), str(nav[2]))
        check("Favorite 计数 +1", nav[3]["count"] == str(base["favorite"] + 1), str(nav[3]))

        expect = {"__untagged__": base["untagged"],
                  "__updating__": base["updating"] + 1,
                  "__favorite__": base["favorite"] + 1}
        for tag, label in (("__untagged__", "未进行标记"),
                           ("__updating__", "更新中"), ("__favorite__", "Favorite")):
            cdp.js(f"document.querySelector('.nav-item[data-tag=\"{tag}\"]').click()")
            wait_for(cdp, f"document.querySelectorAll('.card').length === {expect[tag]}", 10)
            got = cdp.js("({title: document.getElementById('viewTitle').textContent,"
                         " cards: document.querySelectorAll('.card').length,"
                         " names: [...document.querySelectorAll('.card-title')].map(e => e.textContent)})")
            check(f"点「{label}」标题正确", got["title"] == label, str(got["title"]))
            check(f"「{label}」卡片数与计数一致", got["cards"] == expect[tag],
                  f"{got['cards']} != {expect[tag]}")
            check(f"「{label}」含临时作品", NAME[:-4] in got["names"], str(got["names"])[:200])

        print("5) 详情页：状态下拉仅编辑模式、爱心常驻")
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#comic={cid}"})
        wait_for(cdp, "document.getElementById('detail').hidden === false", 20)
        wait_for(cdp, "document.querySelectorAll('.page-cell').length > 0", 25)
        time.sleep(0.3)
        st = cdp.js(DETAIL_JS)
        check("只读态没有更新状态下拉", st["statusVisible"] is False, str(st))
        check("只读态爱心可见", st["favVisible"] is True, str(st))
        check("只读态爱心为按下", st["favPressed"] == "true", str(st))

        cdp.js("document.getElementById('detailEdit').click()")
        wait_for(cdp, "document.getElementById('detail').classList.contains('editing')", 8)
        time.sleep(0.2)
        st = cdp.js(DETAIL_JS)
        check("编辑态出现更新状态下拉", st["statusVisible"] is True, str(st))
        check("编辑态状态是可改的", st["statusDisabled"] is False, str(st))
        check("编辑态值是 updating", st["statusValue"] == "updating", str(st))
        check("编辑态爱心仍可见", st["favVisible"] is True, str(st))

        print("6) 在详情页切换状态与喜爱")
        cdp.js("document.getElementById('detailStatus').value = '';"
               "document.getElementById('detailStatus').dispatchEvent(new Event('change'))")
        check("状态已提交", wait(lambda: api(f"/api/comics/{cid}").get("status") == "", 8))
        cdp.js("document.getElementById('detailFav').click()")
        check("喜爱已取消", wait(lambda: api(f"/api/comics/{cid}").get("favorite") is False, 8))
        time.sleep(0.5)
        st = cdp.js(DETAIL_JS)
        check("爱心变未按下", st["favPressed"] == "false", str(st))
        nav = cdp.js(NAV_JS)
        check("更新中计数回到基线", nav[2]["count"] == str(base["updating"]), str(nav[2]))
        check("Favorite 计数回到基线", nav[3]["count"] == str(base["favorite"]), str(nav[3]))
        cdp.js("document.querySelector('.nav-item[data-tag=\"__favorite__\"]').click()")
        wait_for(cdp, f"document.querySelectorAll('.card').length === {base['favorite']}", 8)
        got = cdp.js("({cards: document.querySelectorAll('.card').length,"
                     " names: [...document.querySelectorAll('.card-title')].map(e => e.textContent),"
                     " empty: document.getElementById('empty').hidden ? '' :"
                     " document.getElementById('empty').textContent})")
        check("Favorite 里没有临时作品了", NAME[:-4] not in got["names"], str(got["names"])[:200])
        if base["favorite"] == 0:
            check("Favorite 空态文案", "标记为喜爱" in got["empty"], str(got["empty"]))

        errs = cdp.js("window.__errors || []")
        check("无 JS 报错", not errs, json.dumps(errs, ensure_ascii=False))
    finally:
        if proc:
            proc.terminate()

    api("/api/sidecar/sync", "POST", {"force": True})
    print("7) 清理")
    (LIB / NAME).unlink(missing_ok=True)
    SIDECAR.unlink(missing_ok=True)
    (LIB / (NAME + ".mangashelf.json")).unlink(missing_ok=True)
    wait_scan()
    check("临时作品已从书架清掉",
          all(i["id"] != cid for i in api("/api/comics")["items"]))

    print(f"\n通过 {passed} 项，失败 {failed} 项")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
