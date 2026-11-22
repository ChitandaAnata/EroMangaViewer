"""全局路径与配置管理。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    # PyInstaller 单文件版：数据放 exe 旁边（可安装到 %LOCALAPPDATA%，
    # 便携版则跟着 exe 走），网页资源从临时解包目录读。
    BASE_DIR = Path(sys.executable).resolve().parent
_env_home = os.environ.get("MANGASHELF_HOME")
if _env_home:
    BASE_DIR = Path(_env_home).expanduser().resolve()

# 打包后 web/ 在 _MEIPASS 里；源码运行时就在项目根目录。
RES_DIR = Path(getattr(sys, "_MEIPASS", str(BASE_DIR)))

DATA_DIR = BASE_DIR / "data"
# 桌面版装在 C:\Program Files 这类只读目录时，外壳会把数据目录指到用户目录，
# 通过 MANGASHELF_DATA 传进来；没传就还是程序目录旁边的 data（网页端直接跑 app.py 时）。
_env_data = os.environ.get("MANGASHELF_DATA")
if _env_data:
    DATA_DIR = Path(_env_data).expanduser().resolve()
# 桌面版把 web/ 放在程序根目录（和 backend 平级），这样改前端不用重新打包；
# 找不到再退回 PyInstaller 的解包目录。
WEB_DIR = BASE_DIR / "web"
if not WEB_DIR.is_dir():
    WEB_DIR = RES_DIR / "web"
DB_PATH = DATA_DIR / "library.db"
COVER_DIR = DATA_DIR / "covers"
PAGE_DIR = DATA_DIR / "pages"
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "app.log"

DEFAULT_CONFIG: Dict[str, Any] = {
    "library_path": str((BASE_DIR / "sample_library").resolve()),
    "host": "127.0.0.1",
    "port": 8765,
    "theme": "dark",
    "cover_width": 480,
    "page_thumb_width": 400,
    "default_reader_mode": "paged",
    "open_browser": True,
    # 把标签/进度写到漫画旁的 xxx.cbz.json，跟着文件走
    "sidecar": True,
}

_cache: Dict[str, Any] | None = None


def ensure_dirs() -> None:
    for d in (DATA_DIR, COVER_DIR, PAGE_DIR, WEB_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # 临时目录也放到 data 下：有的机器 %TEMP% 对普通用户不可写，
    # 解压、插图解码这些一写临时文件就会失败。
    temp = DATA_DIR / "temp"
    try:
        temp.mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = str(temp)
        os.environ["TMP"] = str(temp)
    except OSError:
        pass


def load_config() -> Dict[str, Any]:
    """读取配置，缺失字段用默认值补齐。"""
    global _cache
    if _cache is not None:
        return _cache

    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in cfg:
                        cfg[k] = v
        except Exception as exc:  # 配置损坏时不应阻止启动
            print(f"[config] 读取失败，使用默认配置: {exc}", file=sys.stderr)
    _cache = cfg
    return cfg


def save_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """合并保存配置。"""
    global _cache
    cfg = dict(load_config())
    for k, v in patch.items():
        if k in DEFAULT_CONFIG:
            cfg[k] = v
    ensure_dirs()
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _cache = cfg
    return cfg


def library_path() -> Path:
    return Path(load_config()["library_path"]).expanduser()


def is_inside_library(path: Path) -> bool:
    """确认目标文件确实位于漫画库内，避免路径穿越读到任意文件。"""
    try:
        root = library_path().resolve()
        path.resolve().relative_to(root)
        return True
    except Exception:
        return False
