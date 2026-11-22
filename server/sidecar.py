"""旁挂标签文件：把标签和阅读进度写到漫画文件旁边。

    H:/Comics/foo.cbz  →  H:/Comics/foo.cbz.json

这样数据跟着文件本身走：整库搬到另一台设备、重新分层文件夹、单独拷走一本，
标签和阅读进度都不会丢。数据库仍然负责运行时查询，旁挂文件是随身的持久副本。

文件内容示例::

    {
      "app": "MangaShelf",
      "schema": 1,
      "title": "(C97) [moni (naoto)] ...",
      "category": "dojinshi",
      "status": "",
      "favorite": false,
      "fingerprint": {"size": 12345678, "hash": "3f2a91c4d0b7e156"},
      "tags": [{"name": "language:chinese", "display": "", "color": "#ff5f7e"}],
      "progress": {"lastPage": 23, "pages": 24},
      "updatedAt": 1763388800
    }
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import archive, config, db

APP = "MangaShelf"
SCHEMA_VERSION = 1
SUFFIX = ".json"
ALT_SUFFIX = ".mangashelf.json"

# 扫描时跳过的目录（与 scanner 保持一致，避免循环导入）
SKIP_DIRS = {"__MACOSX", ".git", "data", ".workbuddy", "node_modules", "$RECYCLE.BIN"}

_HASH_CHUNK = 128 * 1024


def enabled() -> bool:
    try:
        return bool(config.load_config().get("sidecar", True))
    except Exception:
        return True


# ---------------------------------------------------------------- 路径与指纹
def path_for(archive_path: Path) -> Path:
    """主文件名：foo.cbz → foo.cbz.json"""
    return archive_path.with_name(archive_path.name + SUFFIX)


def alt_path_for(archive_path: Path) -> Path:
    """备用名：目标 json 被别的软件占用时改用它，避免覆盖别人的文件。"""
    return archive_path.with_name(archive_path.name + ALT_SUFFIX)


def fingerprint(archive_path: Path) -> Dict[str, Any]:
    """便宜的指纹：大小 + 首尾各 128KB 的 sha1，几 GB 的包也很快。"""
    st = archive_path.stat()
    size = st.st_size
    h = hashlib.sha1()
    h.update(str(size).encode("ascii"))
    with archive_path.open("rb") as fh:
        h.update(fh.read(_HASH_CHUNK))
        if size > _HASH_CHUNK * 2:
            fh.seek(-_HASH_CHUNK, os.SEEK_END)
            h.update(fh.read(_HASH_CHUNK))
    return {"size": size, "hash": h.hexdigest()[:16]}


# ---------------------------------------------------------------- 读写
def _load(path: Path) -> Optional[Dict[str, Any]]:
    """读一个旁挂文件；不是我们的格式就返回 None（绝不改别人的 json）。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or raw.get("app") != APP:
        return None
    tags = []
    for t in raw.get("tags") or []:
        if isinstance(t, dict) and t.get("name"):
            tags.append({
                "name": str(t["name"])[:60],
                "display": str(t.get("display") or "")[:60],
                "color": str(t.get("color") or "")[:16],
            })
        elif isinstance(t, str) and t.strip():
            tags.append({"name": t.strip()[:60], "display": "", "color": ""})
    prog = raw.get("progress") if isinstance(raw.get("progress"), dict) else {}
    fp = raw.get("fingerprint") if isinstance(raw.get("fingerprint"), dict) else {}
    try:
        sorder = int(raw.get("seriesOrder") or 0)
    except (TypeError, ValueError):
        sorder = 0
    return {
        "app": APP,
        "schema": raw.get("schema") or 1,
        "title": raw.get("title") or "",
        "category": db.normalize_category(raw.get("category")),
        "status": db.normalize_status(raw.get("status")),
        "favorite": raw.get("favorite") is True
        or str(raw.get("favorite") or "").strip().lower() in ("1", "true", "yes"),
        # 老版本书签没有这几个字段，别把它当成「显式设成 false / 更新完毕 / 不在系列里」
        "hasStatus": "status" in raw,
        "hasFavorite": "favorite" in raw,
        "hasSeries": "series" in raw,
        "series": db.normalize_series(raw.get("series")),
        "seriesOrder": max(0, sorder),
        "fingerprint": {"size": fp.get("size") or 0, "hash": fp.get("hash") or ""},
        "tags": tags,
        "progress": {
            "lastPage": int(prog.get("lastPage") or 0),
            "pages": int(prog.get("pages") or 0),
        },
        "updatedAt": float(raw.get("updatedAt") or 0),
    }


def has_file(archive_path: Path) -> bool:
    """这本作品旁边是否已经有（我们的）旁挂文件。"""
    return path_for(archive_path).is_file() or alt_path_for(archive_path).is_file()


def read_for(archive_path: Path) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """返回 (实际文件路径, 内容)。没有或格式不符返回 None。"""
    for p in (path_for(archive_path), alt_path_for(archive_path)):
        try:
            if p.is_file():
                data = _load(p)
                if data is not None:
                    return p, data
        except OSError:
            continue
    return None


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _tag_tuples(items: Iterable[Any]) -> List[Tuple[str, str, str]]:
    out = []
    for t in items or []:
        if isinstance(t, dict):
            out.append((str(t.get("name") or ""), str(t.get("display") or ""), str(t.get("color") or "")))
        else:
            out.append((str(t or ""), "", ""))
    return sorted(out)


def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """忽略 updatedAt，判断内容是否一致（避免无意义的磁盘写）。"""
    return (
        _tag_tuples(a.get("tags")) == _tag_tuples(b.get("tags"))
        and (a.get("category") or "") == (b.get("category") or "")
        and (a.get("status") or "") == (b.get("status") or "")
        and bool(a.get("favorite")) == bool(b.get("favorite"))
        and (a.get("series") or "") == (b.get("series") or "")
        and int(a.get("seriesOrder") or 0) == int(b.get("seriesOrder") or 0)
        and (a.get("progress") or {}).get("lastPage") == (b.get("progress") or {}).get("lastPage")
        and (a.get("fingerprint") or {}).get("size") == (b.get("fingerprint") or {}).get("size")
    )


def _build_payload(rec: Dict[str, Any], archive_path: Path, old: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    fp: Dict[str, Any] = {"size": 0, "hash": ""}
    try:
        st_size = archive_path.stat().st_size
        old_fp = (old or {}).get("fingerprint") or {}
        if old_fp.get("size") == st_size and old_fp.get("hash"):
            # 大小没变就沿用旧 hash，翻页时不必反复读文件头尾
            fp = {"size": st_size, "hash": old_fp["hash"]}
        else:
            fp = fingerprint(archive_path)
    except OSError:
        pass
    return {
        "app": APP,
        "schema": SCHEMA_VERSION,
        "title": rec.get("title") or archive_path.stem,
        "category": rec.get("category") or "",
        "status": rec.get("status") or "",
        "favorite": bool(rec.get("favorite")),
        "series": rec.get("series") or "",
        "seriesOrder": int(rec.get("series_order") or 0),
        "fingerprint": fp,
        "tags": [
            {"name": t.get("name") or "", "display": t.get("display") or "", "color": t.get("color") or ""}
            for t in (rec.get("tags") or [])
            if t.get("name")
        ],
        "progress": {
            "lastPage": int(rec.get("last_page") or 0),
            "pages": int(rec.get("page_count") or 0),
        },
        "updatedAt": int(time.time()),
    }


def _write_rec(rec: Dict[str, Any], root: Path, force: bool = False) -> bool:
    rel = rec.get("rel_path") or ""
    if not rel:
        return False
    archive_path = root / rel
    try:
        if not archive_path.is_file():
            return False
    except OSError:
        return False

    target = path_for(archive_path)
    old: Optional[Dict[str, Any]] = None
    try:
        if target.is_file():
            old = _load(target)
            if old is None:
                # 同名 json 是别的软件的东西，换个名字写，别覆盖
                target = alt_path_for(archive_path)
                old = _load(target) if target.is_file() else None
        else:
            alt = alt_path_for(archive_path)
            if alt.is_file():
                target = alt
                old = _load(alt)
    except OSError:
        return False

    tags = rec.get("tags") or []
    last_page = int(rec.get("last_page") or 0)
    if (not tags and last_page <= 0 and not (rec.get("category") or "")
            and not (rec.get("status") or "") and not rec.get("favorite")
            and not (rec.get("series") or "") and not target.exists()):
        # 没标签、没进度、没分类、没标记、不在系列里：不凭空生成一堆空文件
        return False

    payload = _build_payload(rec, archive_path, old)
    if not force and old is not None and _same(old, payload):
        return False
    try:
        _atomic_write(target, payload)
        return True
    except OSError:
        return False


def write_for(cid: str, force: bool = False) -> bool:
    if not enabled():
        return False
    rec = db.get_comic(cid)
    if not rec:
        return False
    return _write_rec(rec, config.library_path(), force)


# ---------------------------------------------------------------- 导入
def apply_to_db(cid: str, data: Dict[str, Any]) -> None:
    """把旁挂文件里的标签和进度写进数据库。"""
    items = [
        {"name": t["name"], "display": t.get("display") or "", "color": t.get("color") or ""}
        for t in data.get("tags") or []
    ]
    db.set_comic_tags(cid, items, touch=False)
    if data.get("category") is not None:
        db.set_category(cid, data.get("category"), touch=False)
    if data.get("hasStatus") and "status" in data:
        db.set_status(cid, data.get("status"), touch=False)
    if data.get("hasFavorite") and "favorite" in data:
        db.set_favorite(cid, data.get("favorite"), touch=False)
    if data.get("hasSeries"):
        db.set_series(cid, data.get("series"), data.get("seriesOrder") or None, touch=False)
    prog = data.get("progress") or {}
    lp = prog.get("lastPage")
    if isinstance(lp, int) and lp >= 0:
        db.set_last_page(cid, lp, touch=False)
    db.touch_meta(cid, float(data.get("updatedAt") or time.time()))


def merge(cid: str, archive_path: Path) -> str:
    """扫描到一本时调用：旁挂文件和服务端数据取较新的那份，并保持两边一致。

    返回 "imported" / "exported" / "none"（用于诊断输出）。
    """
    if not enabled():
        return "none"
    rec = db.get_comic(cid)
    if not rec:
        return "none"

    hit = read_for(archive_path)
    if hit is None:
        # 数据库里有标签/进度但没有旁挂文件 → 补写（首次启用由这里完成迁移）
        return "exported" if _write_rec(rec, config.library_path()) else "none"

    data = hit[1]
    meta = float(rec.get("meta_updated_at") or 0)
    local_has = (bool(rec.get("tags")) or int(rec.get("last_page") or 0) > 0
                 or bool(rec.get("status")) or bool(rec.get("favorite"))
                 or bool(rec.get("series")))

    # meta == 0 且本地有数据：属于启用旁挂文件之前积累的老数据，以本地为准
    legacy = meta == 0 and local_has
    if float(data.get("updatedAt") or 0) > meta and not legacy:
        apply_to_db(cid, data)
        return "imported"

    if local_has:
        return "exported" if _write_rec(rec, config.library_path()) else "none"
    return "none"


# ---------------------------------------------------------------- 批量维护
def iter_sidecars(root: Path):
    """遍历库里所有旁挂文件（包括Owner已经不在的孤儿文件）。"""
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in SKIP_DIRS and not d.startswith("$")
        ]
        for name in filenames:
            low = name.lower()
            if not low.endswith(SUFFIX):
                continue
            stem = name[: -len(SUFFIX)]
            if stem.lower().endswith(".mangashelf"):
                stem = stem[: -len(".mangashelf")]
            if os.path.splitext(stem)[1].lower() not in archive.ARCHIVE_EXTS:
                continue
            yield Path(dirpath) / name


def clear_all_tags(root: Optional[Path] = None) -> int:
    """把所有旁挂文件里的标签清空，保留标题和阅读进度。返回改写的文件数。"""
    root = root or config.library_path()
    changed = 0
    for p in iter_sidecars(root):
        data = _load(p)
        if not data or not data.get("tags"):
            continue
        data["tags"] = []
        data["updatedAt"] = int(time.time())
        try:
            _atomic_write(p, data)
            changed += 1
        except OSError:
            continue
    return changed


def has_any_sidecar(root: Path) -> bool:
    """库里是否存在任意一份旁挂文件（用于判断「空库」是否真的空）。"""
    for _ in iter_sidecars(root):
        return True
    return False


# ---------------------------------------------------------------- 改名后的认领
def list_orphans(root: Path) -> List[Dict[str, Any]]:
    """库里所有「主人已经不在了」的旁挂文件（文件被改名/移走留下的）。"""
    found: List[Dict[str, Any]] = []
    if not root.exists():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in SKIP_DIRS and not d.startswith("$")
        ]
        for name in filenames:
            low = name.lower()
            if not low.endswith(SUFFIX):
                continue
            p = Path(dirpath) / name
            owner_name = ""
            if low.endswith(ALT_SUFFIX):
                owner_name = name[: -len(ALT_SUFFIX)]
            else:
                owner_name = name[: -len(SUFFIX)]
            if not owner_name:
                continue
            if os.path.splitext(owner_name)[1].lower() not in archive.ARCHIVE_EXTS:
                continue
            if (p.parent / owner_name).exists():
                continue  # 主人还在，不是孤儿
            data = _load(p)
            if data is None:
                continue
            fp = data.get("fingerprint") or {}
            found.append({
                "path": p,
                "size": int(fp.get("size") or 0),
                "hash": fp.get("hash") or "",
                "data": data,
            })
    return found


def adopt(archive_path: Path, size: int, pool: Dict[int, List[Dict[str, Any]]]) -> bool:
    """漫画被改名后，用「大小 + 指纹」把原来那份旁挂文件认领回来。"""
    if path_for(archive_path).exists() or alt_path_for(archive_path).exists():
        return False
    cands = pool.get(size) or []
    if not cands:
        return False
    try:
        fp = fingerprint(archive_path)
    except OSError:
        return False
    for item in list(cands):
        if item.get("hash") and item["hash"] == fp["hash"]:
            target = path_for(archive_path)
            try:
                os.replace(item["path"], target)
            except OSError:
                return False
            cands.remove(item)
            return True
    return False


# ---------------------------------------------------------------- 写入调度
_pending: Dict[str, float] = {}
_cv = threading.Condition()
_worker: Optional[threading.Thread] = None
DELAY = 1.2


def _loop() -> None:
    while True:
        with _cv:
            while not _pending:
                _cv.wait()
            now = time.time()
            due = [c for c, t in _pending.items() if t <= now]
            if not due:
                _cv.wait(max(0.05, min(_pending.values()) - now))
                continue
            for c in due:
                _pending.pop(c, None)
        for c in due:
            try:
                write_for(c)
            except Exception:
                pass


def sync(cid: str, delay: float = DELAY) -> None:
    """把某本作品的最新标签/进度写出（合并短时间内的多次调用）。"""
    if not enabled():
        return
    global _worker
    with _cv:
        _pending[cid] = time.time() + delay
        _cv.notify()
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, daemon=True, name="sidecar-writer")
            _worker.start()


def flush(timeout: float = 3.0) -> None:
    """等待队列清空（测试和退出时用）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _cv:
            if not _pending:
                return
        time.sleep(0.05)


def sync_all(force: bool = False) -> int:
    """给库里所有作品补写旁挂文件，返回实际写出的数量。"""
    if not enabled():
        return 0
    root = config.library_path()
    written = 0
    for rec in db.list_comics():
        try:
            if _write_rec(rec, root, force=force):
                written += 1
        except Exception:
            continue
    return written
