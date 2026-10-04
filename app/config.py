"""路径与配置读写。WORKBENCH_HOME 环境变量可重定向数据目录（测试用）。"""
import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    home = os.environ.get("WORKBENCH_HOME")
    base = Path(home) if home else ROOT
    return base / "data"


def db_path() -> Path:
    return data_dir() / "workbench.db"


def config_path() -> Path:
    return data_dir() / "config.json"


IMAGES_DIR_NAME = "images"
BACKUPS_DIR_NAME = "backups"


def images_dir() -> Path:
    return data_dir() / IMAGES_DIR_NAME


def backups_dir() -> Path:
    return data_dir() / BACKUPS_DIR_NAME


def ensure_dirs() -> None:
    for d in (data_dir(), images_dir(), backups_dir()):
        d.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = {
    "api_key": "",
    "model": "glm-4-flash",
    # 各抓取源启用开关（key 与 sources 表一致）
    "sources": {
        "official": True,
        "qwen": True,
        "jiqizhixin": True,
        "qbitai": True,
        "ifanr": True,
        "36kr": True,
        "weibo_hot": True,
        "baidu_hot": True,
        "hackernews": True,
    },
    "style_pref": {
        "tone": "轻松接地气",
        "extra": "",
    },
}

SOURCE_NAMES = {
    "official": ("官方源（大模型公司博客）", "official"),
    "qwen": ("通义千问官方博客", "official"),
    "jiqizhixin": ("机器之心", "media"),
    "qbitai": ("量子位", "media"),
    "ifanr": ("爱范儿", "media"),
    "36kr": ("36氪", "media"),
    "weibo_hot": ("微博热搜", "trending"),
    "baidu_hot": ("百度热搜", "trending"),
    "hackernews": ("Hacker News", "overseas"),
}

# 来源地区（资讯页国内/国外切换用）
SOURCE_REGIONS = {
    "official": "海外",
    "qwen": "国内",
    "jiqizhixin": "国内",
    "qbitai": "国内",
    "ifanr": "国内",
    "36kr": "国内",
    "weibo_hot": "国内",
    "baidu_hot": "国内",
    "hackernews": "海外",
}


def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    p = config_path()
    if p.exists():
        try:
            stored = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            stored = {}
        for k, v in stored.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def save_config(cfg: dict) -> None:
    ensure_dirs()
    config_path().write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update_config(patch: dict) -> dict:
    cfg = load_config()
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    save_config(cfg)
    return cfg
