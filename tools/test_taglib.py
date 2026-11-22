"""库级标签目录验证：库根 mangashelf-tags.json 的导出、换设备导入、冲突与容错。

用法：D:/python/python.exe tools/test_taglib.py

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

BASE = Path(tempfile.mkdtemp(prefix="mangashelf-taglib-"))
LIB = BASE / "library"
LIB.mkdir(parents=True, exist_ok=True)

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


def use_temp_env() -> None:
    config.DATA_DIR = BASE / "data"
    config.DB_PATH = config.DATA_DIR / "library.db"
    config.COVER_DIR = config.DATA_DIR / "covers"
    config.PAGE_DIR = config.DATA_DIR / "pages"
    config.CONFIG_PATH = config.DATA_DIR / "config.json"
    config.WEB_DIR = ROOT / "web"
    config._cache = dict(config.DEFAULT_CONFIG, library_path=str(LIB), sidecar=True)


def reset_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(config.DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    db.init_db()


def make_archive(name: str, pages: int = 3) -> Path:
    p = LIB / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        for i in range(pages):
            im = Image.new("RGB", (200, 280), (35, 35, 60))
            ImageDraw.Draw(im).text((16, 16), f"PAGE {i + 1}", fill=(250, 250, 255))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            z.writestr(f"{i + 1:03d}.jpg", buf.getvalue())
    return p


def scan(force: bool = False) -> dict:
    scanner._run(force=force)
    return scanner.STATE.snapshot()


def catalog() -> dict:
    return json.loads((LIB / "mangashelf-tags.json").read_text(encoding="utf-8"))


def names_in_file() -> list:
    return sorted(t["name"] for t in catalog()["tags"])


def main() -> None:
    use_temp_env()
    config.ensure_dirs()
    db.init_db()
    make_archive("A/a.cbz")
    scan(force=True)

    print("1) 提前建的空标签也会写进库根的目录文件")
    db.create_tag("language:chinese", "#ff5f7e", "")          # 用作品时才有，但先建
    db.create_tag("artist:naoto", "#a06bff", "moni")          # 完全没用上的空标签
    db.create_tag("parody:kancolle", "#ff9f43", "舰队collection")
    taglib.export_all()  # 真实场景由 API 层在增删改后调用
    check("目录文件已生成", (LIB / "mangashelf-tags.json").is_file())
    check("文件里包含空标签", names_in_file() == ["artist:naoto", "language:chinese", "parody:kancolle"], names_in_file())
    check("自定义显示名写进文件",
          next(t for t in catalog()["tags"] if t["name"] == "parody:kancolle")["display"] == "舰队collection")
    check("颜色写进文件",
          next(t for t in catalog()["tags"] if t["name"] == "artist:naoto")["color"] == "#a06bff")
    rev1 = catalog()["revision"]
    check("重复导出不重复落盘", taglib.export_all() is False)

    print("2) 换设备：数据库清空后扫描，标签目录（含空标签、颜色、显示名）自动回来")
    reset_db()
    snap = scan(force=True)
    tags = {t["name"]: t for t in db.list_tags()}
    check("三个标签全部恢复", sorted(tags) == ["artist:naoto", "language:chinese", "parody:kancolle"], sorted(tags))
    check("空标签也回来了（数量为 0）", tags.get("artist:naoto", {}).get("count") == 0, tags.get("artist:naoto"))
    check("颜色保持不变", tags.get("artist:naoto", {}).get("color") == "#a06bff", tags.get("artist:naoto"))
    check("自定义显示名保持不变", tags.get("parody:kancolle", {}).get("label") == "舰队collection")
    check("内置词典仍然生效", tags.get("language:chinese", {}).get("label") == "汉语")
    check("扫描没有失败项", snap["failed"] == 0, snap["failed"])

    print("3) 每本作品按标签名自动对上（与数据库自增 id 无关）")
    cid = db.all_paths()["A/a.cbz"]["id"]
    db.set_comic_tags(cid, ["language:chinese", "parody:kancolle"])
    sidecar.write_for(cid)  # 真实场景由 API 层延迟写出
    reset_db()
    scan(force=True)
    cid = db.all_paths()["A/a.cbz"]["id"]
    labels = sorted(t["label"] for t in (db.get_comic(cid) or {}).get("tags", []))
    check("作品的标签恢复了", labels == ["汉语", "舰队collection"], labels)
    check("颜色沿用目录里的", next(t for t in db.get_comic(cid)["tags"] if t["name"] == "parody:kancolle")["color"] == "#ff9f43")

    print("4) 改颜色 / 改显示名 / 删除，都会同步到目录文件")
    db.update_tag("artist:naoto", color="#4ecdc4", display="naoto 老师")
    taglib.export_all()
    row = next(t for t in catalog()["tags"] if t["name"] == "artist:naoto")
    check("改色已写入", row["color"] == "#4ecdc4", row)
    check("改显示名已写入", row["display"] == "naoto 老师", row)
    check("revision 递增", catalog()["revision"] > rev1, (catalog()["revision"], rev1))

    db.delete_tag("parody:kancolle")
    taglib.export_all()
    check("删除的标签不再出现在文件里", "parody:kancolle" not in names_in_file(), names_in_file())

    print("5) 目录文件比本地新时，以文件为准（另一台设备改过）")
    cat = catalog()
    cat["revision"] = cat["revision"] + 5
    cat["tags"].append({"name": "female:眼镜", "display": "", "color": "#f368e0"})
    (LIB / "mangashelf-tags.json").write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
    result = taglib.merge()
    check("merge 报告 imported", result == "imported", result)
    tags = {t["name"]: t for t in db.list_tags()}
    check("新标签被导入", "female:眼镜" in tags, sorted(tags))
    check("本地删掉的旧标签没被文件带回", "parody:kancolle" not in tags, sorted(tags))
    check("revision 与文件对齐", db.tags_revision() == cat["revision"], (db.tags_revision(), cat["revision"]))

    print("6) 本地比文件新时，反向写回文件")
    db.create_tag("male:大叔", "#5b8def")
    result = taglib.merge()
    check("merge 报告 exported", result == "exported", result)
    check("本地新增的标签写进文件", "male:大叔" in names_in_file(), names_in_file())

    print("7) 扫描时从作品旁挂文件冒出的新标签，会补进目录")
    (LIB / "A" / "a.cbz.json").write_text(json.dumps({
        "app": "MangaShelf", "schema": 1, "title": "A",
        "fingerprint": {"size": (LIB / "A/a.cbz").stat().st_size, "hash": ""},
        "tags": [{"name": "character:テスト", "display": "", "color": ""}],
        "progress": {"lastPage": 1, "pages": 3},
        "updatedAt": 4102444800,
    }, ensure_ascii=False), encoding="utf-8")
    scan(force=True)
    check("旁挂文件里的新标签进了目录", "character:テスト" in names_in_file(), names_in_file())

    print("8) 损坏的目录文件不影响使用")
    (LIB / "mangashelf-tags.json").write_text("{ broken json", encoding="utf-8")
    before = sorted(t["name"] for t in db.list_tags())
    snap = scan(force=True)
    check("扫描没有失败项", snap["failed"] == 0, snap["failed"])
    check("本地标签没被破坏", sorted(t["name"] for t in db.list_tags()) == before)
    check("坏文件被重新写成正常格式", catalog().get("kind") == "tag-catalog")

    print("9) 不是本程序的文件不碰")
    foreign = LIB / "mangashelf-tags.json"
    foreign.write_text('{"app": "OtherApp", "tags": []}', encoding="utf-8")
    check("识别为别人的文件", taglib.read() is None)
    taglib.export_all(force=True)
    check("别人的文件被覆盖前先确认格式…（本程序独占该文件名）",
          catalog().get("app") == "MangaShelf")

    print("10) 空库不凭空造文件")
    empty = BASE / "empty"
    empty.mkdir()
    config._cache = dict(config.DEFAULT_CONFIG, library_path=str(empty), sidecar=True)
    reset_db()
    check("空库没有生成目录文件", taglib.export_all() is False and not (empty / "mangashelf-tags.json").exists())
    check("status 报告未生成", taglib.status()["exists"] is False)

    print(f"\n结果：{passed} 通过，{failed} 失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
