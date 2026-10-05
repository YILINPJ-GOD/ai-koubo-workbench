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
                        with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True) as client:
                            r = client.get(c["url"])
                            r.raise_for_status()
                            cache.write_bytes(r.content)
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
