"""系列归属能不能跟着文件走：整库搬家 / 换机器 / 改文件名 的端到端验证。

场景：
  1. 老库里三本书，两本归进同一个系列并排好顺序 → 检查旁挂 json 里真有 series / seriesOrder；
  2. 模拟搬家：只把「压缩包 + 旁挂 json」复制到另一个目录（并顺手改掉其中一本的文件名），
     数据目录换成全新的空库（等于换一台机器），重新扫描；
  3. 断言：系列名、成员、顺序、每本的 seriesOrder 全部从文件里恢复，没有依赖任何中心配置。

全部在临时目录里跑，不接触真实漫画库和真实数据库。
用法：D:/python/python.exe tools/test_series_migrate.py
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

from server import config, db, scanner, sidecar  # noqa: E402

BASE = Path(tempfile.mkdtemp(prefix="mangashelf-series-mig-"))
LIB_OLD = BASE / "library_old"
LIB_NEW = BASE / "library_new"
SERIES = "测试系列"

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


def use_lib(lib: Path, data_name: str, sidecar_on: bool = True) -> None:
    """切到「某台机器的某个数据目录 + 某个漫画库」。"""
    config.DATA_DIR = BASE / data_name
    config.DB_PATH = config.DATA_DIR / "library.db"
    config.COVER_DIR = config.DATA_DIR / "covers"
    config.PAGE_DIR = config.DATA_DIR / "pages"
    config.CONFIG_PATH = config.DATA_DIR / "config.json"
    config.WEB_DIR = ROOT / "web"
    config._cache = dict(config.DEFAULT_CONFIG, library_path=str(lib), sidecar=sidecar_on)


def fresh_db() -> None:
    config.ensure_dirs()
    db.init_db()


def make_archive(lib: Path, name: str, color=(30, 30, 50)) -> Path:
    p = lib / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        for i in range(3):
            im = Image.new("RGB", (240, 340), color)
            ImageDraw.Draw(im).text((20, 20), f"PAGE {i + 1}", fill=(250, 250, 255))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            z.writestr(f"{i + 1:03d}.jpg", buf.getvalue())
    return p


def scan(force: bool = False) -> dict:
    scanner._run(force=force)
    return scanner.STATE.snapshot()


def cid_of(rel: str) -> str:
    info = db.all_paths().get(rel)
    if not info:
        raise SystemExit(f"库里找不到 {rel}，现有：{list(db.all_paths())}")
    return info["id"]


def side(name: str) -> dict:
    p = LIB_OLD / (name + ".json")
    if not p.is_file():
        p = LIB_OLD / (name + ".mangashelf.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def series_of(cid: str) -> tuple:
    rec = db.get_comic(cid) or {}
    return (rec.get("series") or "", int(rec.get("series_order") or 0))


def main() -> None:
    LIB_OLD.mkdir(parents=True, exist_ok=True)
    LIB_NEW.mkdir(parents=True, exist_ok=True)
    use_lib(LIB_OLD, "data_old")
    fresh_db()

    print("1) 老库：建系列、排顺序")
    make_archive(LIB_OLD, "vol1.cbz", (40, 40, 80))
    make_archive(LIB_OLD, "vol2.cbz", (80, 40, 40))
    make_archive(LIB_OLD, "其他.cbz", (40, 80, 40))
    scan(force=True)

    v1, v2, other = cid_of("vol1.cbz"), cid_of("vol2.cbz"), cid_of("其他.cbz")
    db.set_series(v1, SERIES)
    db.set_series(v2, SERIES)
    db.reorder_series(SERIES, [v2, v1])          # 故意反着排：vol2 是第 1 话
    sidecar.sync_all(force=True)

    check("系列列表里有它", [s["name"] for s in db.list_series()] == [SERIES],
          str(db.list_series()))
    check("库里顺序是 vol2 → vol1",
          [r["id"] for r in db.series_comics(SERIES)] == [v2, v1],
          str([r["title"] for r in db.series_comics(SERIES)]))

    print("2) 旁挂 json：系列名和序号确实落盘了")
    s1, s2, so = side("vol1.cbz"), side("vol2.cbz"), side("其他.cbz")
    check("vol1 写了 series", s1.get("series") == SERIES, s1.get("series"))
    check("vol1 写了 seriesOrder=2", s1.get("seriesOrder") == 2, s1.get("seriesOrder"))
    check("vol2 写了 seriesOrder=1", s2.get("seriesOrder") == 1, s2.get("seriesOrder"))
    check("没进系列的书不写 series", (so.get("series") or "") == "", so.get("series"))
    check("series 字段不是空的空壳文件", "series" in s1, str(list(s1)))

    print("3) 搬家：只带压缩包 + json 到新目录（顺手给 vol1 改名）")
    for name in ("vol1.cbz", "vol2.cbz", "其他.cbz"):
        src = LIB_OLD / name
        shutil.copy2(src, LIB_NEW / name)
        if name != "其他.cbz":
            shutil.copy2(LIB_OLD / (name + ".json"), LIB_NEW / (name + ".json"))
    (LIB_NEW / "vol1.cbz").rename(LIB_NEW / "vol1_改名版.cbz")
    (LIB_NEW / "vol1.cbz.json").rename(LIB_NEW / "vol1_改名版.cbz.json")
    check("新目录里只有文件，没有任何库级配置",
          sorted(p.name for p in LIB_NEW.iterdir()) ==
          ["vol1_改名版.cbz", "vol1_改名版.cbz.json", "vol2.cbz", "vol2.cbz.json", "其他.cbz"],
          str(sorted(p.name for p in LIB_NEW.iterdir())))

    print("4) 新机器：全新空数据库，扫这个目录")
    use_lib(LIB_NEW, "data_new")          # 换一个空数据目录 = 换一台机器
    fresh_db()
    check("新库初始为空", db.list_series() == [] and not db.list_comics())
    scan(force=True)

    names = [s["name"] for s in db.list_series()]
    check("系列名从文件里恢复了", names == [SERIES], str(names))
    items = db.series_comics(SERIES)
    check("系列成员是两本", len(items) == 2, str([r["title"] for r in items]))
    check("顺序也恢复了：改名后的 vol1 排第 2",
          [r["title"] for r in items] == ["vol2", "vol1_改名版"],
          str([r["title"] for r in items]))
    check("每本的 seriesOrder 对得上",
          sorted(int(r["series_order"]) for r in items) == [1, 2],
          str([(r["title"], r["series_order"]) for r in items]))
    check("没进系列的那本仍是空系列",
          (db.get_comic(cid_of("其他.cbz")) or {}).get("series") == "",
          str((db.get_comic(cid_of("其他.cbz")) or {}).get("series")))

    print("5) 反向：搬回去时也从文件里读，且不会凭空多出系列")
    use_lib(LIB_OLD, "data_old2")
    fresh_db()
    scan(force=True)
    check("老库重扫同样是 1 个系列", [s["name"] for s in db.list_series()] == [SERIES],
          str(db.list_series()))
    check("老库顺序仍是 vol2 → vol1",
          [r["title"] for r in db.series_comics(SERIES)] == ["vol2", "vol1"],
          str([r["title"] for r in db.series_comics(SERIES)]))

    print(f"\n结果：{passed} 通过，{failed} 失败")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
