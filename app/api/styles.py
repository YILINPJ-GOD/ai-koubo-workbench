"""拆解库接口：链接提取文案（任务）、拆解（任务）、模板 CRUD。"""
from fastapi import APIRouter, HTTPException

from .. import llm as llm_mod
from ..services import jobs, styles, transcribe

router = APIRouter()


@router.post("/styles/extract-text")
def extract_text(body: dict):
    """从视频分享链接提取口播文案（下载+转写，任务化）。"""
    link = str(body.get("link") or "")
    if not transcribe.extract_url(link):
        raise HTTPException(status_code=400, detail="没有识别到链接，请粘贴包含视频链接的分享文本")

    def _job(job_id: str):
        def _progress(msg: str):
            jobs.update_job(job_id, stage="transcribe", message=msg)

        return {"text": transcribe.extract_text_from_link(link, _progress)}

    job_id = jobs.start_job(_job, total=1, label="视频转文案")
    return {"job_id": job_id}


@router.get("/styles")
def list_styles():
    return {"styles": styles.list_styles()}


@router.post("/styles/teardown")
def teardown(body: dict):
    text = str(body.get("text") or "")
    if len(text.strip()) < 30:
        raise HTTPException(status_code=400, detail="文案太短（至少30字），拆不出结构")

    def _job(job_id: str):
        llm = llm_mod.get_llm()
        return styles.teardown_script(text, llm)

    job_id = jobs.start_job(_job, total=1, label="拆解口播文案")
    return {"job_id": job_id}


@router.post("/styles")
def save_style(body: dict):
    name = str(body.get("name") or "").strip()
    source_text = str(body.get("source_text") or "")
    teardown = body.get("teardown")
    if not name:
        raise HTTPException(status_code=400, detail="请给模板起个名字")
    if not source_text or not teardown:
        raise HTTPException(status_code=400, detail="缺少原文或拆解结果")
    style_id = styles.save_style(name, source_text, teardown)
    return {"id": style_id}


@router.put("/styles/{style_id}")
def update_style(style_id: int, body: dict):
    row = styles.update_style(style_id, body)
    if row is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return row


@router.delete("/styles/{style_id}")
def delete_style(style_id: int):
    if not styles.delete_style(style_id):
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"ok": True}
