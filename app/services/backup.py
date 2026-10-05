"""备份与恢复（M6 核心，M0 起启动时即自动备份）。"""
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from ..config import (
    backups_dir,
    config_path,
    data_dir,
    db_path,
    ensure_dirs,
    images_dir,
)

KEEP = 7  # PD.md 7.2：滚动保留最近 7 份


def backup_db(tag: str = "auto") -> Path:
    """备份数据库到 backups/（sqlite backup API，WAL 模式下也一致），返回备份文件路径。"""
    import sqlite3

    ensure_dirs()
    src = db_path()
    if not src.exists():
        raise FileNotFoundError("数据库文件不存在，无法备份")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = backups_dir() / f"workbench-{stamp}-{tag}.db"
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    _rotate()
    return dst


def _rotate(keep: int = KEEP) -> list[Path]:
    """按文件名时间戳排序，仅保留最近 keep 份，返回被删除的文件。"""
    files = sorted(backups_dir().glob("workbench-*.db"))
    removed = []
    for old in files[:-keep] if keep else []:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            pass
    return removed


def export_backup(zip_path: Path) -> Path:
    """导出 db + config.json + images/ 为 zip（手动备份，PD.md 7.3）。"""
    ensure_dirs()
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if db_path().exists():
            zf.write(db_path(), "workbench.db")
        if config_path().exists():
            zf.write(config_path(), "config.json")
        img_dir = images_dir()
        if img_dir.exists():
            for f in img_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f"images/{f.relative_to(img_dir).as_posix()}")
    return zip_path


def import_backup(zip_path: Path) -> dict:
    """导入 zip 覆盖当前数据。恢复前自动备份当前数据（PD.md 7.4）。

    返回 {'restored': [文件名], 'safety_backup': 路径字符串}；需重启应用生效。
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise ValueError("备份文件不存在")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "workbench.db" not in names:
            raise ValueError("备份包里没有 workbench.db，不是有效的备份文件")
        safety = backup_db(tag="before-restore")
        data = data_dir()
        ensure_dirs()
        zf.extract("workbench.db", data)
        if "config.json" in names:
            zf.extract("config.json", data)
        for name in names:
            if name.startswith("images/") and not name.endswith("/"):
                # zip slip 净化：拒绝路径穿越组件（审查F1）
                rel = Path(name).relative_to("images")
                if ".." in rel.parts or rel.is_absolute() or not rel.name:
                    continue
                target = data / "images" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        # 清理旧 WAL/SHM：防止旧帧重放到新库上（审查M4/F9）
        for suffix in ("-wal", "-shm"):
            stale = data / f"workbench.db{suffix}"
            if stale.exists():
                stale.unlink()
        return {
            "restored": [n for n in names if not n.endswith("/")],
            "safety_backup": str(safety),
        }


def clear_all_data() -> dict:
    """清空业务数据（数据库表 + 图片缓存），保留 config.json（含 API key）。

    PD.md 7.5：执行前强制自动备份。
    """
    ensure_dirs()
    safety = backup_db(tag="before-clear") if db_path().exists() else None
    from ..db import connect

    conn = connect()
    try:
        # 先删被引用的子表，再删父表（外键约束）
        for table in ("packs", "items", "hotspots", "styles", "fetch_runs", "app_state"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    removed_images = 0
    if images_dir().exists():
        for f in images_dir().rglob("*"):
            if f.is_file():
                f.unlink()
                removed_images += 1
    return {"safety_backup": str(safety) if safety else "", "removed_images": removed_images}


def list_backups() -> list[dict]:
    out = []
    for f in sorted(backups_dir().glob("workbench-*.db"), reverse=True):
        out.append({"name": f.name, "size": f.stat().st_size})
    return out


def read_export_meta() -> dict:
    """导出前给用户展示的数据量摘要。"""
    from ..db import query_one

    def count(table: str) -> int:
        row = query_one(f"SELECT COUNT(*) AS n FROM {table}")
        return row["n"] if row else 0

    n_images = 0
    if images_dir().exists():
        n_images = sum(1 for f in images_dir().rglob("*") if f.is_file())
    cfg = json.loads(config_path().read_text(encoding="utf-8")) if config_path().exists() else {}
    return {
        "items": count("items"),
        "hotspots": count("hotspots"),
        "packs": count("packs"),
        "styles": count("styles"),
        "images": n_images,
        "has_api_key": bool((cfg.get("api_key") or "").strip()),
    }
