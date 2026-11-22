"""基于标准库 http.server 的 JSON API 与静态资源服务。"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import archive, config, db, scanner, sidecar, taglib, thumbs

APP_NAME = "MangaShelf"
APP_VERSION = "1.0.0"

ID_RE = re.compile(r"^[0-9a-f]{16}$")

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("application/javascript", ".js")

# 发现「记录还在、文件没了」时踢一次后台扫描，10 秒内只踢一次
_last_kick = 0.0


def kick_scan() -> None:
    global _last_kick
    now = time.time()
    if now - _last_kick < 10:
        return
    _last_kick = now
    try:
        scanner.scan_async()
    except Exception:
        pass


def comic_json(row: Dict[str, Any]) -> Dict[str, Any]:
    cid = row["id"]
    ver = row.get("cover_ver") or int(row.get("mtime") or 0)
    return {
        "id": cid,
        "title": row.get("title") or "",
        "path": row.get("rel_path") or "",
        "kind": row.get("kind") or "",
        "size": row.get("size") or 0,
        "mtime": row.get("mtime") or 0,
        "pages": row.get("page_count") or 0,
        "lastPage": row.get("last_page") or 0,
        "addedAt": row.get("added_at") or 0,
        "updatedAt": row.get("updated_at") or 0,
        "cover": f"/api/comics/{cid}/cover?v={ver}",
        "category": row.get("category") or "",
        "categoryLabel": db.COMIC_CAT_LABEL.get(row.get("category") or "", ""),
        "status": row.get("status") or "",
        "favorite": bool(row.get("favorite")),
        "series": row.get("series") or "",
        "seriesOrder": int(row.get("series_order") or 0),
        "tags": row.get("tags") or [],
        "error": row.get("error") or "",
    }


_BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class RenameError(Exception):
    """文件名不合法或目标已存在。"""


def _move_path(src: Path, dst: Path) -> None:
    """移动文件；只改大小写时用临时名中转，避免 Windows 上当成同名。"""
    if os.path.normcase(str(src)) == os.path.normcase(str(dst)):
        tmp = src.with_name("." + uuid.uuid4().hex + ".tmp")
        os.replace(src, tmp)
        try:
            os.replace(tmp, dst)
        except OSError:
            os.replace(tmp, src)
            raise
    else:
        os.replace(src, dst)


def _rename_paths(row: Dict[str, Any], raw: str) -> Tuple[Path, Path, Optional[Path], Optional[Path], str]:
    """算出源文件 / 旁挂文件的新旧路径，并做完整校验。

    返回 (old_archive, new_archive, old_sidecar, new_sidecar, new_title)。
    """
    root = config.library_path()
    old_rel = row.get("rel_path") or ""
    old_archive = root / old_rel
    if not old_rel or not old_archive.is_file():
        raise RenameError("源文件已经不在库里了")

    name = str(raw or "").strip()
    if not name:
        raise RenameError("文件名不能为空")
    if len(name) > 180:
        raise RenameError("文件名太长了（最多 180 个字符）")
    if _BAD_FILENAME.search(name):
        raise RenameError('文件名不能包含 \\ / : * ? " < > | 等字符')

    ext = old_archive.suffix
    stem = name[: -len(ext)] if ext and name.lower().endswith(ext.lower()) else name
    stem = stem.rstrip(" .")
    if not stem or stem in (".", ".."):
        raise RenameError("文件名不合法")

    new_archive = old_archive.with_name(stem + ext)
    same_archive = os.path.normcase(str(new_archive)) == os.path.normcase(str(old_archive))
    if not same_archive and new_archive.exists():
        raise RenameError("同名的漫画文件已经存在")
    new_rel = new_archive.relative_to(root).as_posix()
    if not same_archive and new_rel in db.all_paths():
        raise RenameError("同名的漫画已经在库里了")

    old_sidecar = None
    new_sidecar = None
    hit = sidecar.read_for(old_archive)
    if hit:
        old_sidecar = hit[0]
        suffix = sidecar.ALT_SUFFIX if old_sidecar.name.endswith(sidecar.ALT_SUFFIX) else sidecar.SUFFIX
        new_sidecar = new_archive.with_name(new_archive.name + suffix)
        same_side = (os.path.normcase(str(new_sidecar)) == os.path.normcase(str(old_sidecar)))
        if not same_side and new_sidecar.exists():
            raise RenameError("新文件名对应的书签文件已存在")
    return old_archive, new_archive, old_sidecar, new_sidecar, stem


def comic_rename(cid: str, raw: str) -> Dict[str, Any]:
    """改磁盘上的漫画文件名，同时迁移旁挂书签与数据库记录（保留 id）。"""
    row = db.get_comic(cid)
    if not row:
        raise RenameError("漫画不存在")
    old_archive, new_archive, old_sidecar, new_sidecar, stem = _rename_paths(row, raw)
    old_rel = row["rel_path"]
    new_rel = new_archive.relative_to(config.library_path()).as_posix()
    if (os.path.normcase(str(new_archive)) == os.path.normcase(str(old_archive))
            and stem == Path(old_rel).stem):
        updated = db.get_comic(cid)
        return {"comic": comic_json(updated) if updated else None, "unchanged": True}

    moved: list[Tuple[Path, Path]] = []
    try:
        _move_path(old_archive, new_archive)
        moved.append((new_archive, old_archive))
        if old_sidecar and new_sidecar:
            _move_path(old_sidecar, new_sidecar)
            moved.append((new_sidecar, old_sidecar))
        if not db.move_comic(old_rel, new_rel, stem):
            raise RenameError("数据库里已经有同名作品")
    except Exception as exc:
        # 任何一步失败都把已经搬过的文件搬回去，避免留下半改状态
        for src, dst in reversed(moved):
            try:
                _move_path(src, dst)
            except OSError:
                pass
        if isinstance(exc, RenameError):
            raise
        raise RenameError("改名失败：" + str(exc))

    # 让旁挂书签的 title / updatedAt 跟上新名字，也避免下次扫描把旧内容读回来
    db.touch_meta(cid)
    try:
        sidecar.write_for(cid, force=True)
    except Exception:
        pass
    updated = db.get_comic(cid)
    return {
        "comic": comic_json(updated) if updated else None,
        "unchanged": False,
        "sidecar": "moved" if old_sidecar else "none",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 基础设施
    def log_message(self, fmt: str, *args) -> None:  # 静默访问日志
        pass

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _bytes(self, data: bytes, content_type: str, cache: str = "private, max-age=3600") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------ 路由
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._api(method, path[5:], qs)
            elif method == "GET":
                self._static(path)
            else:
                self._error("方法不被允许", 405)
        except BrokenPipeError:
            pass
        except Exception as exc:
            traceback.print_exc()
            try:
                self._error(f"服务器内部错误：{exc}", 500)
            except Exception:
                pass

    # ------------------------------------------------------------ 静态资源
    def _static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        target = (config.WEB_DIR / path.lstrip("/")).resolve()
        try:
            target.relative_to(config.WEB_DIR.resolve())
        except ValueError:
            self._error("非法路径", 403)
            return
        if not target.is_file():
            self._error("页面不存在", 404)
            return

        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        # 前端资源一律 no-cache：本地服务响应是毫秒级的，省这点时间不值得
        # 换来「改了 web/ 却看到旧页面」这种假故障。
        suffix = target.suffix.lower()
        cache = (
            "no-cache"
            if suffix in (".html", ".js", ".css", ".json", ".webmanifest", ".svg")
            else "public, max-age=3600"
        )
        self._bytes(data, ctype, cache)

    # ------------------------------------------------------------ API
    def _api(self, method: str, route: str, qs: Dict[str, list]) -> None:
        parts = [p for p in route.split("/") if p]

        if parts == ["identity"] and method == "GET":
            # 桌面外壳用这个接口判断「某个端口上跑的是不是这一份程序的服务」：
            # 端口可能被别的软件、或者装在别处的另一份 MangaShelf 占着。
            return self._json({
                "ok": True,
                "app": APP_NAME,
                "version": APP_VERSION,
                "root": str(config.BASE_DIR),
                "proc": proc_context(),   # 排查「打不开资源管理器」这类权限问题用
            })

        if parts == ["state"] and method == "GET":
            # 顺手校验一遍文件是否还在：用户清空/删掉漫画后刷新页面就应该是空书架，
            # 不该等到下一轮监听扫描（最多 15 秒）才消失。
            try:
                scanner.prune_missing()
            except Exception:
                pass
            # 用户把整个漫画库清空（连 mangashelf-tags.json 一起删了）时，
            # 刷新页面就应该回到「全新库」的状态，而不是让标签残留。
            try:
                taglib.ensure_catalog()
            except Exception:
                pass
            return self._json({
                "ok": True,
                "version": APP_VERSION,
                "library": str(config.library_path()),
                "libraryExists": config.library_path().exists(),
                "stats": db.stats(),
                "scan": scanner.STATE.snapshot(),
                "categories": db.category_options(),
                "config": {
                    "theme": config.load_config().get("theme", "dark"),
                    "defaultReaderMode": config.load_config().get("default_reader_mode", "paged"),
                    "port": config.load_config().get("port"),
                    "sidecar": sidecar.enabled(),
                    "tagFile": taglib.status(),
                },
            })

        if parts == ["scan"] and method == "POST":
            body = self._body()
            force = bool(body.get("force"))
            started = scanner.scan_async(force=force)
            return self._json({"ok": True, "started": started, "scan": scanner.STATE.snapshot()})

        if parts == ["scan", "progress"] and method == "GET":
            return self._json({"ok": True, "scan": scanner.STATE.snapshot()})

        if parts == ["comics"] and method == "GET":
            # 支持多个 tag 参数：?tag=language:chinese&tag=parody:original 取交集
            tags = [t for t in (qs.get("tag") or []) if t]
            q = (qs.get("q") or [""])[0]
            sort = (qs.get("sort") or ["recent"])[0]
            rows = db.list_comics(tag=tags, query=q, sort=sort)
            return self._json({
                "ok": True,
                "items": [comic_json(r) for r in rows],
                "total": len(rows),
                "library": str(config.library_path()),
            })

        if len(parts) >= 2 and parts[0] == "comics":
            cid = parts[1]
            if not ID_RE.match(cid):
                return self._error("无效的漫画 ID", 400)

            if len(parts) == 2 and method == "GET":
                row = db.get_comic(cid)
                if not row:
                    return self._error("漫画不存在", 404)
                # 记录还在但文件已被删掉/移走：不要让人点进一个空壳
                target = config.library_path() / row["rel_path"]
                try:
                    missing = not target.is_file()
                except OSError:
                    missing = True
                if missing:
                    kick_scan()
                    return self._error("这本漫画的文件已被移动或删除", 410)
                data = comic_json(row)
                data["ok"] = True
                return self._json(data)

            if len(parts) == 2 and method == "PUT":
                body = self._body()
                if "lastPage" in body:
                    try:
                        db.set_last_page(cid, max(0, int(body["lastPage"])))
                        sidecar.sync(cid, delay=1.5)
                    except (TypeError, ValueError):
                        return self._error("页码格式不正确")
                if "category" in body:
                    # 作品分类：Dojinshi / Manga / ArtistCG / GameCG / Non-H / ImageSet，空串=未分类
                    db.set_category(cid, body.get("category"))
                    sidecar.sync(cid, delay=0.6)
                if "status" in body:
                    # 更新中 / 更新完毕：会进侧栏「更新中」列表
                    db.set_status(cid, body.get("status"))
                    sidecar.sync(cid, delay=0.6)
                if "favorite" in body:
                    # 喜爱标记：会进侧栏 Favorite 列表
                    db.set_favorite(cid, body.get("favorite"))
                    sidecar.sync(cid, delay=0.6)
                if "series" in body:
                    # 系列：传空串表示移出；seriesOrder 不给就追加到末尾
                    order = body.get("seriesOrder")
                    try:
                        order = int(order) if order not in (None, "") else None
                    except (TypeError, ValueError):
                        order = None
                    db.set_series(cid, body.get("series"), order)
                    sidecar.sync(cid, delay=0.6)
                if body.get("title"):
                    db.rename_comic(cid, str(body["title"])[:200])
                row = db.get_comic(cid)
                return self._json({"ok": True, "comic": comic_json(row) if row else None})

            if len(parts) == 3 and parts[2] == "rename" and method == "POST":
                body = self._body()
                try:
                    res = comic_rename(cid, body.get("filename"))
                    return self._json({"ok": True, **res})
                except RenameError as exc:
                    return self._error(str(exc), 400)

            if len(parts) == 3 and parts[2] == "reveal" and method == "POST":
                # 右键菜单的「打开文件夹」：在系统文件管理器里定位到这本漫画的源文件
                row, path = self._comic_file(cid)
                if not row:
                    return self._error("漫画不存在", 404)
                if path is None:
                    return self._error("这本漫画的文件已被移动或删除", 410)
                try:
                    mode, diag = reveal_file(path)
                except RevealError as exc:
                    return self._json({
                        "ok": False,
                        "error": f"打开文件夹失败：{exc}",
                        "reason": exc.reason,
                        "path": str(path), "folder": str(path.parent),
                        "proc": exc.diag.get("proc") or proc_context(),
                    }, 500)
                except Exception as exc:
                    return self._json({
                        "ok": False,
                        "error": f"打开文件夹失败：{exc}",
                        "reason": "",
                        "path": str(path), "folder": str(path.parent),
                        "proc": proc_context(),
                    }, 500)
                return self._json({
                    "ok": True, "mode": mode, "diag": diag,
                    "path": str(path), "folder": str(path.parent),
                })

            if len(parts) == 3 and parts[2] == "cover" and method == "GET":
                return self._cover(cid)

            if len(parts) == 3 and parts[2] == "tags" and method == "PUT":
                body = self._body()
                tags = body.get("tags")
                if not isinstance(tags, list):
                    return self._error("tags 必须是数组")
                out = db.set_comic_tags(cid, tags)
                sidecar.sync(cid, delay=0.4)
                # 标签弹窗里可能顺手输入了新标签，目录也要跟着更新
                try:
                    taglib.export_all()
                except Exception:
                    pass
                return self._json({"ok": True, "tags": out, "allTags": db.list_tags()})

            if len(parts) == 4 and parts[2] == "page" and method == "GET":
                try:
                    index = int(parts[3])
                except ValueError:
                    return self._error("页码格式不正确")
                return self._page(cid, index)

            if len(parts) == 4 and parts[2] == "thumb" and method == "GET":
                try:
                    index = int(parts[3])
                except ValueError:
                    return self._error("页码格式不正确")
                return self._thumb(cid, index)

        if parts == ["series"] and method == "GET":
            # 不带 name：系列目录（含封面与作品数）；带 name：这个系列里的作品，按顺序
            name = (qs.get("name") or [""])[0]
            if not name:
                items = db.list_series()
                return self._json({"ok": True, "items": items, "total": len(items)})
            rows = db.series_comics(name)
            return self._json({
                "ok": True,
                "name": db.normalize_series(name),
                "items": [comic_json(r) for r in rows],
                "total": len(rows),
            })

        if parts == ["series", "order"] and method == "POST":
            body = self._body()
            name = body.get("name")
            ids = body.get("ids")
            if not isinstance(ids, list):
                return self._error("ids 必须是数组")
            count = db.reorder_series(str(name or ""), ids)
            for cid in db.comics_with_series(str(name or "")):
                try:
                    sidecar.sync(cid, delay=0.6)
                except Exception:
                    pass
            return self._json({"ok": True, "updated": count})

        if parts == ["series", "rename"] and method == "POST":
            body = self._body()
            res = db.rename_series(str(body.get("from") or ""), str(body.get("to") or ""))
            if not res["moved"]:
                return self._error("系列不存在或新名称为空", 404)
            for cid in db.comics_with_series(res["to"]):
                try:
                    sidecar.sync(cid, delay=0.6)
                except Exception:
                    pass
            return self._json({"ok": True, **res})

        if parts == ["series"] and method == "DELETE":
            name = (qs.get("name") or [""])[0]
            affected = db.comics_with_series(name)
            count = db.delete_series(name)
            for cid in affected:
                try:
                    sidecar.sync(cid, delay=0.6)
                except Exception:
                    pass
            return self._json({"ok": True, "removed": count})

        if parts == ["tags", "clear"] and method == "POST":
            res = taglib.clear_all()
            return self._json({
                "ok": True,
                "removed": res["tags"],
                "files": res["files"],
                "tags": db.list_tags(),
                "tagFile": taglib.status(),
            })

        if parts == ["tags"] and method == "GET":
            return self._json({"ok": True, "tags": db.list_tags()})

        if parts == ["tags"] and method == "POST":
            body = self._body()
            name = str(body.get("name") or "").strip()
            if not name:
                return self._error("标签名不能为空")
            tag = db.create_tag(
                name[:60],
                str(body.get("color") or ""),
                str(body.get("display") or ""),
            )
            taglib.export_all()
            return self._json({"ok": True, "tag": tag, "tags": db.list_tags()})

        if parts == ["tags"] and method == "PUT":
            body = self._body()
            name = (qs.get("name") or [""])[0] or str(body.get("oldName") or "")
            if not name:
                return self._error("缺少标签名")
            # 改名 / 改显示名 / 改色都会写进每本的旁挂文件，先记下受影响的作品
            affected = db.comics_with_tag(name)
            try:
                ok = db.update_tag(
                    name,
                    body.get("name") or body.get("newName"),
                    body.get("color"),
                    body.get("display"),
                )
            except Exception as exc:
                return self._error(f"改名失败，可能已存在同名标签：{exc}", 400)
            if not ok:
                return self._error("标签不存在", 404)
            taglib.export_all()
            # 让打了这个标签的作品跟着变：刷新修改时间并重写旁挂文件，
            # 否则下次扫描会把旧名字从 json 里读回来
            for cid in affected:
                try:
                    db.touch_meta(cid)
                    sidecar.sync(cid, delay=0.4)
                except Exception:
                    pass
            return self._json({"ok": True, "tags": db.list_tags(), "updated": len(affected)})

        if parts == ["tags"] and method == "DELETE":
            name = (qs.get("name") or [""])[0]
            if not name:
                return self._error("缺少标签名")
            # 先记下哪些作品打着这个标签，删完把它们的旁挂文件一起清掉
            affected = db.comics_with_tag(name)
            ok = db.delete_tag(name)
            if not ok:
                return self._error("标签不存在", 404)
            taglib.export_all()
            for cid in affected:
                try:
                    db.touch_meta(cid)
                    sidecar.sync(cid, delay=0.4)
                except Exception:
                    pass
            return self._json({"ok": True, "tags": db.list_tags(), "updated": len(affected)})

        if parts == ["settings"] and method == "GET":
            cfg = config.load_config()
            return self._json({
                "ok": True,
                "settings": cfg,
                "libraryExists": Path(cfg["library_path"]).exists(),
                "tagFile": taglib.status(),
            })

        if parts == ["settings"] and method == "PUT":
            body = self._body()
            patch: Dict[str, Any] = {}
            if "library_path" in body:
                raw = str(body["library_path"]).strip().strip('"')
                if not raw:
                    return self._error("路径不能为空")
                p = Path(os.path.expandvars(os.path.expanduser(raw)))
                if not p.is_absolute():
                    return self._error("请填写完整的绝对路径，例如 D:\\Comics")
                patch["library_path"] = str(p)
            for key in ("theme", "default_reader_mode"):
                if key in body:
                    patch[key] = body[key]
            if "sidecar" in body:
                patch["sidecar"] = bool(body["sidecar"])
            if not patch:
                return self._error("没有需要保存的设置")
            cfg = config.save_config(patch)
            rescan = bool(body.get("rescan"))
            if rescan and Path(cfg["library_path"]).exists():
                scanner.scan_async(force=False)
            elif cfg.get("sidecar") and "sidecar" in patch:
                # 刚开启旁挂文件：后台把已有标签一次性写到作品旁边
                threading.Thread(target=sidecar.sync_all, daemon=True).start()
            return self._json({
                "ok": True,
                "settings": cfg,
                "libraryExists": Path(cfg["library_path"]).exists(),
                "scanStarted": rescan,
            })

        if parts == ["sidecar", "sync"] and method == "POST":
            body = self._body()
            written = sidecar.sync_all(force=bool(body.get("force")))
            tag_written = taglib.export_all(force=True)
            return self._json({
                "ok": True,
                "enabled": sidecar.enabled(),
                "written": written,
                "tagWritten": tag_written,
                "tagFile": taglib.status(),
                "total": db.stats()["total"],
            })

        if parts == ["system", "pick-folder"] and method == "POST":
            return self._json({"ok": True, "path": pick_folder(str(config.library_path()))})

        if parts == ["system", "reveal"] and method == "POST":
            lib = str(config.library_path())
            try:
                reveal_folder(lib)
            except RevealError as exc:
                return self._json({
                    "ok": False, "error": str(exc), "reason": exc.reason, "path": lib,
                }, 500)
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc), "path": lib}, 500)
            return self._json({"ok": True})

        if parts == ["cache", "clear"] and method == "POST":
            archive.clear_page_cache()
            archive.clear_bulk_cache()
            return self._json({"ok": True})

        return self._error("接口不存在", 404)

    # ------------------------------------------------------------ 图片
    def _comic_file(self, cid: str) -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
        row = db.get_comic(cid)
        if not row:
            return None, None
        path = (config.library_path() / row["rel_path"]).resolve()
        if not config.is_inside_library(path) or not path.is_file():
            # 文件不在了：踢一次扫描让书架统一清理，别让 ghost 记录一直挂着
            kick_scan()
            return row, None
        return row, path

    def _cover(self, cid: str) -> None:
        row, _ = self._comic_file(cid)
        if not row:
            return self._error("漫画不存在", 404)
        cover = row.get("cover_file")
        if cover:
            p = config.COVER_DIR / cover
            if p.is_file():
                return self._bytes(p.read_bytes(), "image/jpeg", "private, max-age=86400")
        # 没有缩略图时退回包内首图
        p = thumbs.cover_path(cid)
        if p.is_file():
            return self._bytes(p.read_bytes(), "image/jpeg", "private, max-age=86400")
        return self._error("封面尚未生成", 404)

    def _page(self, cid: str, index: int) -> None:
        row, path = self._comic_file(cid)
        if not row:
            return self._error("漫画不存在", 404)
        if path is None:
            return self._error("漫画文件已被移动或删除", 410)
        total = row.get("page_count") or 0
        if index < 0 or (total and index >= total):
            return self._error("页码超出范围", 404)
        try:
            data, mime = archive.read_page(path, index)
        except archive.SourceError as exc:
            return self._error(str(exc), 500)
        except Exception as exc:
            return self._error(f"读取页面失败：{exc}", 500)
        self._bytes(data, mime, "private, max-age=86400")

    def _thumb(self, cid: str, index: int) -> None:
        """详情页预览图：生成后落盘缓存，之后直接读小图。"""
        row, path = self._comic_file(cid)
        if not row:
            return self._error("漫画不存在", 404)
        total = row.get("page_count") or 0
        if index < 0 or (total and index >= total):
            return self._error("页码超出范围", 404)

        cached = thumbs.page_thumb_path(cid, index)
        if cached.is_file():
            return self._bytes(cached.read_bytes(), "image/jpeg", "private, max-age=86400")
        if path is None:
            return self._error("漫画文件已被移动或删除", 410)

        try:
            data, _mime = archive.read_page(path, index)
            out = thumbs.write_page_thumb(data, cid, index)
        except archive.SourceError as exc:
            return self._error(str(exc), 500)
        except Exception as exc:
            return self._error(f"生成缩略图失败：{exc}", 500)
        if not out or not out.is_file():
            return self._error("缩略图生成失败", 500)
        self._bytes(out.read_bytes(), "image/jpeg", "private, max-age=86400")


def pick_folder(initial: str) -> Optional[str]:
    """调用系统文件夹选择框（失败返回 None，前端可退化为手输路径）。"""
    if sys.platform != "win32":
        return None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(initialdir=initial, title="选择漫画库文件夹", mustexist=False)
        root.destroy()
        return folder or None
    except Exception as exc:
        print(f"[picker] 无法打开文件夹选择框：{exc}", file=sys.stderr)
        return None


def reveal_folder(path: str) -> None:
    """在文件管理器里打开一个目录（设置页的「打开库文件夹」）。"""
    if sys.platform == "win32":
        if is_low_integrity():
            raise RevealError(
                "当前程序以低权限（沙箱）身份运行，没有权限打开资源管理器",
                "low-integrity",
            )
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _win_select_file(path: Path) -> Tuple[bool, int]:
    """让资源管理器定位并选中某个文件，返回 (是否成功, HRESULT)。

    不能直接起 `explorer.exe /select,<路径>`：部分机器上这个新进程会以
    0xC0000142（DLL 初始化失败）弹「应用程序无法正常启动」。改用 Shell API
    把请求交给**已经在运行**的资源管理器，不产生新进程。
    """
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.windll.ole32
    try:
        ole32.CoInitialize(None)  # 该接口建议在 STA 线程调用
    except Exception:
        pass
    try:
        shell32 = ctypes.windll.shell32
        shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
        shell32.ILCreateFromPathW.restype = ctypes.c_void_p
        shell32.ILFree.argtypes = [ctypes.c_void_p]
        shell32.ILFree.restype = None
        shell32.SHOpenFolderAndSelectItems.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong,
        ]
        shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
        pidl = shell32.ILCreateFromPathW(str(path))
        if not pidl:
            return False, -1
        try:
            # cidl=0 且 pidl 指向具体条目：打开它所在目录并选中它
            hr = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
        finally:
            shell32.ILFree(pidl)
        return hr == 0, int(hr)
    except Exception:
        return False, -2
    finally:
        try:
            ole32.CoUninitialize()
        except Exception:
            pass


def proc_context() -> Dict[str, Any]:
    """当前进程的身份：完整性级别 / 是否管理员 / 会话号。

    用来解释「能读盘、却打不开资源管理器」：以低完整性级别（或受限令牌）运行的
    进程没法驱动 shell —— ShellExecute 直接返回 5（拒绝访问），explorer.exe 也
    起不来（0xC0000142）。正常双击启动的程序是 Medium，不会有这个问题。
    """
    info: Dict[str, Any] = {"platform": sys.platform, "pid": os.getpid()}
    if sys.platform != "win32":
        return info
    import ctypes
    from ctypes import wintypes

    adv, k32 = ctypes.windll.advapi32, ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    adv.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    token = wintypes.HANDLE()
    if adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        try:
            size = wintypes.DWORD(0)
            # TokenIntegrityLevel = 25
            adv.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
            if size.value:
                buf = ctypes.create_string_buffer(size.value)
                if adv.GetTokenInformation(token, 25, buf, size, ctypes.byref(size)):
                    # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES Label; }
                    sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
                    count = adv.GetSidSubAuthorityCount(sid)[0]
                    rid = adv.GetSidSubAuthority(sid, count - 1)[0]
                    info["integrity_rid"] = int(rid)
                    info["integrity"] = {
                        0x0000: "untrusted", 0x1000: "low", 0x2000: "medium",
                        0x2100: "medium-plus", 0x3000: "high", 0x4000: "system",
                    }.get(rid, hex(rid))
        except Exception:
            pass
        finally:
            k32.CloseHandle(token)
    try:
        info["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        pass
    try:
        sid = wintypes.DWORD()
        if k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid)):
            info["session"] = int(sid.value)
    except Exception:
        pass
    return info


def is_low_integrity() -> bool:
    """当前进程是否运行在低完整性级别（沙箱 / 受限令牌）下。

    这类进程能读盘、能起普通子进程，但**驱不动 shell**：SHOpenFolderAndSelectItems
    返回拒绝访问，ShellExecute / startfile 也都会被拒；而一旦尝试自己
    CreateProcess 一个 explorer.exe，那个新进程会以 0xC0000142（DLL 初始化失败）
    弹「应用程序无法正常启动」。所以低完整性下不要做任何尝试，直接认输。
    """
    rid = proc_context().get("integrity_rid")
    return isinstance(rid, int) and rid < 0x2000


class RevealError(RuntimeError):
    """打开文件夹失败，带上原因分类和诊断信息，好让前端决定怎么兜底。"""

    def __init__(self, msg: str, reason: str = "", diag: Optional[Dict[str, Any]] = None):
        super().__init__(msg)
        self.reason = reason          # "low-integrity" | "denied" | ""
        self.diag = diag or {}


def reveal_file(path: Path) -> Tuple[str, Dict[str, Any]]:
    """在文件管理器里定位到具体文件。

    返回 ("selected" | "folder", 诊断信息)。只用两种不会另起 explorer 进程的方式：
      1. Shell 接口 SHOpenFolderAndSelectItems（打开目录并选中文件）
      2. os.startfile(所在目录)（走 ShellExecute，由已在运行的 shell 处理）
    绝不显式启动 explorer.exe —— 低完整性进程起它会 0xC0000142。
    """
    diag: Dict[str, Any] = {}
    if sys.platform == "win32":
        ok, hr = _win_select_file(path)
        diag["select_hr"] = hr
        if ok:
            return "selected", diag

        if is_low_integrity():
            # 已经被降权：再试任何一条路都只会弹崩溃框，直接告诉调用方换个办法
            diag["reason"] = "low-integrity"
            raise RevealError(
                "当前程序以低权限（沙箱）身份运行，没有权限打开资源管理器",
                "low-integrity", diag,
            )

        # 退一步：只打开所在目录（不指定文件）
        try:
            os.startfile(str(path.parent))  # type: ignore[attr-defined]
            return "folder", diag
        except OSError as exc:
            diag["startfile"] = f"{exc}"
            diag["proc"] = proc_context()
            if getattr(exc, "winerror", None) == 5:
                diag["reason"] = "denied"
                raise RevealError(
                    "系统拒绝访问（WinError 5）：当前进程没有权限驱动资源管理器",
                    "denied", diag,
                ) from exc
            raise
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
        return "selected", diag
    subprocess.Popen(["xdg-open", str(path.parent)])
    return "folder", diag


class QuietServer(ThreadingHTTPServer):
    """浏览器提前关闭连接（切页/关窗）时会抛 ConnectionResetError，
    这属于正常现象，不用把整段堆栈刷到控制台。"""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def build_server(host: str, port: int) -> ThreadingHTTPServer:
    httpd = QuietServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
