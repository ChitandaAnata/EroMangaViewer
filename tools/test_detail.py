"""详情页端到端验证：点击流、分页、阅读器跳转、移动端布局。

用法：D:/python/python.exe tools/test_detail.py
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot import BASE, CDP, CHROME, PORT, USER_DIR, library_dir  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = library_dir()
TEST_NAME = "__pagination_test__.cbz"

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


def wait_scan(timeout: float = 120.0) -> None:
    """触发一次扫描并等它真正结束。

    不能只看 running：扫描线程启动有延迟，POST 之后立刻查询往往还是
    running=False，会提前返回，导致后面找不到刚放进去的样本。
    """
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


def make_test_archive() -> None:
    out = LIB / TEST_NAME
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for i in range(60):
            im = Image.new("RGB", (600, 900), (30 + i * 2, 34, 60 + i))
            d = ImageDraw.Draw(im)
            d.rectangle([40, 40, 560, 860], outline=(220, 220, 230), width=4)
            d.text((60, 60), f"PAGE {i + 1}", fill=(240, 240, 250))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            z.writestr(f"{i + 1:03d}.jpg", buf.getvalue())
    print(f"已生成测试漫画：{TEST_NAME}（60 页）")


def find_id(title_prefix: str) -> str:
    for item in api("/api/comics")["items"]:
        if item["title"].startswith(title_prefix):
            return item["id"]
    raise SystemExit(f"找不到作品：{title_prefix}")


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


def key(cdp, name: str, code: int) -> None:
    base = {"key": name, "code": name, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
    cdp.cmd("Input.dispatchKeyEvent", {**base, "type": "keyDown"})
    cdp.cmd("Input.dispatchKeyEvent", {**base, "type": "keyUp"})


def main() -> None:
    make_test_archive()
    wait_scan()
    # 所有点击流都跑在自己的临时样本书上，避免改动库里真实作品的阅读进度
    first_id = find_id("__pagination_test__")
    first_pages = api(f"/api/comics/{first_id}")["pages"]
    test_id = first_id
    print(f"测试作品 id={first_id}（{first_pages} 页）")

    proc, cdp = launch(1440, 900)
    try:
        cdp.cmd("Page.navigate", {"url": BASE + "/"})
        wait(cdp, "document.querySelectorAll('.card').length >= 2", 25)

        print("1) 书架点击进入详情")
        cdp.js(f"document.querySelector('.card[data-id=\"{first_id}\"]').click()")
        wait(cdp, "!document.getElementById('detail').hidden", 15)
        wait(cdp, "document.querySelectorAll('.page-cell img').length > 0", 20)
        state = cdp.js("""({
            hash: location.hash,
            detail: !document.getElementById('detail').hidden,
            shelf: document.getElementById('shelfView').hidden,
            title: document.getElementById('detailTitle').textContent,
            meta: !!document.getElementById('detailMeta'),
            tags: document.querySelectorAll('#detailTags .d-tag').length,
            pages: document.getElementById('pagesCount').textContent,
        })""")
        check("未直接进阅读器", cdp.js("document.getElementById('reader').hidden") is True)
        check("路由为 #comic=", state["hash"].startswith("#comic="), state["hash"])
        check("详情页显示、书架隐藏", state["detail"] and state["shelf"], json.dumps(state, ensure_ascii=False))
        check("标题非空", bool(state["title"].strip()))
        check("旧的元信息表格已移除", state["meta"] is False)
        # 详情页默认只读：编辑入口只在打开右上角「编辑模式」后出现
        check("只读态没有标签编辑入口",
              cdp.js("document.querySelectorAll('#detailTags .d-tag-add').length") == 0,
              str(cdp.js("document.querySelectorAll('#detailTags .d-tag-add').length")))
        cdp.js("document.getElementById('detailEdit').click()")
        wait(cdp, "document.getElementById('detail').classList.contains('editing')", 8)
        check("开编辑模式后出现标签编辑入口",
              cdp.js("document.querySelectorAll('#detailTags .d-tag-add').length") == 1,
              str(cdp.js("document.querySelectorAll('#detailTags .d-tag-add').length")))
        cdp.js("document.getElementById('detailEdit').click()")
        wait(cdp, "!document.getElementById('detail').classList.contains('editing')", 8)

        print("2) 点击预览页跳到阅读器对应页")
        cdp.js("document.querySelectorAll('.page-cell')[4].click()")
        wait(cdp, "!document.getElementById('reader').hidden", 15)
        wait(cdp, "document.getElementById('rImg').complete && document.getElementById('rImg').naturalWidth > 0", 20)
        check("阅读器打开", cdp.js("!document.getElementById('reader').hidden") is True)
        check("从第 5 页开始", cdp.js("document.getElementById('rPageLabel').textContent") == f"5 / {first_pages}",
              cdp.js("document.getElementById('rPageLabel').textContent"))
        check("返回按钮显示“详情”", cdp.js("document.querySelector('#rBack span').textContent") == "详情")

        print("3) 关闭阅读器回到详情")
        key(cdp, "Escape", 27)
        wait(cdp, "document.getElementById('reader').hidden", 10)
        check("阅读器已关闭", cdp.js("document.getElementById('reader').hidden") is True)
        check("回到详情页", cdp.js("!document.getElementById('detail').hidden") is True)
        wait(cdp, "document.getElementById('detailProgressText').textContent.includes('第 5')", 10)
        check("详情页进度已更新",
              "第 5" in (cdp.js("document.getElementById('detailProgressText').textContent") or ""),
              cdp.js("document.getElementById('detailProgressText').textContent"))

        print("4) 返回书架")
        cdp.js("document.getElementById('detailBack').click()")
        wait(cdp, "document.getElementById('detail').hidden", 10)
        check("详情隐藏、书架恢复",
              cdp.js("document.getElementById('detail').hidden && !document.getElementById('shelfView').hidden") is True)
        check("hash 已清空", cdp.js("location.hash") in ("", "#"), cdp.js("location.hash"))

        print("5) 分页预览（60 页作品）")
        cdp.cmd("Page.navigate", {"url": f"{BASE}/#comic={test_id}"})
        wait(cdp, "!document.getElementById('detail').hidden", 20)
        wait(cdp, "document.querySelectorAll('.page-cell img').length === 24", 25)
        check("首页显示 1–24 页", cdp.js("document.getElementById('pagesCount').textContent").startswith("第 1–24 页"),
              cdp.js("document.getElementById('pagesCount').textContent"))
        check("生成 3 个页码 + 前后翻页", cdp.js("document.querySelectorAll('#pagesPager .pg-btn').length") == 5,
              str(cdp.js("document.querySelectorAll('#pagesPager .pg-btn').length")))
        cdp.js("document.querySelector('#pagesPager .pg-btn.active').nextElementSibling.click()")
        wait(cdp, "document.getElementById('pagesCount').textContent.startsWith('第 25')", 10)
        check("翻到 25–48 页", cdp.js("document.getElementById('pagesCount').textContent").startswith("第 25–48 页"),
              cdp.js("document.getElementById('pagesCount').textContent"))
        wait(cdp, "document.querySelectorAll('.page-cell img').length === 24", 20)

        print("6) 详情页管理标签")
        api("/api/tags", "POST", {"name": "测试标签"})
        cdp.js("document.getElementById('detailEdit').click()")
        cdp.js("document.getElementById('dAddTag').click()")
        check("标签弹窗打开", wait(cdp, "!document.getElementById('tagModal').hidden", 8))
        check("新建的标签出现在下拉里", wait(cdp,
              "[...document.querySelectorAll('#tagSelects select')]"
              ".some(s => [...s.options].some(o => o.value === 'mixed:测试标签'))", 8))
        cdp.js("""(() => {
            const s = [...document.querySelectorAll('#tagSelects select')]
              .find(x => [...x.options].some(o => o.value === 'mixed:测试标签'));
            s.value = 'mixed:测试标签';
            s.dispatchEvent(new Event('change', { bubbles: true }));
        })()""")
        time.sleep(0.2)
        cdp.js("document.getElementById('tagSaveBtn').click()")
        wait(cdp, "document.getElementById('tagModal').hidden", 10)
        wait(cdp, "[...document.querySelectorAll('#detailTags .d-tag')].some(e => e.textContent.includes('测试标签'))", 12)
        chips = cdp.js("[...document.querySelectorAll('#detailTags .d-tag')].map(e => e.textContent.trim())")
        check("新标签出现在详情页", any("测试标签" in c for c in chips), json.dumps(chips, ensure_ascii=False))

        print("7) 移动端布局（390px）")
        cdp.cmd("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        time.sleep(0.4)
        m = cdp.js("""({
            overflowX: document.documentElement.scrollWidth - window.innerWidth,
            cols: getComputedStyle(document.getElementById('pageGrid')).gridTemplateColumns.split(' ').length,
            cover: document.getElementById('detailCover').getBoundingClientRect().width,
            tagRows: document.querySelectorAll('#detailTags .tag-line').length,
            favReadSameLine: (() => {
                const f = document.getElementById('detailFav').getBoundingClientRect();
                const r = document.getElementById('detailRead').getBoundingClientRect();
                return Math.abs(f.top - r.top) < 6 && r.left > f.left;
            })(),
            readWide: document.getElementById('detailRead').getBoundingClientRect().width > 180,
            pagerVisible: document.getElementById('pagesPager').getBoundingClientRect().height > 0,
        })""")
        check("无横向溢出", m["overflowX"] <= 1, str(m["overflowX"]))
        check("预览为 3 列", m["cols"] == 3, str(m["cols"]))
        check("封面宽度自适应", 140 <= m["cover"] <= 180, str(m["cover"]))
        check("标签按分类成行", m["tagRows"] >= 1, str(m["tagRows"]))
        check("爱心在开始阅读左边同一行", m["favReadSameLine"] is True, str(m))
        check("主按钮占满剩余宽度", m["readWide"] is True, str(m))

        errs = cdp.js("window.__errors || []")
        check("无 JS 报错", not errs, json.dumps(errs, ensure_ascii=False))
        cdp.ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        try:
            # 先删标签文件再删压缩包，否则扫描会把旁挂文件当成孤儿留在库里
            for suffix in ("", ".json", ".mangashelf.json"):
                (LIB / (TEST_NAME + suffix)).unlink(missing_ok=True)
        except OSError:
            pass
        wait_scan()
        try:
            api("/api/tags?name=" + urllib.parse.quote("mixed:测试标签"), "DELETE")
        except Exception:
            pass

    print(f"\n结果：{passed} 通过，{failed} 失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
