"""热点选题库与工作台数据服务。"""
import json
from datetime import datetime

from ..db import execute, query, query_one

STATUS_FLOW = ["pending", "packed", "shot"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def list_hotspots(status: str = "", day: str = "", active: bool = False, limit: int = 60) -> list[dict]:
    where, params = ["1=1"], []
    if status:
        where.append("status=?")
        params.append(status)
    if day:
        where.append("day=?")
        params.append(day)
    if active:
        # 今日活跃 = 昨日至今更新过且未过时（与首页必做同一池子）
        from datetime import timedelta

        since = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        where.append("status != 'expired'")
        where.append("date(updated_at) >= ?")
        params.append(since)
    rows = query(
        f"SELECT * FROM hotspots WHERE {' AND '.join(where)} ORDER BY day DESC, updated_at DESC, score DESC LIMIT ?",
        tuple(params) + (limit,),
    )
    for r in rows:
        r["angles"] = json.loads(r["angles"] or "[]")
    return rows


def get_hotspot(hotspot_id: int) -> dict | None:
    r = query_one("SELECT * FROM hotspots WHERE id=?", (hotspot_id,))
    if not r:
        return None
    r["angles"] = json.loads(r["angles"] or "[]")
    return r


def hotspot_sources(hotspot_id: int) -> list[dict]:
    return query(
        "SELECT id, title, url, summary, summary_zh, source_key, source_type, image_url, raw_text, images "
        "FROM items WHERE event_id=? ORDER BY id",
        (hotspot_id,),
    )


def get_pack(hotspot_id: int) -> dict | None:
    r = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hotspot_id,))
    if not r:
        return None
    for key in ("scripts", "captions", "image_candidates", "image_selected", "publish", "risks", "checklist"):
        r[key] = json.loads(r[key] or ("{}" if key in ("scripts", "captions", "publish") else "[]"))
    return r


def mark_shot(hotspot_id: int) -> dict | None:
    execute("UPDATE hotspots SET status='shot', updated_at=? WHERE id=?", (_now(), hotspot_id))
    return get_hotspot(hotspot_id)


def expire(hotspot_id: int) -> dict | None:
    """标记过时：立刻退出必做/备选，且不再被递补。"""
    execute(
        "UPDATE hotspots SET status='expired', is_must=0, is_backup=0, updated_at=? WHERE id=?",
        (_now(), hotspot_id),
    )
    return get_hotspot(hotspot_id)


def reactivate(hotspot_id: int) -> dict | None:
    """从过时状态恢复为待处理。"""
    execute(
        "UPDATE hotspots SET status='pending', updated_at=? WHERE id=?",
        (_now(), hotspot_id),
    )
    return get_hotspot(hotspot_id)


def mark_packed(hotspot_id: int) -> None:
    execute("UPDATE hotspots SET status='packed', updated_at=? WHERE id=?", (_now(), hotspot_id))
