"""库级标签目录：把所有标签的定义写在漫画库根目录，跟着漫画库一起走。

    H:/Comics/mangashelf-tags.json

每本作品旁边的 ``xxx.cbz.json`` 只记录「这本用了哪些标签」；标签本身叫什么、
属于哪个分类、显示名和颜色，都记在这一份目录里。换设备时把整个漫画文件夹
拷过去，扫描会先把目录读回来（包括还没有任何作品使用的空标签），再按标签名
把每本作品对上——匹配完全靠 ``namespace:value`` 这个名字，跟数据库自增 id 无关，
所以两边 id 不一样也不影响。

文件内容::

    {
      "app": "MangaShelf",
      "kind": "tag-catalog",
      "schema": 1,
      "revision": 12,
      "updatedAt": 1763388800,
      "tags": [{"name": "language:chinese", "display": "汉语", "color": "#ff5f7e"}]
    }

``revision`` 是单调递增的计数器，用来判断目录和本地数据库谁更新：
本地标签有增删改就 +1，导出时写进去；换到新设备时本地是 0，自然以目录为准。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import archive, config, db, sidecar

APP = "MangaShelf"
KIND = "tag-catalog"
SCHEMA_VERSION = 1
FILENAME = "mangashelf-tags.json"

# 与 scanner / sidecar 保持一致，避免这几个目录里的东西被当成漫画
SKIP_DIRS = {"__MACOSX", ".git", "data", ".workbuddy", "node_modules", "$RECYCLE.BIN"}


def path_for(root: Optional[Path] = None) -> Path:
    return (root or config.library_path()) / FILENAME


def enabled() -> bool:
    """标签目录始终启用。

    它只是库根的一个小文件，不像每本旁边的 ``.cbz.json`` 会撒满整个目录，
    所以不受「标签跟随文件」开关影响——关掉那个开关的人通常只是不想在漫画
    目录里看到上千个小文件，标签目录仍然应该跟着库走。
    """
    return True


# ---------------------------------------------------------------- 读取
def read(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读目录文件。不是我们的文件（或损坏）返回 None，绝不覆盖别人的 json。"""
    p = path_for(root)
    try:
        if not p.is_file():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or raw.get("app") != APP:
        return None
    if raw.get("kind") not in (None, KIND):
        return None

    tags: List[Dict[str, str]] = []
    seen = set()
    for item in raw.get("tags") or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            display = str(item.get("display") or "").strip()[:60]
            color = str(item.get("color") or "").strip()[:16]
        elif isinstance(item, str):
            name, display, color = item.strip(), "", ""
        else:
            continue
        if not name:
            continue
        canon, _, _ = db.split_tag_name(name)
        canon = canon[:60]
        if not canon or canon in seen:
            continue
        seen.add(canon)
        tags.append({"name": canon, "display": display, "color": color})

    try:
        revision = int(raw.get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    return {
        "app": APP,
        "kind": KIND,
        "schema": raw.get("schema") or SCHEMA_VERSION,
        "revision": max(0, revision),
        "updatedAt": float(raw.get("updatedAt") or 0),
        "tags": tags,
    }


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 导出
def _same(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> bool:
    if not old:
        return False
    if int(old.get("revision") or 0) != int(new.get("revision") or 0):
        return False
    a = sorted((t["name"], t["display"], t["color"]) for t in old.get("tags") or [])
    b = sorted((t["name"], t["display"], t["color"]) for t in new.get("tags") or [])
    return a == b


def export_all(force: bool = False) -> bool:
    """把当前数据库里的标签目录整体写到库根。返回是否真的写了盘。"""
    if not enabled():
        return False
    root = config.library_path()
    try:
        if not root.is_dir():
            return False
    except OSError:
        return False

    tags = db.list_tags()
    rev = db.tags_revision()
    target = path_for(root)

    if not tags:
        # 一个标签都没有了：目录文件也就没有存在的意义。
        # 把我们自己写的那份删掉，避免「删了又回来」的错觉；不碰别人的文件。
        try:
            if not target.exists():
                return False
        except OSError:
            return False
        if read(root) is None:
            return False  # 别人的 / 损坏的文件，不动
        try:
            target.unlink()
        except OSError:
            pass
        if not target.exists():
            return True
        # 删不掉（被占用、权限不足）时退一步写一份空的，至少别留旧标签
        try:
            _atomic_write(target, {
                "app": APP,
                "kind": KIND,
                "schema": SCHEMA_VERSION,
                "revision": max(rev, 1),
                "updatedAt": int(time.time()),
                "tags": [],
            })
            return True
        except OSError:
            return False

    payload = {
        "app": APP,
        "kind": KIND,
        "schema": SCHEMA_VERSION,
        "revision": max(rev, 1),
        "updatedAt": int(time.time()),
        "tags": [
            {"name": t["name"], "display": t.get("display") or "", "color": t.get("color") or ""}
            for t in tags
        ],
    }

    old = read(root)
    if not force and _same(old, payload):
        return False
    try:
        _atomic_write(target, payload)
    except OSError:
        return False
    if rev < 1:
        db.set_tags_revision(1)
    return True


# ---------------------------------------------------------------- 导入
def import_into_db(cat: Dict[str, Any]) -> int:
    """把目录文件里的标签合并进数据库，返回新建的数量。

    只增不删：本地多出来的标签（也许是别的设备上刚加的）保留，删除传播靠
    每次导出都是全量快照——本地删掉后写出的目录里就没有它了。
    """
    created = 0
    for t in cat.get("tags") or []:
        try:
            if db.tag_import(t["name"], t.get("display") or "", t.get("color") or ""):
                created += 1
        except Exception:
            continue
    return created


def _has_archives(root: Path) -> bool:
    """库里是否还有任意一本漫画（只看磁盘，不看数据库）。"""
    if not root.is_dir():
        return False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in SKIP_DIRS and not d.startswith("$")
            ]
            for name in filenames:
                if name.startswith("."):
                    continue
                if os.path.splitext(name)[1].lower() in archive.ARCHIVE_EXTS:
                    return True
    except OSError:
        return True  # 读不动就当它还有东西，别误删标签
    return False


def reset_if_orphaned() -> bool:
    """目录文件被删掉时，判断用户是不是想「清空标签」。

    三个条件同时满足才清空本地标签：

    * 库根的 ``mangashelf-tags.json`` 不在了（不是损坏，是不存在）
    * 数据库里确实还有标签
    * 漫画库里一本作品都没有，也没有残留的 ``作品名.cbz.json``

    只要还有作品在，就当作是误删文件，从数据库重建目录文件（数据不动）。
    这样「清空整个漫画文件夹」= 全新开始，而「只删了 json」不会丢标签。
    """
    if not enabled():
        return False
    root = config.library_path()
    try:
        if not root.is_dir() or path_for(root).is_file():
            return False
    except OSError:
        return False
    if not db.list_tags():
        return False
    if _has_archives(root) or sidecar.has_any_sidecar(root):
        return False
    db.clear_tags()
    return True


def ensure_catalog() -> str:
    """轻量校验：目录文件不在了就处理一下（空库清空标签 / 有作品则重建）。

    刷新页面时调用，比整轮扫描快得多：文件在就一次 stat 直接返回。
    返回 ``"cleared"`` / ``"exported"`` / ``"none"``。
    """
    if not enabled():
        return "none"
    root = config.library_path()
    try:
        if path_for(root).is_file():
            return "none"
    except OSError:
        return "none"
    if reset_if_orphaned():
        return "cleared"
    return "exported" if export_all() else "none"


def clear_all() -> Dict[str, int]:
    """用户主动清空标签：数据库、每本作品旁的标签、库根目录文件一起清。

    阅读进度和作品本身不受影响。
    """
    root = config.library_path()
    tags = db.clear_tags()
    files = 0
    try:
        files = sidecar.clear_all_tags(root)
    except Exception:
        files = 0
    try:
        path_for(root).unlink(missing_ok=True)
    except OSError:
        pass
    return {"tags": tags, "files": files}


def merge(path: Optional[Path] = None) -> str:
    """启动 / 扫描时调用：目录文件和本地数据库取较新的那份，并保持两边一致。

    返回 ``"imported"`` / ``"exported"`` / ``"cleared"`` / ``"none"``（诊断用）。
    """
    if not enabled():
        return "none"
    root = path or config.library_path()
    cat = read(root)
    local_rev = db.tags_revision()

    if cat is None:
        # 目录文件不在了：空库当作「用户清空了库」，否则从数据库重建
        if reset_if_orphaned():
            return "cleared"
        if export_all():
            return "exported"
        return "none"

    if int(cat["revision"]) > local_rev:
        import_into_db(cat)
        db.set_tags_revision(int(cat["revision"]))
        return "imported"

    if export_all():
        return "exported"
    return "none"


def status() -> Dict[str, Any]:
    """给界面用的状态：目录文件在哪、有多少个标签、是否已同步。"""
    root = config.library_path()
    p = path_for(root)
    cat = read(root)
    rev = db.tags_revision()
    return {
        "file": FILENAME,
        "path": str(p),
        "exists": p.is_file(),
        "enabled": enabled(),
        "tags": len(cat["tags"]) if cat else 0,
        "revision": rev,
        "fileRevision": int(cat["revision"]) if cat else 0,
        "inSync": bool(cat) and int(cat["revision"]) == rev,
    }
