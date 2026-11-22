"""标签目录被删掉 / 用户主动清空标签的行为验证。

用法：D:/python/python.exe tools/test_tag_reset.py

全部在临时目录里跑，不接触真实漫画库和真实数据库。
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from server import config, db, scanner, sidecar, taglib  # noqa: E402

BASE = Path(tempfile.mkdtemp(prefix="mangashelf-reset-"))

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


def use_lib(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR = BASE / "data"
    config.DB_PATH = config.DATA_DIR / "library.db"
    config.COVER_DIR = config.DATA_DIR / "covers"
    config.PAGE_DIR = config.DATA_DIR / "pages"
    config.CONFIG_PATH = config.DATA_DIR / "config.json"
    config.WEB_DIR = ROOT / "web"
    config._cache = dict(config.DEFAULT_CONFIG, library_path=str(path), sidecar=True)
    config.ensure_dirs()


def reset_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(config.DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    db.init_db()


def make_archive(lib: Path, name: str, pages: int = 3) -> Path:
    p = lib / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        for i in range(pages):
            im = Image.new("RGB", (200, 280), (35, 35, 60))
            ImageDraw.Draw(im).text((16, 16), f"PAGE {i + 1}", fill=(250, 250, 255))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            z.writestr(f"{i + 1:03d}.jpg", buf.getvalue())
    return p


def scan(force: bool = True) -> dict:
    scanner._run(force=force)
    return scanner.STATE.snapshot()


def cat_path(lib: Path) -> Path:
    return lib / "mangashelf-tags.json"


def main() -> None:
    # ---------------------------------------------------------------- 1
    print("1) 空库 + 删掉目录文件 = 恢复出厂（标签一起清掉，不再重建）")
    lib1 = BASE / "empty"
    use_lib(lib1)
    reset_db()
    db.create_tag("language:chinese", "#ff5f7e")
    db.create_tag("parody:kancolle", "#ff9f43", "舰队collection")
    taglib.export_all()
    check("先有目录文件", cat_path(lib1).is_file())
    cat_path(lib1).unlink()
    result = taglib.ensure_catalog()
    check("刷新时识别为清空", result == "cleared", result)
    check("数据库里的标签已清空", db.list_tags() == [], [t["name"] for t in db.list_tags()])
    check("不会再把文件生成回来", not cat_path(lib1).exists())
    check("revision 归零", db.tags_revision() == 0, db.tags_revision())

    # ---------------------------------------------------------------- 2
    print("2) 有作品时删掉目录文件 = 误删，从数据库重建，标签不丢")
    lib2 = BASE / "withcomic"
    use_lib(lib2)
    reset_db()
    make_archive(lib2, "B/b.cbz")
    scan()
    db.create_tag("language:chinese", "#ff5f7e")
    db.create_tag("artist:naoto", "#a06bff", "moni")
    taglib.export_all()
    cat_path(lib2).unlink()
    result = taglib.ensure_catalog()
    check("有作品时报告重建", result == "exported", result)
    check("目录文件被重建", cat_path(lib2).is_file())
    check("标签一个都没少", sorted(t["name"] for t in db.list_tags()) == ["artist:naoto", "language:chinese"])
    check("显示名还在", next(t for t in db.list_tags() if t["name"] == "artist:naoto")["display"] == "moni")

    # ---------------------------------------------------------------- 3
    print("3) 库里只剩旁挂文件（作品被移走）时也不清空")
    lib3 = BASE / "orphan"
    use_lib(lib3)
    reset_db()
    db.create_tag("language:chinese", "#ff5f7e")
    img = io.BytesIO()
    Image.new("RGB", (120, 160), (20, 20, 40)).save(img, "JPEG")
    (lib3 / "gone.cbz.json").write_text(json.dumps({
        "app": "MangaShelf", "schema": 1, "title": "gone",
        "fingerprint": {"size": 1234, "hash": "abc"},
        "tags": [{"name": "language:chinese", "display": "", "color": "#ff5f7e"}],
        "progress": {"lastPage": 2, "pages": 10},
        "updatedAt": 1763388800,
    }, ensure_ascii=False), encoding="utf-8")
    result = taglib.ensure_catalog()
    check("有旁挂文件时不清空", result == "exported", result)
    check("标签仍在", [t["name"] for t in db.list_tags()] == ["language:chinese"])

    # ---------------------------------------------------------------- 4
    print("4) 主动清空：数据库、每本旁的 json、库根目录文件一起清")
    lib4 = BASE / "clearall"
    use_lib(lib4)
    reset_db()
    make_archive(lib4, "C/c.cbz", pages=4)
    scan()
    cid = db.all_paths()["C/c.cbz"]["id"]
    db.create_tag("language:chinese", "#ff5f7e")
    db.create_tag("circle:テスト", "#4ecdc4")
    db.set_comic_tags(cid, ["language:chinese", "circle:テスト"])
    db.set_last_page(cid, 3)
    sidecar.write_for(cid, force=True)
    taglib.export_all()
    side_file = lib4 / "C" / "c.cbz.json"
    before = json.loads(side_file.read_text(encoding="utf-8"))
    check("清空前：旁挂文件有 2 个标签", len(before["tags"]) == 2, before["tags"])

    res = taglib.clear_all()
    check("报告清掉 2 个标签", res["tags"] == 2, res)
    check("数据库标签已清空", db.list_tags() == [])
    check("目录文件已删除", not cat_path(lib4).exists())
    after = json.loads(side_file.read_text(encoding="utf-8"))
    check("旁挂文件里的标签被清空", after["tags"] == [], after["tags"])
    check("旁挂文件保留阅读进度", after["progress"]["lastPage"] == 3, after["progress"])
    check("作品记录还在", db.get_comic(cid) is not None)

    scan()
    check("重新扫描不会把标签带回来", db.list_tags() == [], [t["name"] for t in db.list_tags()])
    check("作品的标签仍为空", db.get_comic(cid)["tags"] == [])
    check("阅读进度没丢", db.get_comic(cid)["last_page"] == 3, db.get_comic(cid)["last_page"])
    check("清空后不会凭空生成目录文件", not cat_path(lib4).exists())

    # ---------------------------------------------------------------- 5
    print("5) 清空后重新建标签，目录文件照常生成")
    db.create_tag("male:大叔", "#5b8def")
    taglib.export_all()
    check("文件重新出现", cat_path(lib4).is_file())
    data = json.loads(cat_path(lib4).read_text(encoding="utf-8"))
    check("只有一个标签", [t["name"] for t in data["tags"]] == ["male:大叔"], data["tags"])

    print(f"\n结果：{passed} 通过，{failed} 失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
