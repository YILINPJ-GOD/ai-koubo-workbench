"""SQLite 访问层：每次调用短连接，WAL 模式，行转字典。"""
import sqlite3
from pathlib import Path

from .config import db_path, data_dir, ensure_dirs

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    ensure_dirs()
    conn = connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _seed_sources(conn)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：老库补列。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    if "images" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN images TEXT DEFAULT '[]'")
    hcols = {r[1] for r in conn.execute("PRAGMA table_info(hotspots)")}
    if "category" not in hcols:
        conn.execute("ALTER TABLE hotspots ADD COLUMN category TEXT DEFAULT ''")
    icols2 = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    if "todo_done" not in icols2:
        conn.execute("ALTER TABLE items ADD COLUMN todo_done INTEGER DEFAULT 0")
    if "region" not in icols2:
        conn.execute("ALTER TABLE items ADD COLUMN region TEXT DEFAULT ''")
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(packs)")}
    if "outline" not in pcols:
        conn.execute("ALTER TABLE packs ADD COLUMN outline TEXT DEFAULT '{}'")
    scols = {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
    if "region" not in scols:
        conn.execute("ALTER TABLE sources ADD COLUMN region TEXT DEFAULT ''")
    from .config import SOURCE_REGIONS

    for key, region in SOURCE_REGIONS.items():
        conn.execute("UPDATE sources SET region=? WHERE key=?", (region, key))


def _seed_sources(conn: sqlite3.Connection) -> None:
    from .config import SOURCE_NAMES

    for key, (name, type_) in SOURCE_NAMES.items():
        conn.execute(
            "INSERT OR IGNORE INTO sources(key, name, type) VALUES(?,?,?)",
            (key, name, type_),
        )


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    """写操作，返回 lastrowid。"""
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def executemany(sql: str, seq: list[tuple]) -> None:
    conn = connect()
    try:
        conn.executemany(sql, seq)
        conn.commit()
    finally:
        conn.close()


def get_state(key: str, default: str = "") -> str:
    row = query_one("SELECT value FROM app_state WHERE key=?", (key,))
    return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    execute(
        "INSERT INTO app_state(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
