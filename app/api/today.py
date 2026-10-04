"""首页「今日」接口 + 重新评估。"""
from fastapi import APIRouter

from ..pipeline.cluster import cluster_and_store
from ..services import jobs, today

router = APIRouter()


@router.get("/today")
def get_today():
    return today.today_payload()


@router.post("/today/reevaluate")
def reevaluate():
    job_id = jobs.start_job(cluster_and_store, total=1, label="重新评估选题")
    return {"job_id": job_id}
