"""端到端冒烟测试：书架、标签、筛选、进度、搜索、设置、错误处理。

自带一本临时样本书，跑完自动清理，不会碰库里已有的漫画。
用法：D:/python/python.exe tools/smoke_test.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_samples import make_page  # noqa: E402
from shot import library_dir  # noqa: E402

BASE = (os.environ.get("MANGASHELF_BASE") or "http://127.0.0.1:8765").rstrip("/")
ROOT = Path(__file__).resolve().parent.parent
LIB = library_dir()
TITLE = "__smoke_test__"
ARCHIVE = LIB / f"{TITLE}.cbz"


def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")
    if not cond:
        raise SystemExit(1)


def wait_scan(timeout=90):
    call("POST", "/api/scan", {"force": False})
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not call("GET", "/api/scan/progress")[1]["scan"]["running"]:
            return
        time.sleep(0.3)
    raise TimeoutError("扫描超时")


def make_archive():
    pages = [make_page("冒烟测试", i, 3, ((20, 30, 50), (90, 70, 130))) for i in range(3)]
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_STORED) as z:
        for i, data in enumerate(pages):
            z.writestr(f"{i + 1:03d}.jpg", data)


def cleanup():
    # 先删旁挂标签文件再删压缩包，否则扫描会把它当成孤儿保留在库里
    for _ in range(2):
        for suffix in (".json", ".mangashelf.json", ""):
            try:
                ARCHIVE.with_name(ARCHIVE.name + suffix).unlink(missing_ok=True)
            except OSError:
                pass
        wait_scan()  # 扫描可能在上一步的写旁挂竞态里又写回 json，扫完再删一遍
    for name in ("冒烟标签A", "冒烟标签B", "冒烟标签B改"):
        call("DELETE", "/api/tags?name=" + urllib.parse.quote(name))


def main():
    LIB.mkdir(parents=True, exist_ok=True)
    make_archive()
    wait_scan()

    try:
        print("1) 书架")
        status, lib = call("GET", "/api/comics")
        check("列表返回", status == 200 and lib["total"] >= 1, f"total={lib['total']}")
        items = lib["items"]
        check("标题完整", all(i["title"] for i in items))
        target = next((i for i in items if i["title"] == TITLE), None)
        check("临时样本书已入库", target is not None and target["pages"] == 3,
              f"pages={target['pages'] if target else '?'}")

        print("2) 标签")
        status, r = call("POST", "/api/tags", {"name": "冒烟标签A"})
        check("新建标签", status == 200, str(r.get("error", "")))
        status, r = call("POST", "/api/tags", {"name": "冒烟标签B"})
        check("再建一个", status == 200)
        status, r = call("PUT", f"/api/comics/{target['id']}/tags", {"tags": ["冒烟标签A", "冒烟标签B"]})
        check("给作品打标签", status == 200 and len(r["tags"]) == 2,
              str([t["name"] for t in r["tags"]]))

        print("3) 按标签筛选")
        status, r = call("GET", "/api/comics?tag=" + urllib.parse.quote("冒烟标签A"))
        check("筛选能命中", r["total"] == 1, f"total={r['total']}")
        status, r = call("GET", "/api/tags")
        counts = {t["name"]: t["count"] for t in r["tags"]}
        check("标签计数正确", counts.get("mixed:冒烟标签A") == 1 and counts.get("mixed:冒烟标签B") == 1, str(counts))

        print("4) 阅读进度")
        status, r = call("PUT", f"/api/comics/{target['id']}", {"lastPage": 2})
        check("保存进度", status == 200)
        status, r = call("GET", f"/api/comics/{target['id']}")
        check("进度已存", r["lastPage"] == 2, f"lastPage={r['lastPage']}")

        print("5) 搜索")
        status, r = call("GET", "/api/comics?q=" + urllib.parse.quote("__smoke"))
        check("按文件名搜索", r["total"] == 1, f"total={r['total']}")

        print("6) 标签重命名与删除")
        status, r = call("PUT", "/api/tags?name=" + urllib.parse.quote("冒烟标签B"), {"name": "冒烟标签B改"})
        check("重命名标签", status == 200 and any(t["name"] == "mixed:冒烟标签B改" for t in r["tags"]))
        status, r = call("DELETE", "/api/tags?name=" + urllib.parse.quote("冒烟标签B改"))
        check("删除标签", status == 200 and not any(t["name"] == "mixed:冒烟标签B改" for t in r["tags"]))
        check("作品标签一并清理",
              all(t["name"] != "mixed:冒烟标签B改" for t in call("GET", f"/api/comics/{target['id']}")[1]["tags"]))

        print("7) 设置")
        status, r = call("GET", "/api/settings")
        original_path = r["settings"]["library_path"]
        check("读取设置", status == 200 and Path(original_path) == LIB.resolve(), original_path)
        status, r = call("PUT", "/api/settings", {"library_path": original_path})
        check("保存相同路径", status == 200 and r["settings"]["library_path"] == original_path)
        status, r = call("PUT", "/api/settings", {"library_path": "relative/path"})
        check("拒绝相对路径", status == 400, str(r.get("error", "")))

        print("8) 错误处理")
        status, r = call("GET", "/api/comics/deadbeefdeadbeef")
        check("未知 ID 返回 404", status == 404, str(r.get("error")))
        status, r = call("GET", "/api/comics/../../etc/passwd")
        check("非法 ID 被拒绝", status in (400, 404))

        print("9) 页面预览")
        status, r = call("GET", f"/api/comics/{target['id']}")
        page = urllib.request.urlopen(f"{BASE}/api/comics/{target['id']}/page/0")
        check("整页图片可读", status == 200 and page.headers.get("Content-Type", "").startswith("image/"))
        thumb = urllib.request.urlopen(f"{BASE}/api/comics/{target['id']}/thumb/0")
        check("预览缩略图可读", thumb.headers.get("Content-Type") == "image/jpeg")
        status, r = call("GET", f"/api/comics/{target['id']}/thumb/9")
        check("预览页码越界返回 404", status == 404)
    finally:
        cleanup()

    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
