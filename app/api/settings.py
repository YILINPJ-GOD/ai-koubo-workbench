"""设置：API key / 模型 / 抓取源开关 / 写稿偏好 / 测试连接。"""
from fastapi import APIRouter

from ..config import SOURCE_NAMES, load_config, update_config
from ..llm import LLMError, get_llm

router = APIRouter()


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return key[:6] + "****" + key[-4:]


@router.get("/settings")
def get_settings():
    cfg = load_config()
    sources = [
        {"key": key, "name": SOURCE_NAMES[key][0], "type": SOURCE_NAMES[key][1],
         "enabled": bool(cfg.get("sources", {}).get(key, True))}
        for key in SOURCE_NAMES
    ]
    return {
        "api_key_masked": _mask(cfg.get("api_key") or ""),
        "has_api_key": bool((cfg.get("api_key") or "").strip()),
        "model": cfg.get("model"),
        "sources": sources,
        "style_pref": cfg.get("style_pref", {}),
    }


@router.put("/settings")
def put_settings(patch: dict):
    allowed_models = {"glm-4-flash", "glm-4-air", "glm-4-plus"}
    patch = dict(patch)
    if "model" in patch and patch["model"] not in allowed_models:
        patch["model"] = "glm-4-flash"
    if "api_key" in patch:
        # 前端可能原样回传掩码串，此时不覆盖真实 key
        v = str(patch["api_key"] or "").strip()
        if "****" in v or not v:
            patch.pop("api_key")
        else:
            patch["api_key"] = v
    cfg = update_config(patch)
    return {"ok": True, "api_key_masked": _mask(cfg.get("api_key") or "")}


@router.post("/settings/test")
def test_connection(body: dict | None = None):
    """测试连接。body 可带 api_key：未保存的 key 也能直接验证（不落盘）。"""
    body = body or {}
    cfg = load_config()
    key = str(body.get("api_key") or "").strip() or (cfg.get("api_key") or "").strip()
    if not key:
        return {"ok": False, "message": "尚未配置 API key，请先填写智谱 API key 并保存"}
    try:
        from ..llm import LLMClient

        llm = LLMClient(key, cfg.get("model") or "glm-4-flash")
        out = llm.chat("你是连通性测试助手。", "收到请只回复两个字：pong", retries=0)
        return {"ok": True, "message": f"连接成功（{llm.model}），模型回复：{out.strip()[:20]}"}
    except LLMError as e:
        return {"ok": False, "message": str(e)}
