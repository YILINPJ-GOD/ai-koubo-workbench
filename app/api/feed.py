"""资讯流水接口 + 全局刷新任务。"""
import json

from fastapi import APIRouter, HTTPException

from ..pipeline import run as pipeline_run
from ..services import feed, jobs

router = APIRouter()


@router.get("/feed")
def get_feed(
    category: str = "",
    source_type: str = "",
    unread: bool = False,
    starred: bool = False,
    q: str = "",
    day: str = "",
    sort: str = "",
    region: str = "",
    limit: int = 100,
    offset: int = 0,
):
    return feed.list_items(
        category=category,
        source_type=source_type,
        unread_only=unread,
        starred_only=starred,
        q=q,
        day=day,
        sort=sort,
        region=region,
        limit=min(limit, 300),
        offset=offset,
    )


@router.get("/feed/archive")
def feed_archive():
    return {"days": feed.archive_days()}


@router.patch("/items/{item_id}")
def patch_item(item_id: int, patch: dict):
    row = feed.set_item_flags(
        item_id,
        is_read=patch.get("is_read"),
        is_starred=patch.get("is_starred"),
        todo_done=patch.get("todo_done"),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return row


@router.get("/todo")
def get_todo():
    return {"todos": feed.todo_items()}


@router.post("/items/{item_id}/promote")
def promote_to_hotspot(item_id: int):
    """收藏的资讯一键转为选题卡，进入热点工作流。"""
    from datetime import datetime

    from ..db import execute, query_one

    item = query_one("SELECT * FROM items WHERE id=?", (item_id,))
    if not item:
        raise HTTPException(status_code=404, detail="资讯不存在")
    if item.get("event_id"):
        # 已转过的直接返回原卡（审查L8：双击产生重复卡）
        return {"hotspot_id": item["event_id"], "existed": True}
    now = datetime.now().isoformat(timespec="seconds")
    hid = execute(
        """INSERT INTO hotspots(title, category, why, angles, suggested_length, score,
           sources_count, status, day, cover, is_must, is_backup, created_at, updated_at)
           VALUES (?,?,?,?,?,?,1,'pending',?,?,0,0,?,?)""",
        (
            item["title"][:80],
            item["category"] or "AI产品",
            item["summary"] or "",
            json.dumps([item["title"][:40]], ensure_ascii=False),
            "30s",
            75,
            item["day"],
            item["image_url"] or "",
            now,
            now,
        ),
    )
    execute("UPDATE items SET event_id=? WHERE id=?", (hid, item_id))
    execute("UPDATE items SET todo_done=1 WHERE id=?", (item_id,))
    return {"hotspot_id": hid}


@router.post("/feed/read-all")
def read_all(
    category: str = "",
    source_type: str = "",
    q: str = "",
    day: str = "",
    region: str = "",
):
    return {"marked": feed.mark_all_read(category=category, source_type=source_type, q=q, day=day, region=region)}


@router.post("/refresh")
def start_refresh():
    job_id = jobs.start_job(pipeline_run.refresh_pipeline, total=1, label="刷新资讯")
    return {"job_id": job_id}
