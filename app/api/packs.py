"""素材包接口：生成（任务）、读取、选图、重新生成、图片文件与打包下载。"""
import os
import tempfile
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from .. import llm as llm_mod
from ..llm import LLMError
from ..services import jobs, packs

router = APIRouter()


def _run_generation(hotspot_id: int, style_id):
    def _job(job_id: str):
        llm = llm_mod.get_llm()
        result = packs.generate_pack(hotspot_id, style_id, llm)
        warnings = result.get("warnings") or []
        return {"pack_id": result["pack_id"], "warnings": warnings}
    return _job


@router.post("/hotspots/{hotspot_id}/pack")
def generate(hotspot_id: int, body: dict | None = None):
    style_id = (body or {}).get("style_id")
    from ..services.hotspots import get_hotspot

    if get_hotspot(hotspot_id) is None:
        raise HTTPException(status_code=404, detail="热点不存在")
    job_id = jobs.start_job(_run_generation(hotspot_id, style_id), total=1, label="生成素材包")
    return {"job_id": job_id}


@router.post("/packs/{pack_id}/outline")
def make_outline(pack_id: int, body: dict):
    """拍摄提词用：把指定档位口播稿拆句并生成关键词提纲（缓存）。"""
    import json as _json

    from .. import llm as llm_mod
    from ..db import query_one
    from ..services.hotspots import get_outline, save_outline

    slot = str(body.get("slot") or "30s")
    if slot not in ("15s", "30s", "60s"):
        raise HTTPException(status_code=400, detail="档位不合法")

    row = query_one("SELECT scripts FROM packs WHERE id=?", (pack_id,))
    if not row:
        raise HTTPException(status_code=404, detail="素材包不存在")
    script = (_json.loads(row["scripts"]).get(slot) or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail=f"{slot} 档还没有口播稿")

    cached = get_outline(pack_id, slot)
    if cached:
        return {"slot": slot, "lines": cached, "cached": True}

    def _run(job_id: str):
        from ..services import jobs as _jobs

        _jobs.update_job(job_id, message="AI 正在提炼关键词提纲…")
        llm = llm_mod.get_llm()
        data = llm.chat_json(
            "你是口播提词助手。把口播稿拆成适合提词的短句，每句给2-4个便于记忆的关键词。"
            '严格输出 JSON：{"lines":[{"text":"原句","keys":["关键词","关键词"]}]}',
            script,
        )
        lines = []
        for ln in (data.get("lines") or [])[:40] if isinstance(data, dict) else []:
            text = str(ln.get("text", "")).strip()
            if not text:
                continue
            keys = [str(k).strip() for k in (ln.get("keys") or []) if str(k).strip()][:4]
            lines.append({"text": text, "keys": keys})
        if not lines:
            raise LLMError("提纲生成结果为空，请重试")
        save_outline(pack_id, slot, lines)
        return {"slot": slot, "lines": lines, "cached": False}

    job_id = jobs.start_job(_run, total=1, label="生成提纲")
    return {"job_id": job_id}


@router.put("/packs/{pack_id}/images")
def select_images(pack_id: int, body: dict):
    from ..db import query_one

    row = query_one("SELECT image_candidates FROM packs WHERE id=?", (pack_id,))
    if not row:
        raise HTTPException(status_code=404, detail="素材包不存在")
    import json as _json

    n = len(_json.loads(row["image_candidates"]))
    selected = [int(i) for i in (body.get("selected") or []) if 0 <= int(i) < n]
    packs.set_selected_images(pack_id, selected)
    return {"selected": selected}


@router.get("/packs/{pack_id}/images/{idx}/file")
def image_file(pack_id: int, idx: int):
    import json as _json

    from ..db import query_one

    row = query_one("SELECT image_candidates FROM packs WHERE id=?", (pack_id,))
    if not row:
        raise HTTPException(status_code=404, detail="素材包不存在")
    candidates = _json.loads(row["image_candidates"])
    if idx < 0 or idx >= len(candidates):
        raise HTTPException(status_code=404, detail="配图不存在")
    c = candidates[idx]
    if c["type"] == "card":
        p = Path(c["path"])
        if not p.exists():
            raise HTTPException(status_code=404, detail="卡片文件缺失")
        return FileResponse(p, filename=f"card_{idx + 1}.png")
    # 远程原文配图：代理下载并缓存
    cache = packs.remote_cache_path(c["url"])
    if not cache.exists():
        try:
            with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True) as client:
                with client.stream("GET", c["url"]) as r:
                    r.raise_for_status()
                    ctype = r.headers.get("content-type", "")
                    if ctype and not ctype.startswith("image/"):
                        raise HTTPException(status_code=415, detail="链接不是图片")
                    data = r.read()
                    if len(data) > 20 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="图片超过20MB，跳过")
                cache.write_bytes(data)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"图片下载失败：{e}")
    return FileResponse(cache, filename=f"image_{idx + 1}.png")


@router.get("/packs/{pack_id}/images/download")
def download_selected(pack_id: int):
    """把选中的配图打包成 zip。"""
    import json as _json

    from ..db import query_one

    row = query_one("SELECT * FROM packs WHERE id=?", (pack_id,))
    if not row:
        raise HTTPException(status_code=404, detail="素材包不存在")
    candidates = _json.loads(row["image_candidates"])
    selected = _json.loads(row["image_selected"] or "[]")
    if not selected:
        raise HTTPException(status_code=400, detail="尚未勾选任何配图")

    _fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    os.close(_fd)  # 立即关闭句柄，否则 Windows 上 unlink 报 WinError 32（审查F8）
    tmp = Path(tmp_name)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in selected:
            if i >= len(candidates):
                continue
            c = candidates[i]
            if c["type"] == "card":
                p = Path(c["path"])
                if p.exists():
                    zf.write(p, f"card_{i + 1}.png")
            else:
                cache = packs.remote_cache_path(c["url"])
                if not cache.exists():
                    try:
                        # 与 image_file 同一防护：content-type 校验 + 20MB 上限（审查反馈）
                        with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True) as client:
                            with client.stream("GET", c["url"]) as r:
                                r.raise_for_status()
                                ctype = r.headers.get("content-type", "")
                                if ctype and not ctype.startswith("image/"):
                                    continue  # 非图片：跳过这张，不炸整个包
                                data = r.read()
                                if len(data) > 20 * 1024 * 1024:
                                    continue  # 超20MB：跳过
                                cache.write_bytes(data)
                    except Exception:  # noqa: BLE001
                        continue
                if cache.exists():
                    zf.write(cache, f"image_{i + 1}.png")
    return FileResponse(tmp, filename=f"pack_{pack_id}_images.zip", background=BackgroundTask(_cleanup, tmp))


def _cleanup(path: Path):
    try:
        path.unlink()
    except OSError:
        pass
