"""首页「今日」聚合：只汇总，不生产。"""
import json
from datetime import datetime

from ..config import load_config
from ..db import get_state, query, query_one


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _card(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "day": row["day"],
        "why": row["why"],
        "angles": json.loads(row["angles"] or "[]"),
        "suggested_length": row["suggested_length"],
        "score": row["score"],
        "sources_count": row["sources_count"],
        "cover": row["cover"],
        "sequel_of": row["sequel_of"],
        "status": row["status"],
    }


def _active_since() -> str:
    """「今日」的语义 = 昨日至今：昨晚的大事今早还在跟进窗口内。"""
    from datetime import timedelta

    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def today_payload() -> dict:
    day = _today()
    since = _active_since()
    # 必做/备选：昨日至今仍活跃（最近更新过）且未过时
    must = query(
        "SELECT * FROM hotspots WHERE is_must=1 AND status != 'expired' "
        "AND date(updated_at) >= ? ORDER BY day DESC, updated_at DESC, score DESC",
        (since,),
    )
    backup = query(
        "SELECT * FROM hotspots WHERE is_backup=1 AND is_must=0 AND status != 'expired' "
        "AND date(updated_at) >= ? ORDER BY day DESC, updated_at DESC, score DESC LIMIT 6",
        (since,),
    )
    stats = {}
    try:
        stats = json.loads(get_state("last_refresh_stats") or "{}")
    except json.JSONDecodeError:
        stats = {}
    funnel = {
        "fetched": stats.get("raw", 0),
        "kept": stats.get("kept", 0),
        "hotspots": len(must) + len(backup),
        "per_source": stats.get("per_source", {}),
    }
    from .feed import todo_items

    cfg = load_config()
    counts = query_one("SELECT COUNT(*) AS n FROM items")
    return {
        "date": day,
        "todos": todo_items(),
        "last_refresh_at": get_state("last_refresh_at"),
        "must": [_card(r) for r in must],
        "backup": [_card(r) for r in backup],
        "funnel": funnel,
        "empty_reason": get_state(f"empty_reason_{day}"),
        "key_configured": bool((cfg.get("api_key") or "").strip()),
        "has_items": bool(counts and counts["n"]),
    }
