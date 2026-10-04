"""内存任务表：刷新流水线 / 素材包生成 / 文案拆解等长任务的进度与结果。"""
import threading
import traceback
import uuid
from datetime import datetime

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_JOB_TTL = 200  # 完成后保留秒数，供前端最后一次拉取
_finished_at: dict[str, float] = {}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start_job(fn, *args, total: int = 1, label: str = "") -> str:
    """在后台线程执行 fn(job_id, *args)。fn 内用 update_job 汇报进度。"""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "label": label,
            "status": "running",  # running | done | error
            "stage": "",
            "message": "",
            "done": 0,
            "total": total,
            "result": None,
            "error": "",
            "created_at": _now(),
        }
    t = threading.Thread(target=_run, args=(fn, job_id, args), daemon=True)
    t.start()
    return job_id


def _run(fn, job_id: str, args: tuple) -> None:
    try:
        result = fn(job_id, *args)
        finish_job(job_id, result=result)
    except Exception as e:  # noqa: BLE001 —— 任务线程兜底，错误必须进任务表
        finish_job(job_id, error=f"{e}")
        traceback.print_exc()


def update_job(job_id: str, *, done: int | None = None, total: int | None = None,
               stage: str | None = None, message: str | None = None) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        if done is not None:
            job["done"] = done
        if total is not None:
            job["total"] = total
        if stage is not None:
            job["stage"] = stage
        if message is not None:
            job["message"] = message


def finish_job(job_id: str, *, result=None, error: str = "") -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["status"] = "error" if error else "done"
        job["error"] = error
        if result is not None:
            job["result"] = result
        _finished_at[job_id] = datetime.now().timestamp()
        _gc_locked()


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _gc_locked() -> None:
    now = datetime.now().timestamp()
    expired = [jid for jid, ts in _finished_at.items() if now - ts > _JOB_TTL]
    for jid in expired:
        _jobs.pop(jid, None)
        _finished_at.pop(jid, None)
