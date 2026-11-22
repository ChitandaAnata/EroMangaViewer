"""作品文件改名端到端验证。

覆盖：编辑模式里的文件名字段、后端原子改名（源文件 + 旁挂书签 + 数据库，id 不变）、
非法名 / 同名冲突拒绝且不破坏现场、改名后强制重扫不丢标签与进度、UI 即时同步。
跑完清理所有临时作品、旁挂文件和临时标签。
用法：D:/python/python.exe tools/test_rename_file.py
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CDP, CHROME, PORT, USER_DIR, wait_for, library_dir  # noqa: E402

LIB = library_dir()
ORIGINAL = "__rename_test__.cbz"
TAKEN = "__rename_taken__.cbz"
RENAMED = "__rename_new__.cbz"
UI_RENAMED = "__rename_ui__.cbz"
TAG = "mixed:__rename_test__"

ok = 0
bad = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  ✓ {label}")
    else:
        bad += 1
        print(f"  ✗ {label}  {extra}")


def api(path: str, method: str = "GET", body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"__status": e.code, **json.loads(e.read() or b"{}")}


def wait_scan(timeout: float = 120.0, force: bool = False) -> None:
    before = float(api("/api/scan/progress")["scan"].get("finished_at") or 0)
    api("/api/scan", "POST", {"force": force})
    end = time.time() + timeout
    while time.time() < end:
        snap = api("/api/scan/progress")["scan"]
        if snap["running"]:
            time.sleep(0.25)
            continue
        if float(snap.get("finished_at") or 0) > before:
            return
        time.sleep(0.2)


def make_archive(name: str) -> None:
    out = LIB / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for i in range(3):
            im = Image.new("RGB", (160, 240), (60 + i * 40, 80, 120))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70)
            z.writestr(f"{i + 1:03d}.jpg", buf.getvalue())


def find_id(prefix: str) -> str:
    for item in api("/api/comics")["items"]:
        if item["title"].startswith(prefix):
            return item["id"]
    raise SystemExit(f"找不到作品：{prefix}")


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


def sidecar_payload(name: str):
    p = LIB / (name + ".json")
    if not p.exists():
        p = LIB / (name + ".mangashelf.json")
    try:
        return p, json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return p, None


def main() -> None:
    for n in (ORIGINAL, TAKEN, RENAMED, UI_RENAMED):
        (LIB / n).unlink(missing_ok=True)
        (LIB / (n + ".json")).unlink(missing_ok=True)
        (LIB / (n + ".mangashelf.json")).unlink(missing_ok=True)
    make_archive(ORIGINAL)
    make_archive(TAKEN)
    wait_scan()

    cid = find_id("__rename_test__")
    taken_id = find_id("__rename_taken__")
    print(f"被测作品 id={cid}，占位作品 id={taken_id}")

    api("/api/tags", "POST", {"name": TAG, "display": "改名测试"})
    api(f"/api/comics/{cid}/tags", "PUT", {"tags": [{"name": TAG}]})
    api(f"/api/comics/{cid}", "PUT", {"category": "dojinshi", "lastPage": 1,
                                        "status": "updating", "favorite": True})
    api("/api/sidecar/sync", "POST", {"force": True})
    check("改名前旁挂文件已写出", wait_until(lambda: sidecar_payload(ORIGINAL)[1] is not None, 8))

    print("1) 后端：源文件名 / 数据库 / 旁挂书签一起改")
    res = api(f"/api/comics/{cid}/rename", "POST", {"filename": "__rename_new__"})
    check("改名接口成功", res.get("ok") is True, json.dumps(res, ensure_ascii=False))
    check("新路径写进数据库", res.get("comic", {}).get("path") == RENAMED,
          str(res.get("comic", {}).get("path")))
    check("新标题来自文件名", res.get("comic", {}).get("title") == "__rename_new__",
          str(res.get("comic", {}).get("title")))
    check("id 保持不变", api(f"/api/comics/{cid}").get("path") == RENAMED)
    check("磁盘上旧文件已消失", not (LIB / ORIGINAL).exists())
    check("磁盘上出现新文件", (LIB / RENAMED).is_file())
    check("旧旁挂文件已消失",
          not (LIB / (ORIGINAL + ".json")).exists() and not (LIB / (ORIGINAL + ".mangashelf.json")).exists())
    side_path, side = sidecar_payload(RENAMED)
    check("旁挂书签跟着改名", side is not None, str(side_path))
    check("旁挂书签标题更新", (side or {}).get("title") == "__rename_new__", str((side or {}).get("title")))
    check("旁挂书签里的标签没丢", any(t.get("name") == TAG for t in (side or {}).get("tags") or []),
          json.dumps(side, ensure_ascii=False)[:200])
    check("旁挂书签里的分类没丢", (side or {}).get("category") == "dojinshi",
          str((side or {}).get("category")))
    check("旁挂书签里的进度没丢", ((side or {}).get("progress") or {}).get("lastPage") == 1,
          str((side or {}).get("progress")))
    check("旁挂书签里的更新状态没丢", (side or {}).get("status") == "updating")
    check("旁挂书签里的喜爱没丢", (side or {}).get("favorite") is True)
    row = api(f"/api/comics/{cid}")
    check("数据库里的标签没丢", any(t.get("name") == TAG for t in row.get("tags") or []))
    check("数据库里的分类没丢", row.get("category") == "dojinshi")
    check("数据库里的进度没丢", row.get("lastPage") == 1)
    check("数据库里的更新状态没丢", row.get("status") == "updating")
    check("数据库里的喜爱没丢", row.get("favorite") is True)

    print("2) 重扫不复活旧文件 / 不丢数据")
    wait_scan(force=True)
    time.sleep(0.6)
    check("重扫后路径仍是新的", api(f"/api/comics/{cid}").get("path") == RENAMED,
          str(api(f"/api/comics/{cid}").get("path")))
    check("重扫后标签还在", any(t.get("name") == TAG for t in api(f"/api/comics/{cid}").get("tags") or []))
    check("重扫后旁挂文件还在", sidecar_payload(RENAMED)[1] is not None)
    check("重扫后更新状态还在", api(f"/api/comics/{cid}").get("status") == "updating")
    check("重扫后喜爱还在", api(f"/api/comics/{cid}").get("favorite") is True)

    print("3) 非法名 / 同名冲突：拒绝且不破坏现场")
    for bad_name in ("", "../escape", "a/b", "a\\b", "bad:name", "   "):
        r = api(f"/api/comics/{cid}/rename", "POST", {"filename": bad_name})
        check(f"拒绝非法名 {bad_name!r}", r.get("__status") == 400,
              json.dumps(r, ensure_ascii=False))
    check("非法名之后原文件还在", (LIB / RENAMED).is_file())
    r = api(f"/api/comics/{cid}/rename", "POST", {"filename": "__rename_taken__"})
    check("同名冲突被拒绝", r.get("__status") == 400, json.dumps(r, ensure_ascii=False))
    check("冲突后源文件还在", (LIB / RENAMED).is_file())
    check("冲突后占位文件没被覆盖", (LIB / TAKEN).is_file())
    check("冲突后数据库路径没变", api(f"/api/comics/{cid}").get("path") == RENAMED)
    r = api(f"/api/comics/{cid}/rename", "POST", {"filename": RENAMED})
    check("原名重复提交返回 unchanged", r.get("ok") is True and r.get("unchanged") is True,
          json.dumps(r, ensure_ascii=False))

    print("4) 页面：编辑模式才露出文件名字段")
    proc = None
    try:
        proc, cdp = launch(1440, 900)
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#comic={cid}"})
        wait_for(cdp, "document.getElementById('detail').hidden === false", 20)
        wait_for(cdp, "document.querySelectorAll('.page-cell').length > 0", 25)
        time.sleep(0.35)

        check("只读态没有文件名字段",
              cdp.js("document.getElementById('detailFileWrap').offsetParent === null") is True)
        cdp.js("document.getElementById('detailEdit').click()")
        wait_for(cdp, "document.getElementById('detail').classList.contains('editing')", 8)
        check("编辑态出现文件名字段",
              cdp.js("document.getElementById('detailFileWrap').offsetParent !== null") is True)
        state = cdp.js("""({
            name: document.getElementById('detailFilename').value,
            ext: document.getElementById('detailFileExt').textContent.trim(),
            crumb: document.getElementById('detailCrumb').textContent,
        })""")
        check("输入框只放主名", state["name"] == "__rename_new__", json.dumps(state, ensure_ascii=False))
        check("扩展名单独展示且不可编辑", state["ext"] == ".cbz", json.dumps(state, ensure_ascii=False))
        check("面包屑是新文件名", "__rename_new__.cbz" in state["crumb"], state["crumb"])

        print("5) 页面改名：即时同步详情页与书架")
        cdp.js("""(() => { const i = document.getElementById('detailFilename');
            i.value = '__rename_ui__'; i.focus();
            document.getElementById('detailRenameBtn').click(); })()""")
        check("页面改名写库", wait_until(
            lambda: api(f"/api/comics/{cid}").get("path") == UI_RENAMED, 10),
            str(api(f"/api/comics/{cid}").get("path")))
        check("磁盘上也改了", (LIB / UI_RENAMED).is_file() and not (LIB / RENAMED).exists())
        check("新旁挂文件在", sidecar_payload(UI_RENAMED)[1] is not None)
        check("输入框刷新成新名", wait_for(
            cdp, "document.getElementById('detailFilename').value === '__rename_ui__'", 8))
        check("面包屑同步新路径", "__rename_ui__.cbz" in cdp.js(
            "document.getElementById('detailCrumb').textContent"))
        check("书架卡片标题同步", wait_for(cdp,
              f"document.querySelector('.card[data-id=\"{cid}\"] .card-title')?.textContent === '__rename_ui__'", 8))
        check("提示已同步源文件与书签", "同步" in cdp.js("document.getElementById('toast').textContent"),
              cdp.js("document.getElementById('toast').textContent"))

        print("6) 页面错误提示")
        cdp.js("""(() => { document.getElementById('detailFilename').value = 'bad/name';
            document.getElementById('detailRenameBtn').click(); })()""")
        check("给出改名失败提示", wait_for(cdp,
              "document.getElementById('toast').textContent.includes('改名失败')", 8),
              cdp.js("document.getElementById('toast').textContent"))
        check("失败后路径不变", api(f"/api/comics/{cid}").get("path") == UI_RENAMED)
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
        print("7) 清理临时作品")
        for n in (RENAMED, UI_RENAMED, TAKEN):
            (LIB / n).unlink(missing_ok=True)
            (LIB / (n + ".json")).unlink(missing_ok=True)
            (LIB / (n + ".mangashelf.json")).unlink(missing_ok=True)
        api("/api/tags?name=" + urllib.parse.quote(TAG, safe=""), "DELETE")
        wait_scan()
        leftovers = [p.name for p in LIB.glob("__rename*")]
        check("临时文件清干净", not leftovers, json.dumps(leftovers, ensure_ascii=False))

    print(f"\n{ok} 项通过，{bad} 项失败")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
