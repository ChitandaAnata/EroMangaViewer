"""验证：文件重命名后标签/进度保留 + 新增文件在扫描后入库。

自带临时样本书，只操作自己的文件，跑完自动清理。
用法：D:/python/python.exe tools/test_watch.py
"""
import json
import os
import sys
import time
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

OLD = LIB / "__watch_test__.cbz"
NEW = LIB / "__watch_test_renamed__.cbz"
COPY = LIB / "__watch_new__.cbz"


def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_scan(timeout=120):
    # 等的是「这一轮扫描跑完」，不能只看 running=False —— 提交后第一次轮询
    # 可能正好落在扫描线程起来之前，会把上一轮的 finished_at 当成结果。
    before = float(call("GET", "/api/scan/progress")["scan"].get("finished_at") or 0)
    call("POST", "/api/scan", {"force": False})
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = call("GET", "/api/scan/progress")["scan"]
        if s["running"]:
            time.sleep(0.4)
            continue
        if float(s.get("finished_at") or 0) > before:
            return s
        time.sleep(0.3)
    raise TimeoutError("扫描超时")


def find(title):
    for c in call("GET", "/api/comics")["items"]:
        if c["title"] == title:
            return c
    return None


def make_archive():
    pages = [make_page("重命名测试", i, 5, ((24, 26, 46), (110, 96, 150))) for i in range(5)]
    with zipfile.ZipFile(OLD, "w", zipfile.ZIP_STORED) as z:
        for i, data in enumerate(pages):
            z.writestr(f"{i + 1:03d}.jpg", data)


def drop(p):
    """连同旁挂标签文件一起删（先删标签文件，否则扫描会把它当孤儿留着）。"""
    for suffix in (".json", ".mangashelf.json", ""):
        try:
            p.with_name(p.name + suffix).unlink(missing_ok=True)
        except OSError:
            pass


def cleanup():
    for p in (OLD, NEW, COPY):
        drop(p)
    wait_scan()
    for name in ("重命名测试", "收藏"):
        try:
            call("DELETE", "/api/tags?name=" + urllib.parse.quote(name))
        except Exception:
            pass


def main():
    cleanup_files = (OLD, NEW, COPY)
    for p in cleanup_files:
        drop(p)
    make_archive()
    wait_scan()

    try:
        print("准备：给临时样本书打标签并记录进度")
        comic = find("__watch_test__")
        assert comic, "临时样本书没有入库"
        call("PUT", f"/api/comics/{comic['id']}/tags", {"tags": ["重命名测试", "收藏"]})
        call("PUT", f"/api/comics/{comic['id']}", {"lastPage": 3})
        before = call("GET", f"/api/comics/{comic['id']}")
        print(f"  标签={[t['name'] for t in before['tags']]} 进度={before['lastPage']} 页数={before['pages']}")

        print("\n步骤 1：重命名压缩包")
        OLD.rename(NEW)
        wait_scan()
        after = find("__watch_test_renamed__")
        assert after, "重命名后的作品没有出现"
        print(f"  新记录标签={[t['name'] for t in after['tags']]} 进度={after['lastPage']} 页数={after['pages']}")
        assert sorted(t["name"] for t in after["tags"]) == sorted(["mixed:重命名测试", "mixed:收藏"]), "标签丢失！"
        assert after["lastPage"] == 3, "阅读进度丢失！"
        print("  ✓ 标签与进度已随文件迁移")

        print("\n步骤 2：把文件改回原名")
        NEW.rename(OLD)
        wait_scan()
        back = find("__watch_test__")
        assert back and sorted(t["name"] for t in back["tags"]) == sorted(["mixed:重命名测试", "mixed:收藏"])
        assert back["lastPage"] == 3
        print("  ✓ 改回原名后标签与进度依然保留")

        print("\n步骤 3：新增一本漫画")
        COPY.write_bytes(OLD.read_bytes())
        wait_scan()
        added = find("__watch_new__")
        assert added, "新文件没有被扫描到"
        print(f"  ✓ 新作品已入库：{added['title']} / {added['pages']} 页 / 封面={added['cover']}")
        COPY.unlink()
        wait_scan()
        assert find("__watch_new__") is None, "删除后记录未清理"
        print("  ✓ 删除文件后记录已自动清理")
    finally:
        cleanup()

    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
