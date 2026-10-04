"""系统级接口：健康检查、任务进度、全局状态。"""
from fastapi import APIRouter, HTTPException

from ..db import get_state
from ..services import jobs

router = APIRouter()

VERSION = "1.0.0"


@router.get("/health")
def health():
    from ..config import load_config
    from ..db import query_one

    cfg = load_config()
    db_ok = True
    try:
        query_one("SELECT 1")
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "ok": True,
        "version": VERSION,
        "db_ok": db_ok,
        "key_configured": bool((cfg.get("api_key") or "").strip()),
        "model": cfg.get("model"),
        "features": {"refresh": True},
        "last_refresh_at": get_state("last_refresh_at"),
        "last_refresh_stats": _safe_json(get_state("last_refresh_stats")),
    }


def _safe_json(text: str):
    import json

    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job
