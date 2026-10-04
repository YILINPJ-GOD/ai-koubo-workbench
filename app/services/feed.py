"""资讯流水查询与状态操作。"""
from ..db import execute, query, query_one

CATEGORIES = ["大模型", "AI产品", "科技大事件"]


def list_items(
    category: str = "",
    source_type: str = "",
    unread_only: bool = False,
    starred_only: bool = False,
    q: str = "",
    day: str = "",
    sort: str = "",
    region: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    where, params = ["1=1"], []

    if category:
        where.append("category=?")
        params.append(category)
    if source_type:
        where.append("source_type=?")
        params.append(source_type)
    if unread_only:
        where.append("is_read=0")
    if starred_only:
        where.append("is_starred=1")
    if q:
        where.append("(i.title LIKE ? OR i.summary LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if day:
        where.append("i.day=?")
        params.append(day)

    if region:
        where.append("COALESCE(NULLIF(i.region,''), s.region)=?")
        params.append(region)

    where_sql = " AND ".join(where)
    total = (
        query_one(
            f"""SELECT COUNT(*) AS n FROM items i
                LEFT JOIN hotspots h ON i.event_id = h.id
                LEFT JOIN sources s ON s.key = i.source_key
                WHERE {where_sql}""",
            tuple(params),
        )
        or {"n": 0}
    )["n"]
    order = "i.id DESC"
    if sort == "hot":
        # 最火优先：事件热度分 → 官方源优先 → 时间
        order = "COALESCE(h.score, 0) DESC, CASE i.source_type WHEN 'official' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, i.id DESC"
    rows = query(
        f"""SELECT i.*, h.id AS hotspot_id, h.title AS hotspot_title, h.score AS event_score,
                   s.name AS source_name
            FROM items i
            LEFT JOIN hotspots h ON i.event_id = h.id
            LEFT JOIN sources s ON s.key = i.source_key
            WHERE {where_sql}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        tuple(params) + (limit, offset),
    )
    return {"total": total, "items": rows}


def archive_days() -> list[dict]:
    """历史归档：每天条数，倒序。"""
    return query(
        "SELECT day, COUNT(*) AS n FROM items GROUP BY day ORDER BY day DESC"
    )


def set_item_flags(
    item_id: int,
    is_read: bool | None = None,
    is_starred: bool | None = None,
    todo_done: bool | None = None,
) -> dict | None:
    row = query_one("SELECT id FROM items WHERE id=?", (item_id,))
    if not row:
        return None
    if is_read is not None:
        execute("UPDATE items SET is_read=? WHERE id=?", (1 if is_read else 0, item_id))
    if is_starred is not None:
        execute("UPDATE items SET is_starred=? WHERE id=?", (1 if is_starred else 0, item_id))
    if todo_done is not None:
        execute("UPDATE items SET todo_done=? WHERE id=?", (1 if todo_done else 0, item_id))
    return query_one("SELECT * FROM items WHERE id=?", (item_id,))


def todo_items(limit: int = 12) -> list[dict]:
    """今日待办：收藏且未完成的资讯，新收藏在前。"""
    return query(
        """SELECT i.id, i.title, i.url, i.summary, i.source_key, i.source_type, i.fetched_at,
                  s.name AS source_name
           FROM items i LEFT JOIN sources s ON s.key = i.source_key
           WHERE i.is_starred=1 AND i.todo_done=0
           ORDER BY i.id DESC LIMIT ?""",
        (limit,),
    )


def mark_all_read() -> int:
    from ..db import connect

    conn = connect()
    try:
        cur = conn.execute("UPDATE items SET is_read=1 WHERE is_read=0")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def source_type_counts() -> dict:
    rows = query("SELECT source_type, COUNT(*) AS n FROM items GROUP BY source_type")
    return {r["source_type"]: r["n"] for r in rows}
