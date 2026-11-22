"""漫画库扫描：遍历目录、增量解析压缩包、生成封面。"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import archive, config, db, sidecar, taglib, thumbs

SKIP_DIRS = {"__MACOSX", ".git", "data", ".workbuddy", "node_modules", "$RECYCLE.BIN"}


class ScanState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.total = 0
        self.done = 0
        self.added = 0
        self.updated = 0
        self.removed = 0
        self.failed = 0
        self.claimed = 0
        self.written = 0
        self.current = ""
        self.errors: List[Dict[str, str]] = []
        self.started_at = 0.0
        self.finished_at = 0.0
        self.library = ""

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "total": self.total,
                "done": self.done,
                "added": self.added,
                "updated": self.updated,
                "removed": self.removed,
                "failed": self.failed,
                "claimed": self.claimed,
                "written": self.written,
                "current": self.current,
                "errors": self.errors[-30:],
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "library": self.library,
            }


STATE = ScanState()
_thread: Optional[threading.Thread] = None


def _iter_archives(root: Path) -> List[Path]:
    found: List[Path] = []
    if not root.exists():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in SKIP_DIRS and not d.startswith("$")
        ]
        for name in filenames:
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in archive.ARCHIVE_EXTS:
                found.append(Path(dirpath) / name)
    return found


def _title_of(path: Path) -> str:
    stem = path.stem
    return stem.strip() or path.name


def _process(
    path: Path,
    root: Path,
    force: bool,
    known: Dict[str, Dict[str, Any]],
    state: ScanState,
    orphans: Optional[Dict[int, List[Dict[str, Any]]]] = None,
) -> None:
    rel = path.relative_to(root).as_posix()
    try:
        st = path.stat()
    except OSError as exc:
        with state.lock:
            state.failed += 1
            state.errors.append({"file": rel, "error": str(exc)})
        return

    prev = known.get(rel)
    unchanged = (
        prev is not None
        and not force
        and prev.get("size") == st.st_size
        and abs(float(prev.get("mtime") or 0) - st.st_mtime) < 1
        and prev.get("cover_file")
    )

    # 文件被改名/移动时，把原位置那份 xxx.cbz.json 认领过来（要在「未变化」判断之前，
    # 因为改名后文件内容是不变的）
    adopted = False
    if orphans:
        try:
            adopted = sidecar.adopt(path, st.st_size, orphans)
            if adopted:
                with state.lock:
                    state.claimed += 1
        except Exception:
            adopted = False

    if unchanged:
        # 文件没变，但旁边的标签文件可能被改过（从别的设备拷回来、手工编辑）
        if adopted or sidecar.has_file(path):
            try:
                sidecar.merge(prev["id"], path)
            except Exception:
                pass
        with state.lock:
            state.done += 1
        return

    cid = prev["id"] if prev else db.make_id(rel)
    rec: Dict[str, Any] = {
        "id": cid,
        "rel_path": rel,
        "title": _title_of(path),
        "kind": archive.ARCHIVE_EXTS.get(path.suffix.lower(), ""),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "page_count": 0,
        "cover_file": None,
        "cover_ver": f"{int(st.st_mtime)}-{st.st_size}",
        "error": None,
    }

    try:
        with archive.open_source(path) as src:
            rec["kind"] = src.kind
            rec["page_count"] = src.page_count
            first, _mime = src.page_bytes(0)
        cover = thumbs.write_cover(first, cid)
        rec["cover_file"] = cover
        if not cover:
            rec["error"] = "封面生成失败（首图可能损坏）"
    except archive.SourceError as exc:
        rec["error"] = str(exc)
        rec["cover_file"] = thumbs.cover_path(cid).name if thumbs.cover_path(cid).exists() else None
    except Exception as exc:  # 单本出错不应中断整次扫描
        rec["error"] = f"解析失败：{exc}"

    db.upsert_comic(rec)

    # 标签/阅读进度跟着文件走：把这本旁边的 xxx.cbz.json 与数据库对齐
    try:
        sidecar.merge(rec["id"], path)
    except Exception:
        pass

    # 文件内容变了，之前缓存的页面缩略图已失效
    if prev:
        thumbs.remove_pages(cid)

    with state.lock:
        state.done += 1
        if rec["error"]:
            state.failed += 1
            state.errors.append({"file": rel, "error": rec["error"]})
        if prev:
            state.updated += 1
        else:
            state.added += 1


def _run(force: bool) -> None:
    root = config.library_path()
    state = STATE
    with state.lock:
        state.running = True
        state.total = 0
        state.done = 0
        state.added = 0
        state.updated = 0
        state.removed = 0
        state.failed = 0
        state.claimed = 0
        state.written = 0
        state.current = ""
        state.errors = []
        state.started_at = time.time()
        state.finished_at = 0.0
        state.library = str(root)

    try:
        # 先把库根的标签目录读回来（换设备后本地数据库还是空的，这一步能恢复
        # 包括「还没挂到任何作品上的空标签」在内的整套标签体系）
        try:
            taglib.merge(root)
        except Exception:
            pass

        files = _iter_archives(root)
        known = db.all_paths()
        with state.lock:
            state.total = len(files)

        seen = set()
        for f in files:
            seen.add(f.relative_to(root).as_posix())

        # 先尝试识别「重命名/移动」：尺寸与修改时间一致的新旧路径配对，
        # 直接迁移记录，避免用户整理文件夹后标签和阅读进度丢失。
        orphan_pool: Dict[int, List[Dict[str, Any]]] = {}
        if sidecar.enabled():
            try:
                for item in sidecar.list_orphans(root):
                    orphan_pool.setdefault(item["size"], []).append(item)
            except Exception:
                orphan_pool = {}

        removed_rels = [r for r in known if r not in seen]
        added_rels = [r for r in seen if r not in known]
        if removed_rels and added_rels:
            buckets: Dict[Any, List[str]] = {}
            for rel in removed_rels:
                info = known[rel]
                key = (info.get("size"), round(float(info.get("mtime") or 0)))
                buckets.setdefault(key, []).append(rel)
            for new_rel in list(added_rels):
                try:
                    st = (root / new_rel).stat()
                except OSError:
                    continue
                key = (st.st_size, round(st.st_mtime))
                cands = buckets.get(key) or []
                if len(cands) != 1:
                    continue
                old_rel = cands[0]
                if db.move_comic(old_rel, new_rel, _title_of(root / new_rel)):
                    known[new_rel] = known.pop(old_rel)
                    removed_rels.remove(old_rel)
                    added_rels.remove(new_rel)
                    buckets.pop(key, None)
                    with state.lock:
                        state.current = f"{old_rel} → {new_rel}"

        for f in files:
            rel = f.relative_to(root).as_posix()
            with state.lock:
                state.current = rel
            _process(f, root, force, known, state, orphan_pool)

        # 清理已从磁盘删除的条目
        for rel in removed_rels:
            cover = db.delete_comic(rel)
            if cover:
                try:
                    (config.COVER_DIR / cover).unlink(missing_ok=True)
                except OSError:
                    pass
            cid = (known.get(rel) or {}).get("id")
            if cid:
                thumbs.remove_pages(cid)
            with state.lock:
                state.removed += 1

        # 注意：这里不清理空标签。用户可能先建好分类、再往里放作品，
        # 自动清理会让刚创建的标签凭空消失。删除标签交给用户手动操作。

        # 给所有作品补写旁挂文件：首次启用时把老库里的标签一次性写到文件旁边，
        # 之后每次扫描只是校验（内容没变就不落盘）。
        if sidecar.enabled():
            try:
                with state.lock:
                    state.current = "写入作品旁的标签文件…"
                state.written = sidecar.sync_all()
            except Exception:
                state.written = 0

        # 顺手清掉已删除作品的页面缩略图（防止删除瞬间的并发请求留下残骸）
        try:
            thumbs.prune_page_cache(db.all_ids())
        except Exception:
            pass

        # 扫描中可能从旁挂文件里冒出新的标签，收尾时把目录写回库根
        try:
            taglib.export_all()
        except Exception:
            pass
    except Exception as exc:
        with state.lock:
            state.errors.append({"file": "", "error": f"扫描中断：{exc}"})
    finally:
        with state.lock:
            state.running = False
            state.current = ""
            state.finished_at = time.time()


def scan_async(force: bool = False) -> bool:
    """启动后台扫描，若已在扫描中返回 False。"""
    global _thread
    if STATE.running:
        return False
    _thread = threading.Thread(target=_run, args=(force,), daemon=True, name="library-scan")
    _thread.start()
    return True


def wait(timeout: float = 60.0) -> None:
    if _thread and _thread.is_alive():
        _thread.join(timeout)


def prune_missing(min_interval: float = 2.0) -> int:
    """清掉「数据库里有、磁盘上已经没有」的记录，返回清理数量。

    刷新页面时顺手跑一次，用户清空 / 删掉文件后不必等下一轮监听扫描，
    书架立刻就是真实状态。``min_interval`` 防止连续刷新时反复 stat 整个库。
    """
    global _prune_at
    if STATE.running:
        return 0
    now = time.time()
    if now - _prune_at < min_interval:
        return 0
    _prune_at = now
    root = config.library_path()
    gone: List[Tuple[str, Dict[str, Any]]] = []
    for rel, info in db.all_paths().items():
        try:
            if not (root / rel).is_file():
                gone.append((rel, info))
        except OSError:
            gone.append((rel, info))
    for rel, info in gone:
        cover = db.delete_comic(rel)
        if cover:
            try:
                (config.COVER_DIR / cover).unlink(missing_ok=True)
            except OSError:
                pass
        cid = info.get("id")
        if cid:
            try:
                thumbs.remove_pages(cid)
            except Exception:
                pass
    return len(gone)


# ---------------------------------------------------------------- 自动监听
_watcher_started = False
_prune_at = 0.0


def _fingerprint(root: Path):
    """库内容指纹：路径 + 大小 + 修改时间，用于检测文件夹变化。"""
    out = []
    for p in _iter_archives(root):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((p.relative_to(root).as_posix(), st.st_size, round(st.st_mtime)))
    out.sort()
    return tuple(out)


def _watch_loop(interval: float) -> None:
    try:
        last = _fingerprint(config.library_path())
    except Exception:
        last = ()
    while True:
        time.sleep(interval)
        try:
            if STATE.running:
                continue
            cur = _fingerprint(config.library_path())
            if cur != last:
                last = cur
                scan_async()
            # 兜底清理：删除瞬间仍在生成的缩略图可能把目录重建出来
            thumbs.prune_page_cache(db.all_ids())
        except Exception:
            continue


def start_watcher(interval: float = 15.0) -> None:
    """后台轮询漫画库，发现新增/删除/改名就自动增量扫描。"""
    global _watcher_started
    if _watcher_started:
        return
    _watcher_started = True
    threading.Thread(
        target=_watch_loop, args=(interval,), daemon=True, name="library-watch"
    ).start()
