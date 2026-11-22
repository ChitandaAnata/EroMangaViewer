"""SQLite 存储层：漫画元数据、标签、阅读进度。"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS comics (
    id            TEXT PRIMARY KEY,
    rel_path      TEXT UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    kind          TEXT,
    size          INTEGER DEFAULT 0,
    mtime         REAL DEFAULT 0,
    page_count    INTEGER DEFAULT 0,
    cover_file    TEXT,
    cover_ver     TEXT DEFAULT '',
    category      TEXT DEFAULT '',
    status        TEXT DEFAULT '',
    favorite      INTEGER DEFAULT 0,
    series        TEXT DEFAULT '',
    series_order  INTEGER DEFAULT 0,
    last_page     INTEGER DEFAULT 0,
    added_at      REAL DEFAULT 0,
    updated_at    REAL DEFAULT 0,
    meta_updated_at REAL DEFAULT 0,
    error         TEXT
);
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    ns         TEXT DEFAULT '',
    display    TEXT DEFAULT '',
    color      TEXT DEFAULT '',
    created_at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS comic_tags (
    comic_id TEXT NOT NULL,
    tag_id   INTEGER NOT NULL,
    PRIMARY KEY (comic_id, tag_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_comic_tags_tag ON comic_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_comics_title ON comics(title);
"""

TAG_PALETTE = [
    "#ff5f7e", "#ff9f43", "#ffd166", "#4ecdc4", "#5b8def",
    "#a06bff", "#f368e0", "#2fd39a", "#ff7b54", "#7f8cff",
]

# 标签分类：存储用命名空间（如 language:chinese），界面显示中文分类名 + 显示名
TAG_NAMESPACES = [
    ("language", "语言"),
    ("parody", "原作"),
    ("character", "角色"),
    ("artist", "艺术家"),
    ("group", "社团"),
    ("female", "女性"),
    ("male", "男性"),
    ("mixed", "混合"),
    ("other", "其他"),
]
TAG_NS_LABEL = dict(TAG_NAMESPACES)
TAG_NS_ORDER = {ns: i for i, (ns, _) in enumerate(TAG_NAMESPACES)}

# 允许用户用别名书写分类前缀，统一归一化成规范名
TAG_NS_ALIASES = {
    "language": "language", "lang": "language", "languages": "language",
    "parody": "parody", "series": "parody", "original": "parody", "origin": "parody",
    "character": "character", "char": "character", "chara": "character", "role": "character",
    "artist": "artist", "author": "artist", "art": "artist", "illustrator": "artist",
    # 社团统一用 group，circle / club / publisher 都是旧写法
    "group": "group", "circle": "group", "club": "group", "publisher": "group",
    "female": "female", "females": "female",
    "male": "male", "males": "male",
    # 不带前缀的普通标签归「混合」；显式 other:/misc:/tag: 归「其他」
    "mixed": "mixed",
    "other": "other", "misc": "other", "tag": "other",
}

# 常见取值的默认显示名；用户也可以给任意标签单独设显示名
TAG_DISPLAY = {
    ("language", "chinese"): "汉语",
    ("language", "zh"): "汉语",
    ("language", "zh-cn"): "简体中文",
    ("language", "zh-tw"): "繁体中文",
    ("language", "simplified"): "简体",
    ("language", "traditional"): "繁体",
    ("language", "japanese"): "日语",
    ("language", "ja"): "日语",
    ("language", "english"): "英语",
    ("language", "en"): "英语",
    ("language", "korean"): "韩语",
    ("language", "ko"): "韩语",
    ("language", "cantonese"): "粤语",
    ("language", "spanish"): "西班牙语",
    ("language", "es"): "西班牙语",
    ("language", "french"): "法语",
    ("language", "fr"): "法语",
    ("language", "german"): "德语",
    ("language", "de"): "德语",
    ("language", "russian"): "俄语",
    ("language", "ru"): "俄语",
    ("language", "thai"): "泰语",
    ("language", "vietnamese"): "越南语",
    ("language", "indonesian"): "印尼语",
    ("language", "portuguese"): "葡萄牙语",
    ("language", "translated"): "翻译",
    ("language", "raw"): "生肉",
}


# 作品分类：描述作品本身属于哪一类（与标签无关）
COMIC_CATEGORIES = [
    ("dojinshi", "Dojinshi"),
    ("manga", "Manga"),
    ("artistcg", "ArtistCG"),
    ("gamecg", "GameCG"),
    ("non-h", "Non-H"),
    ("imageset", "ImageSet"),
]
COMIC_CAT_LABEL = dict(COMIC_CATEGORIES)
COMIC_CAT_ALIASES = {
    "dojinshi": "dojinshi", "doujinshi": "dojinshi", "doujin": "dojinshi", "dojin": "dojinshi",
    "manga": "manga", "comic": "manga",
    "artistcg": "artistcg", "artist cg": "artistcg", "artcg": "artistcg", "cg": "artistcg",
    "gamecg": "gamecg", "game cg": "gamecg",
    "non-h": "non-h", "nonh": "non-h", "non h": "non-h", "non-hentai": "non-h", "safe": "non-h",
    "imageset": "imageset", "image set": "imageset", "image-set": "imageset",
}


def normalize_category(value: Any) -> str:
    """把用户输入归一化成分类键；认不出来的一律当「未分类」（空串）。"""
    key = str(value or "").strip().lower().replace("_", " ")
    if not key:
        return ""
    if key in COMIC_CAT_LABEL:
        return key
    return COMIC_CAT_ALIASES.get(key, "")


def normalize_status(value: Any) -> str:
    """更新状态只认两种：空串=更新完毕，updating=更新中。"""
    key = str(value or "").strip().lower().replace("-", "").replace("_", "")
    if key in ("updating", "ongoing", "wip", "incomplete", "更新中", "连载中"):
        return "updating"
    return ""


def category_options() -> List[Dict[str, str]]:
    return [{"key": k, "label": v} for k, v in COMIC_CATEGORIES]


def split_tag_name(name: str) -> Tuple[str, str, str]:
    """把标签拆成 (规范名, 分类, 值)。

    ``language:chinese`` → ("language:chinese", "language", "chinese")
    ``lang:chinese``     → ("language:chinese", "language", "chinese")
    ``circle:x``         → ("group:x", "group", "x")      （circle 是社团的旧写法）
    ``other:x``          → ("other:x", "other", "x")      （显式归「其他」）
    ``misc:x``           → ("other:x", "other", "x")
    ``热血``             → ("mixed:热血", "mixed", "热血")  （没写分类的进「混合」）
    """
    raw = (name or "").strip()
    if not raw:
        return "", "", ""
    if ":" in raw:
        head, rest = raw.split(":", 1)
        key = head.strip().lower()
        value = rest.strip()
        if value and key in TAG_NS_ALIASES:
            ns = TAG_NS_ALIASES[key]
            return f"{ns}:{value}", ns, value
    if raw.lower().startswith("mixed:"):
        return raw, "mixed", raw.split(":", 1)[1].strip()
    return f"mixed:{raw}", "mixed", raw


def tag_display(ns: str, value: str, explicit: str = "") -> str:
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    hit = TAG_DISPLAY.get((ns, (value or "").strip().lower()))
    return hit or (value or "")


def tag_view(row: Dict[str, Any], count: int = 0) -> Dict[str, Any]:
    """统一的标签输出结构：name 用于存储/筛选，label 用于界面显示。"""
    name, ns, value = split_tag_name(row.get("name") or "")
    stored_ns = row.get("ns") or ""
    if stored_ns:
        ns = stored_ns
        if ":" in name:
            value = name.split(":", 1)[1]
    return {
        "id": row.get("id"),
        "name": name,
        "ns": ns,
        "nsLabel": TAG_NS_LABEL.get(ns, "混合"),
        "label": tag_display(ns, value, row.get("display") or ""),
        "display": row.get("display") or "",
        "value": value,
        "color": row.get("color") or "",
        "count": count,
    }


def tag_sort_key(tag: Dict[str, Any]):
    return (
        TAG_NS_ORDER.get(tag.get("ns") or "", 99),
        -int(tag.get("count") or 0),
        (tag.get("label") or "").lower(),
    )


def make_id(rel_path: str) -> str:
    return hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:16]


def color_for(name: str) -> str:
    return TAG_PALETTE[sum(name.encode("utf-8")) % len(TAG_PALETTE)]


def pick_unused_color(conn: sqlite3.Connection) -> str:
    """优先挑一个还没被占用的调色板颜色，避免多个标签同色难分辨。"""
    used = [r["color"] for r in conn.execute("SELECT color FROM tags").fetchall()]
    counts = {c: used.count(c) for c in TAG_PALETTE}
    return min(TAG_PALETTE, key=lambda c: (counts[c], TAG_PALETTE.index(c)))


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def conn_ctx():
    """带提交/回滚/关闭的连接上下文，避免连接泄漏与嵌套死锁。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- 元信息
def get_meta(key: str, default: str = "") -> str:
    with conn_ctx() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return (row["value"] if row else default) or default


def set_meta(key: str, value: str) -> None:
    with conn_ctx() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def tags_revision() -> int:
    """标签目录的版本号：每次增 / 删 / 改标签都 +1，用来和库根的清单比新旧。"""
    try:
        return int(get_meta("tags_revision", "0") or 0)
    except ValueError:
        return 0


def set_tags_revision(value: int) -> None:
    set_meta("tags_revision", str(int(value)))


def bump_tags_revision() -> int:
    """标签目录有变动时调用，返回新版本号。"""
    with conn_ctx() as conn:
        return _bump_rev(conn)


def _bump_rev(conn: sqlite3.Connection) -> int:
    """在已有事务内 +1，避免另开连接自锁。"""
    row = conn.execute("SELECT value FROM meta WHERE key='tags_revision'").fetchone()
    try:
        cur = int((row["value"] if row else 0) or 0)
    except ValueError:
        cur = 0
    nxt = cur + 1
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('tags_revision', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(nxt),),
    )
    return nxt


def init_db() -> None:
    with conn_ctx() as conn:
        conn.executescript(SCHEMA)
        ccols = {r["name"] for r in conn.execute("PRAGMA table_info(comics)").fetchall()}
        if "meta_updated_at" not in ccols:
            # 标签/进度最后修改时间，用来和旁挂 .json 比新旧
            conn.execute("ALTER TABLE comics ADD COLUMN meta_updated_at REAL DEFAULT 0")
        if "category" not in ccols:
            # 作品分类（Dojinshi / Manga / ArtistCG / …），空串表示未分类
            conn.execute("ALTER TABLE comics ADD COLUMN category TEXT DEFAULT ''")
        if "status" not in ccols:
            # 更新状态：空串=更新完毕，updating=更新中
            conn.execute("ALTER TABLE comics ADD COLUMN status TEXT DEFAULT ''")
        if "favorite" not in ccols:
            # 喜爱标记：1=Favorite
            conn.execute("ALTER TABLE comics ADD COLUMN favorite INTEGER DEFAULT 0")
        if "series" not in ccols:
            # 系列名（空串=不属于任何系列）。同一系列的作品共用一个名字
            conn.execute("ALTER TABLE comics ADD COLUMN series TEXT DEFAULT ''")
        if "series_order" not in ccols:
            # 系列内顺序，从 1 开始；0 表示没排过（按标题兜底）
            conn.execute("ALTER TABLE comics ADD COLUMN series_order INTEGER DEFAULT 0")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tags)").fetchall()}
        if "ns" not in cols:
            conn.execute("ALTER TABLE tags ADD COLUMN ns TEXT DEFAULT ''")
        if "display" not in cols:
            conn.execute("ALTER TABLE tags ADD COLUMN display TEXT DEFAULT ''")
        # 老标签回填：把别名 / 旧分类前缀统一成规范名（circle→group，无前缀→mixed:）
        for row in conn.execute("SELECT id, name, ns FROM tags").fetchall():
            canon, ns, _ = split_tag_name(row["name"])
            cur_ns = row["ns"] or ""
            if canon == row["name"] and cur_ns == ns:
                continue
            clash = conn.execute(
                "SELECT id, display, color FROM tags WHERE name=? AND id<>?", (canon, row["id"])
            ).fetchone()
            if clash:
                # 归一化后撞名了：把作品关联挪到已有那条上，再删掉重复的旧行
                conn.execute(
                    "INSERT OR IGNORE INTO comic_tags (comic_id, tag_id) "
                    "SELECT comic_id, ? FROM comic_tags WHERE tag_id=?",
                    (clash["id"], row["id"]),
                )
                conn.execute("DELETE FROM comic_tags WHERE tag_id=?", (row["id"],))
                old = conn.execute(
                    "SELECT display, color FROM tags WHERE id=?", (row["id"],)
                ).fetchone()
                new_display = (clash["display"] or "") or ((old["display"] if old else "") or "")
                new_color = (clash["color"] or "") or ((old["color"] if old else "") or "")
                if new_display != (clash["display"] or "") or new_color != (clash["color"] or ""):
                    conn.execute(
                        "UPDATE tags SET display=?, color=? WHERE id=?",
                        (new_display, new_color, clash["id"]),
                    )
                conn.execute("DELETE FROM tags WHERE id=?", (row["id"],))
            else:
                conn.execute("UPDATE tags SET name=?, ns=? WHERE id=?", (canon, ns, row["id"]))


# ---------------------------------------------------------------- 漫画
def upsert_comic(rec: Dict[str, Any]) -> str:
    """插入或更新漫画记录，保留已有的阅读进度和标签。"""
    now = time.time()
    cid = rec.get("id") or make_id(rec["rel_path"])
    with conn_ctx() as conn:
        row = conn.execute("SELECT id, added_at FROM comics WHERE rel_path=?", (rec["rel_path"],)).fetchone()
        if row:
            conn.execute(
                """UPDATE comics SET title=?, kind=?, size=?, mtime=?, page_count=?,
                   cover_file=?, cover_ver=?, updated_at=?, error=? WHERE id=?""",
                (
                    rec["title"], rec.get("kind", ""), rec.get("size", 0), rec.get("mtime", 0),
                    rec.get("page_count", 0), rec.get("cover_file"), rec.get("cover_ver", ""),
                    now, rec.get("error"), row["id"],
                ),
            )
            return row["id"]
        conn.execute(
            """INSERT INTO comics (id, rel_path, title, kind, size, mtime, page_count,
               cover_file, cover_ver, last_page, added_at, updated_at, error)
               VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?)""",
            (
                cid, rec["rel_path"], rec["title"], rec.get("kind", ""), rec.get("size", 0),
                rec.get("mtime", 0), rec.get("page_count", 0), rec.get("cover_file"),
                rec.get("cover_ver", ""), now, now, rec.get("error"),
            ),
        )
        return cid


def all_paths() -> Dict[str, Dict[str, Any]]:
    with conn_ctx() as conn:
        rows = conn.execute("SELECT id, rel_path, size, mtime, cover_file FROM comics").fetchall()
    return {r["rel_path"]: dict(r) for r in rows}


def all_ids() -> List[str]:
    with conn_ctx() as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM comics").fetchall()]


def delete_comic(rel_path: str) -> Optional[str]:
    with conn_ctx() as conn:
        row = conn.execute("SELECT id, cover_file FROM comics WHERE rel_path=?", (rel_path,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM comic_tags WHERE comic_id=?", (row["id"],))
        conn.execute("DELETE FROM comics WHERE id=?", (row["id"],))
        return row["cover_file"]


def delete_comic_by_id(cid: str) -> Optional[str]:
    with conn_ctx() as conn:
        row = conn.execute("SELECT cover_file FROM comics WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM comic_tags WHERE comic_id=?", (cid,))
        conn.execute("DELETE FROM comics WHERE id=?", (cid,))
        return row["cover_file"]


def get_comic(cid: str) -> Optional[Dict[str, Any]]:
    with conn_ctx() as conn:
        row = conn.execute("SELECT * FROM comics WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        data = dict(row)
        tags = conn.execute(
            """SELECT t.id, t.name, t.ns, t.display, t.color FROM tags t
               JOIN comic_tags ct ON ct.tag_id=t.id
               WHERE ct.comic_id=? ORDER BY t.name""",
            (cid,),
        ).fetchall()
    data["tags"] = sorted((tag_view(dict(t)) for t in tags), key=tag_sort_key)
    return data


def list_comics(
    tag: Optional[Any] = None,
    query: str = "",
    sort: str = "recent",
) -> List[Dict[str, Any]]:
    """列出作品。

    ``tag`` 可以是单个标签名，也可以是标签名列表；多个标签时取交集
    （同时拥有这些标签的作品）。

    ``query`` 按空白拆成多个关键词，每个词都要命中书名或相对路径，
    多个词之间取交集；连续空格和全角空格都当作分隔符。
    标签不通过 ``query`` 匹配，要走 ``tag`` 参数（联想点选 / 侧栏勾选）。
    """
    sql = "SELECT c.* FROM comics c"
    params: List[Any] = []
    where = []

    raw_tags = tag if isinstance(tag, (list, tuple)) else ([tag] if tag else [])
    canon_tags: List[str] = []
    for item in raw_tags:
        canon = split_tag_name(str(item))[0] or str(item)
        if canon and canon not in canon_tags:
            canon_tags.append(canon)
    if canon_tags:
        marks = ",".join("?" for _ in canon_tags)
        # 交集：按 comic 分组后，命中的标签数必须等于要筛的标签数
        where.append(
            f"c.id IN (SELECT ct.comic_id FROM comic_tags ct JOIN tags t ON t.id=ct.tag_id "
            f"WHERE t.name IN ({marks}) GROUP BY ct.comic_id HAVING COUNT(DISTINCT t.name)=?)"
        )
        params.extend(canon_tags)
        params.append(len(canon_tags))
    if query:
        # 空格分隔多个关键词，取交集（AND）：每个词都要命中书名或路径。
        # 标签不在这里匹配——输入 "chi" 但没点联想时，不能因为 artist:noripachi
        # 含 chi 就把书名里没有 chi 的作品搜出来；要按标签筛请走 tag 参数。
        for word in str(query).split():
            where.append("(c.title LIKE ? OR c.rel_path LIKE ?)")
            like = f"%{word}%"
            params.extend([like, like])
    if where:
        sql += " WHERE " + " AND ".join(where)

    order = {
        "recent": "c.added_at DESC",
        "title": "c.title COLLATE NOCASE ASC",
        "pages": "c.page_count DESC",
        "progress": "c.updated_at DESC",
    }.get(sort, "c.added_at DESC")
    sql += f" ORDER BY {order}"

    with conn_ctx() as conn:
        rows = conn.execute(sql, params).fetchall()
        tag_rows = conn.execute(
            """SELECT ct.comic_id, t.id, t.name, t.ns, t.display, t.color FROM comic_tags ct
               JOIN tags t ON t.id=ct.tag_id ORDER BY t.name"""
        ).fetchall()

    tag_map: Dict[str, List[Dict[str, Any]]] = {}
    for r in tag_rows:
        tag_map.setdefault(r["comic_id"], []).append(tag_view(dict(r)))
    for cid, tags in tag_map.items():
        tag_map[cid] = sorted(tags, key=tag_sort_key)

    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = tag_map.get(d["id"], [])
        out.append(d)
    return out


def set_last_page(cid: str, page: int, touch: bool = True) -> None:
    now = time.time()
    with conn_ctx() as conn:
        if touch:
            conn.execute(
                "UPDATE comics SET last_page=?, updated_at=?, meta_updated_at=? WHERE id=?",
                (page, now, now, cid),
            )
        else:
            conn.execute("UPDATE comics SET last_page=? WHERE id=?", (page, cid))


def set_category(cid: str, value: Any, touch: bool = True) -> str:
    """设置作品分类，返回归一化后的键（空串表示未分类）。"""
    cat = normalize_category(value)
    now = time.time()
    with conn_ctx() as conn:
        if touch:
            conn.execute(
                "UPDATE comics SET category=?, meta_updated_at=? WHERE id=?", (cat, now, cid)
            )
        else:
            conn.execute("UPDATE comics SET category=? WHERE id=?", (cat, cid))
    return cat


def set_status(cid: str, value: Any, touch: bool = True) -> str:
    """设置更新状态，返回归一化后的值（空串=更新完毕）。"""
    status = normalize_status(value)
    now = time.time()
    with conn_ctx() as conn:
        if touch:
            conn.execute(
                "UPDATE comics SET status=?, meta_updated_at=? WHERE id=?", (status, now, cid)
            )
        else:
            conn.execute("UPDATE comics SET status=? WHERE id=?", (status, cid))
    return status


def set_favorite(cid: str, value: Any, touch: bool = True) -> bool:
    """设置喜爱标记，返回归一化后的布尔值。"""
    fav = 1 if (value is True or str(value).strip().lower() in ("1", "true", "yes", "on")) else 0
    now = time.time()
    with conn_ctx() as conn:
        if touch:
            conn.execute(
                "UPDATE comics SET favorite=?, meta_updated_at=? WHERE id=?", (fav, now, cid)
            )
        else:
            conn.execute("UPDATE comics SET favorite=? WHERE id=?", (fav, cid))
    return bool(fav)


def touch_meta(cid: str, ts: Optional[float] = None) -> None:
    """记录标签/进度的最后修改时间（用于和旁挂文件比新旧）。"""
    with conn_ctx() as conn:
        conn.execute("UPDATE comics SET meta_updated_at=? WHERE id=?", (ts or time.time(), cid))


# ---------------------------------------------------------------- 系列
# 系列就是「一组作品共用一个名字」，存在每本作品自己身上（series / series_order）：
# 旁挂 json 里也带一份，所以整库搬迁或单独拷走一本时，系列归属和顺序都不会丢。
SERIES_MAX_LEN = 80


def normalize_series(value: Any) -> str:
    """系列名归一化：压掉多余空白、去首尾空格、限长。空串表示不属于任何系列。"""
    return " ".join(str(value or "").split())[:SERIES_MAX_LEN]


def _next_series_order(conn: sqlite3.Connection, name: str, exclude: str = "") -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(series_order), 0) FROM comics WHERE series=? AND id<>?",
        (name, exclude),
    ).fetchone()
    return int(row[0] or 0) + 1


def set_series(
    cid: str,
    value: Any,
    order: Optional[int] = None,
    touch: bool = True,
) -> Dict[str, Any]:
    """把某本作品放进 / 移出系列。

    ``order`` 为 None 时追加到系列末尾；给 ``""`` 表示移出系列。
    返回 ``{"series": 系列名, "order": 序号}``。
    """
    name = normalize_series(value)
    now = time.time()
    with conn_ctx() as conn:
        cur = conn.execute(
            "SELECT series, series_order FROM comics WHERE id=?", (cid,)
        ).fetchone()
        if cur is None:
            return {"series": "", "order": 0}
        old = cur["series"] or ""
        if not name:
            new_order = 0
        elif order is not None:
            new_order = max(1, int(order))
        elif name == old and int(cur["series_order"] or 0) > 0:
            # 已经在这个系列里，又没要求换位置：原地不动
            new_order = int(cur["series_order"])
        else:
            new_order = _next_series_order(conn, name, cid)

        if touch:
            conn.execute(
                "UPDATE comics SET series=?, series_order=?, meta_updated_at=? WHERE id=?",
                (name, new_order, now, cid),
            )
        else:
            conn.execute(
                "UPDATE comics SET series=?, series_order=? WHERE id=?",
                (name, new_order, cid),
            )
    return {"series": name, "order": new_order}


def list_series() -> List[Dict[str, Any]]:
    """所有系列 + 作品数 + 第一本的封面（按系列内顺序）。"""
    with conn_ctx() as conn:
        rows = conn.execute(
            """SELECT series AS name, COUNT(*) AS count
                 FROM comics WHERE series<>'' GROUP BY series ORDER BY series COLLATE NOCASE"""
        ).fetchall()
        out = []
        for r in rows:
            first = conn.execute(
                """SELECT id, title, cover_ver, mtime FROM comics WHERE series=?
                   ORDER BY CASE WHEN series_order>0 THEN series_order ELSE 999999 END,
                            title COLLATE NOCASE LIMIT 1""",
                (r["name"],),
            ).fetchone()
            out.append({
                "name": r["name"],
                "count": int(r["count"] or 0),
                "coverId": first["id"] if first else "",
                "cover": f"/api/comics/{first['id']}/cover?v={first['cover_ver'] or int(first['mtime'] or 0)}" if first else "",
                "firstTitle": first["title"] if first else "",
            })
        return out


def series_comics(name: str) -> List[Dict[str, Any]]:
    """系列内作品，按顺序排；没排过序的按标题兜底放最后。"""
    name = normalize_series(name)
    if not name:
        return []
    sql = """SELECT c.* FROM comics c WHERE c.series=?
             ORDER BY CASE WHEN c.series_order>0 THEN c.series_order ELSE 999999 END,
                      c.title COLLATE NOCASE"""
    with conn_ctx() as conn:
        rows = conn.execute(sql, (name,)).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        marks = ",".join("?" for _ in ids)
        tag_rows = conn.execute(
            f"""SELECT ct.comic_id, t.id, t.name, t.ns, t.display, t.color FROM comic_tags ct
                JOIN tags t ON t.id=ct.tag_id WHERE ct.comic_id IN ({marks}) ORDER BY t.name""",
            ids,
        ).fetchall()
    tag_map: Dict[str, List[Dict[str, Any]]] = {}
    for r in tag_rows:
        tag_map.setdefault(r["comic_id"], []).append(tag_view(dict(r)))
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = sorted(tag_map.get(d["id"], []), key=tag_sort_key)
        out.append(d)
    return out


def reorder_series(name: str, ids: Iterable[str]) -> int:
    """按给定顺序重排系列（1 开始连续编号）。返回更新条数。"""
    name = normalize_series(name)
    if not name:
        return 0
    want = [str(i) for i in (ids or []) if i]
    with conn_ctx() as conn:
        members = [
            r["id"] for r in conn.execute(
                "SELECT id FROM comics WHERE series=? ORDER BY CASE WHEN series_order>0 "
                "THEN series_order ELSE 999999 END, title COLLATE NOCASE",
                (name,),
            ).fetchall()
        ]
        if not members:
            return 0
        seen = set()
        ordered = []
        for cid in want:
            if cid in members and cid not in seen:
                seen.add(cid)
                ordered.append(cid)
        # 没在列表里点到的（并发新增之类）保持原相对顺序排到后面
        ordered += [c for c in members if c not in seen]
        now = time.time()
        for i, cid in enumerate(ordered, start=1):
            conn.execute(
                "UPDATE comics SET series_order=?, meta_updated_at=? WHERE id=?",
                (i, now, cid),
            )
        return len(ordered)


def comics_with_series(name: str) -> List[str]:
    name = normalize_series(name)
    if not name:
        return []
    with conn_ctx() as conn:
        return [
            r["id"] for r in conn.execute(
                "SELECT id FROM comics WHERE series=? ORDER BY series_order", (name,)
            ).fetchall()
        ]


def rename_series(old: str, new: str) -> Dict[str, Any]:
    """系列改名；目标名已存在时合并过去（被合并的排在后面）。"""
    old_name = normalize_series(old)
    new_name = normalize_series(new)
    if not old_name or not new_name or old_name == new_name:
        return {"from": old_name, "to": new_name, "moved": 0}
    with conn_ctx() as conn:
        rows = conn.execute(
            "SELECT id FROM comics WHERE series=? ORDER BY CASE WHEN series_order>0 "
            "THEN series_order ELSE 999999 END, title COLLATE NOCASE",
            (old_name,),
        ).fetchall()
        if not rows:
            return {"from": old_name, "to": new_name, "moved": 0}
        start = _next_series_order(conn, new_name)
        now = time.time()
        for i, r in enumerate(rows):
            conn.execute(
                "UPDATE comics SET series=?, series_order=?, meta_updated_at=? WHERE id=?",
                (new_name, start + i, now, r["id"]),
            )
        return {"from": old_name, "to": new_name, "moved": len(rows)}


def delete_series(name: str) -> int:
    """解散系列：成员全部移出（作品本身不动）。返回受影响条数。"""
    name = normalize_series(name)
    if not name:
        return 0
    now = time.time()
    with conn_ctx() as conn:
        cur = conn.execute(
            "UPDATE comics SET series='', series_order=0, meta_updated_at=? WHERE series=?",
            (now, name),
        )
        return int(cur.rowcount or 0)


def rename_comic(cid: str, title: str) -> None:
    with conn_ctx() as conn:
        conn.execute("UPDATE comics SET title=? WHERE id=?", (title, cid))


def move_comic(old_rel: str, new_rel: str, new_title: str) -> bool:
    """文件被重命名/移动时迁移记录，保住标签与阅读进度。"""
    with conn_ctx() as conn:
        row = conn.execute("SELECT id FROM comics WHERE rel_path=?", (old_rel,)).fetchone()
        if not row:
            return False
        clash = conn.execute("SELECT 1 FROM comics WHERE rel_path=?", (new_rel,)).fetchone()
        if clash:
            return False
        conn.execute(
            "UPDATE comics SET rel_path=?, title=?, updated_at=? WHERE id=?",
            (new_rel, new_title, time.time(), row["id"]),
        )
        return True


# ---------------------------------------------------------------- 标签
def list_tags() -> List[Dict[str, Any]]:
    with conn_ctx() as conn:
        rows = conn.execute(
            """SELECT t.id, t.name, t.ns, t.display, t.color, COUNT(ct.comic_id) AS count
               FROM tags t LEFT JOIN comic_tags ct ON ct.tag_id=t.id
               GROUP BY t.id ORDER BY t.name COLLATE NOCASE"""
        ).fetchall()
    tags = [tag_view(dict(r), int(r["count"] or 0)) for r in rows]
    tags.sort(key=tag_sort_key)
    return tags


def ensure_tag(
    name: str,
    color: str = "",
    display: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """确保标签存在，返回其 id。传入 conn 时复用同一事务，避免自锁。"""
    canon, ns, _ = split_tag_name(name)
    if not canon:
        raise ValueError("标签名不能为空")
    canon = canon[:60]
    if conn is None:
        with conn_ctx() as c:
            return ensure_tag(canon, color, display, c)
    row = conn.execute("SELECT id, display, color FROM tags WHERE name=?", (canon,)).fetchone()
    if row:
        # 已经存在时，只在它还没有显示名 / 颜色时补上（不覆盖用户设过的值）
        tid, old_display, old_color = row["id"], row["display"] or "", row["color"] or ""
        if display and not old_display:
            conn.execute("UPDATE tags SET display=? WHERE id=?", (display[:60], tid))
        if color and not old_color:
            conn.execute("UPDATE tags SET color=? WHERE id=?", (color[:16], tid))
        return tid
    conn.execute(
        "INSERT INTO tags (name, ns, display, color, created_at) VALUES (?,?,?,?,?)",
        (canon, ns, (display or "")[:60], color or pick_unused_color(conn), time.time()),
    )
    _bump_rev(conn)
    return conn.execute("SELECT id FROM tags WHERE name=?", (canon,)).fetchone()["id"]


def create_tag(name: str, color: str = "", display: str = "") -> Dict[str, Any]:
    tid = ensure_tag(name, color, display)
    bump_tags_revision()
    tags = {t["name"]: t for t in list_tags()}
    canon, ns, value = split_tag_name(name)
    return tags.get(canon) or {
        "id": tid, "name": canon, "ns": ns, "nsLabel": TAG_NS_LABEL.get(ns, "混合"),
        "label": tag_display(ns, value, display), "value": value,
        "color": color_for(canon), "count": 0,
    }


def update_tag(
    old_name: str,
    new_name: Optional[str] = None,
    color: Optional[str] = None,
    display: Optional[str] = None,
) -> bool:
    old_name = split_tag_name(old_name)[0] or old_name
    with conn_ctx() as conn:
        row = conn.execute("SELECT id FROM tags WHERE name=?", (old_name,)).fetchone()
        if not row:
            return False
        if new_name and new_name != old_name:
            canon, ns, _ = split_tag_name(new_name)
            conn.execute("UPDATE tags SET name=?, ns=? WHERE id=?", (canon[:60], ns, row["id"]))
        if color is not None:
            conn.execute("UPDATE tags SET color=? WHERE id=?", (color, row["id"]))
        if display is not None:
            conn.execute("UPDATE tags SET display=? WHERE id=?", ((display or "").strip()[:60], row["id"]))
        _bump_rev(conn)
    return True


def tag_import(name: str, display: str = "", color: str = "") -> bool:
    """从库根的标签目录导入一个标签：不存在就建，存在则以文件里的值为准。

    与 ``ensure_tag`` 的区别：这里会覆盖显示名和颜色（目录是权威副本），
    并且不推进 revision（导入方负责把 revision 对齐到文件里的值）。
    返回 True 表示新建了标签。
    """
    canon, ns, _ = split_tag_name(name)
    if not canon:
        return False
    canon = canon[:60]
    with conn_ctx() as conn:
        row = conn.execute("SELECT id, display, color FROM tags WHERE name=?", (canon,)).fetchone()
        if row:
            if display and display[:60] != (row["display"] or ""):
                conn.execute("UPDATE tags SET display=? WHERE id=?", (display[:60], row["id"]))
            if color and color[:16] != (row["color"] or ""):
                conn.execute("UPDATE tags SET color=? WHERE id=?", (color[:16], row["id"]))
            return False
        conn.execute(
            "INSERT INTO tags (name, ns, display, color, created_at) VALUES (?,?,?,?,?)",
            (canon, ns, (display or "")[:60], (color or "")[:16] or pick_unused_color(conn), time.time()),
        )
    return True


def comics_with_tag(name: str) -> List[str]:
    """所有打了这个标签的作品 id（改名/删除后要让它们的旁挂文件跟着变）。"""
    canon = split_tag_name(name)[0] or name
    with conn_ctx() as conn:
        rows = conn.execute(
            """SELECT ct.comic_id FROM comic_tags ct JOIN tags t ON t.id=ct.tag_id
               WHERE t.name=?""",
            (canon,),
        ).fetchall()
    return [r["comic_id"] for r in rows]


def delete_tag(name: str) -> bool:
    name = split_tag_name(name)[0] or name
    with conn_ctx() as conn:
        row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM comic_tags WHERE tag_id=?", (row["id"],))
        conn.execute("DELETE FROM tags WHERE id=?", (row["id"],))
        _bump_rev(conn)
    return True


def clear_tags() -> int:
    """清空整个标签体系（标签定义 + 与作品的关联），revision 归零。

    作品记录和阅读进度不受影响；返回删掉的标签数量。
    """
    with conn_ctx() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()
        n = int(row["c"] if row else 0)
        conn.execute("DELETE FROM comic_tags")
        conn.execute("DELETE FROM tags")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('tags_revision','0') "
            "ON CONFLICT(key) DO UPDATE SET value='0'"
        )
    return n


def set_comic_tags(cid: str, items: Iterable[Any], touch: bool = True) -> List[Dict[str, Any]]:
    """整体覆盖某本作品的标签。

    接受字符串（``language:chinese``）或 ``{"name":..., "display":..., "color":...}`` 对象。
    ``touch=False`` 用于从旁挂文件导入时不刷新修改时间。
    """
    cleaned: List[Tuple[str, str, str]] = []
    seen = set()
    for item in items:
        if isinstance(item, dict):
            raw = str(item.get("name") or "").strip()
            display = str(item.get("display") or "").strip()
            color = str(item.get("color") or "").strip()
        else:
            raw, display, color = str(item or "").strip(), "", ""
        if not raw:
            continue
        canon, _ns, _value = split_tag_name(raw)
        canon = canon[:60]
        if canon in seen:
            continue
        seen.add(canon)
        cleaned.append((canon, display[:60], color[:16]))
    with conn_ctx() as conn:
        conn.execute("DELETE FROM comic_tags WHERE comic_id=?", (cid,))
        for n, display, color in cleaned:
            tid = ensure_tag(n, color=color, display=display, conn=conn)
            conn.execute("INSERT OR IGNORE INTO comic_tags (comic_id, tag_id) VALUES (?,?)", (cid, tid))
        if touch:
            conn.execute("UPDATE comics SET meta_updated_at=? WHERE id=?", (time.time(), cid))
    return list((get_comic(cid) or {}).get("tags", []))


def reconcile_tags() -> None:
    """清掉没有任何漫画引用的空标签。"""
    with conn_ctx() as conn:
        conn.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM comic_tags)"
        )


def stats() -> Dict[str, int]:
    with conn_ctx() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM comics").fetchone()["c"]
        errs = conn.execute("SELECT COUNT(*) AS c FROM comics WHERE error IS NOT NULL AND error<>''").fetchone()["c"]
        pages = conn.execute("SELECT COALESCE(SUM(page_count),0) AS c FROM comics").fetchone()["c"]
    return {"total": total, "errors": errs, "pages": pages}
