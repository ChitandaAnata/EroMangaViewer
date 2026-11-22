"""旁挂标签文件验证：写入、换设备导入、改名认领、外部同名 json 保护。

用法：D:/python/python.exe tools/test_sidecar.py

全部在临时目录里跑，不接触真实漫画库和真实数据库。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from server import config, db, scanner, sidecar  # noqa: E402

BASE = Path(tempfile.mkdtemp(prefix="mangashelf-sidecar-"))
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


def use_temp_env(sidecar_on: bool = True) -> None:
    config.DATA_DIR = BASE / "data"
    config.DB_PATH = config.DATA_DIR / "library.db"
    config.COVER_DIR = config.DATA_DIR / "covers"
    config.PAGE_DIR = config.DATA_DIR / "pages"
    config.CONFIG_PATH = config.DATA_DIR / "config.json"
    config.WEB_DIR = ROOT / "web"
    config._cache = dict(config.DEFAULT_CONFIG, library_path=str(LIB), sidecar=sidecar_on)


def make_archive(name: str, pages: int = 3, color=(30, 30, 50)) -> Path:
    p = LIB / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        for i in range(pages):
            im = Image.new("RGB", (240, 340), color)
            ImageDraw.Draw(im).text((20, 20), f"PAGE {i + 1}", fill=(250, 250, 255))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            z.writestr(f"{i + 1:03d}.jpg", buf.getvalue())
    return p


def reset_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(config.DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    db.init_db()


def scan(force: bool = False) -> dict:
    scanner._run(force=force)
    return scanner.STATE.snapshot()


def cid_of(rel: str) -> str:
    info = db.all_paths().get(rel)
    if not info:
        raise SystemExit(f"库里找不到 {rel}，现有：{list(db.all_paths())}")
    return info["id"]


def tag_names(cid: str):
    return sorted(t["name"] for t in (db.get_comic(cid) or {}).get("tags", []))


def main() -> None:
    use_temp_env()
    config.ensure_dirs()
    db.init_db()

    make_archive("A/a.cbz")
    make_archive("B/b.cbz", color=(60, 30, 30))
    make_archive("C/c.cbz", color=(30, 60, 30))
    scan(force=True)

    print("1) 打标签/看进度后自动写出旁挂文件")
    cid_a = cid_of("A/a.cbz")
    db.set_comic_tags(cid_a, [
        {"name": "language:chinese"},
        {"name": "parody:kancolle", "display": "舰队collection"},
    ])
    db.set_last_page(cid_a, 2)
    check("write_for 写出成功", sidecar.write_for(cid_a) is True)
    sp = LIB / "A" / "a.cbz.json"
    check("生成 A/a.cbz.json", sp.is_file())
    data = json.loads(sp.read_text(encoding="utf-8"))
    check("文件里是分类标签", [t["name"] for t in data["tags"]] == ["language:chinese", "parody:kancolle"], data.get("tags"))
    check("文件里有阅读进度", (data.get("progress") or {}).get("lastPage") == 2, data.get("progress"))
    fp = data.get("fingerprint") or {}
    check("文件里有指纹", fp.get("size") == (LIB / "A" / "a.cbz").stat().st_size and len(fp.get("hash") or "") == 16, fp)
    check("内容是幂等的（不重复落盘）", sidecar.write_for(cid_a) is False)
    check("没有标签的作品不生成空文件", not (LIB / "B" / "b.cbz.json").exists())

    print("2) 换设备：数据库清空后重新扫描，标签自动回来")
    reset_db()
    scan(force=True)
    rec = db.get_comic(cid_of("A/a.cbz"))
    check("标签从文件恢复", tag_names(cid_of("A/a.cbz")) == ["language:chinese", "parody:kancolle"], tag_names(cid_of("A/a.cbz")))
    check("自定义显示名恢复",
          next((t["label"] for t in rec["tags"] if t["name"] == "parody:kancolle"), "") == "舰队collection")
    check("阅读进度恢复", rec["last_page"] == 2, rec["last_page"])

    print("3) 文件改名后旁挂文件自动认领")
    old_p, new_p = LIB / "A" / "a.cbz", LIB / "A" / "a-renamed.cbz"
    os.replace(old_p, new_p)
    snap = scan()
    check("旁挂文件跟到新名字", (LIB / "A" / "a-renamed.cbz.json").is_file())
    check("旧名字的 json 不在了", not (LIB / "A" / "a.cbz.json").exists())
    check("认领计数 +1", snap.get("claimed", 0) >= 1, snap.get("claimed"))
    check("改名后标签还在", tag_names(cid_of("A/a-renamed.cbz")) == ["language:chinese", "parody:kancolle"],
          tag_names(cid_of("A/a-renamed.cbz")))

    print("4) 别的设备改过标签，文件拷回来后以文件为准")
    jp = LIB / "A" / "a-renamed.cbz.json"
    data = json.loads(jp.read_text(encoding="utf-8"))
    data["tags"] = [{"name": "female:巨乳", "display": "", "color": ""}]
    data["progress"] = {"lastPage": 1, "pages": 3}
    data["updatedAt"] = int(time.time()) + 60
    jp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    scan()
    check("采纳文件里的标签", tag_names(cid_of("A/a-renamed.cbz")) == ["female:巨乳"], tag_names(cid_of("A/a-renamed.cbz")))
    check("采纳文件里的进度", (db.get_comic(cid_of("A/a-renamed.cbz")) or {}).get("last_page") == 1)

    print("5) 旁边已有别人的同名 json 时不覆盖，换个名字写")
    foreign = LIB / "C" / "c.cbz.json"
    foreign.write_text('{"hello": "world"}', encoding="utf-8")
    cid_c = cid_of("C/c.cbz")
    db.set_comic_tags(cid_c, [{"name": "male:单男主"}])
    sidecar.write_for(cid_c)
    check("别人的 json 原样保留", json.loads(foreign.read_text(encoding="utf-8")) == {"hello": "world"})
    check("改用 c.cbz.mangashelf.json", (LIB / "C" / "c.cbz.mangashelf.json").is_file())

    print("6) 关闭开关后不再写文件")
    use_temp_env(sidecar_on=False)
    bcid = cid_of("B/b.cbz")
    db.set_comic_tags(bcid, [{"name": "language:japanese"}])
    check("write_for 直接返回 False", sidecar.write_for(bcid) is False)
    check("确实没有生成文件", not (LIB / "B" / "b.cbz.json").exists())

    print("7) 损坏的旁挂文件不影响扫描")
    use_temp_env(sidecar_on=True)
    bad = LIB / "B" / "b.cbz.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    snap = scan(force=True)
    check("扫描没有失败项", snap["failed"] == 0, snap["failed"])
    check("坏文件内容没被动", bad.read_text(encoding="utf-8") == "{ this is not json")
    check("数据库里仍有标签", tag_names(cid_of("B/b.cbz")) == ["language:japanese"], tag_names(cid_of("B/b.cbz")))
    check("改写到备用名", (LIB / "B" / "b.cbz.mangashelf.json").is_file())

    print("8) 全量写出")
    written = sidecar.sync_all()
    check("sync_all 返回数量", isinstance(written, int), written)
    check("再次全量写出没有多余改动", sidecar.sync_all() == 0, "仍有重复写入")

    print(f"\n结果：{passed} 通过，{failed} 失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
