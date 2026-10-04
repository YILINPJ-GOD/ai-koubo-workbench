"""热点选题库接口。"""
from fastapi import APIRouter, HTTPException

from ..services import hotspots

router = APIRouter()


@router.get("/hotspots")
def list_hotspots(status: str = "", day: str = "", active: bool = False):
    return {"hotspots": hotspots.list_hotspots(status=status, day=day, active=active)}


@router.get("/hotspots/{hotspot_id}")
def get_hotspot(hotspot_id: int):
    h = hotspots.get_hotspot(hotspot_id)
    if h is None:
        raise HTTPException(status_code=404, detail="热点不存在")
    return {
        "hotspot": h,
        "sources": hotspots.hotspot_sources(hotspot_id),
        "pack": hotspots.get_pack(hotspot_id),
    }


@router.post("/hotspots/{hotspot_id}/mark-shot")
def mark_shot(hotspot_id: int):
    h = hotspots.mark_shot(hotspot_id)
    if h is None:
        raise HTTPException(status_code=404, detail="热点不存在")
    return h


@router.post("/hotspots/{hotspot_id}/expire")
def expire_hotspot(hotspot_id: int):
    h = hotspots.expire(hotspot_id)
    if h is None:
        raise HTTPException(status_code=404, detail="热点不存在")
    # 立刻保底递补：移出一个，马上补入新的
    from ..db import connect
    from ..pipeline.cluster import ensure_must_floor

    conn = connect()
    try:
        backfilled = ensure_must_floor(conn)
        conn.commit()
    finally:
        conn.close()
    return {"hotspot": h, "backfilled": backfilled}


@router.post("/hotspots/{hotspot_id}/reactivate")
def reactivate_hotspot(hotspot_id: int):
    h = hotspots.reactivate(hotspot_id)
    if h is None:
        raise HTTPException(status_code=404, detail="热点不存在")
    return h
