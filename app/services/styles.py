"""拆解库：他人口播文案 → AI 拆解五件套 → 风格模板管理。"""
import json
from datetime import datetime

from ..db import execute, query, query_one

SYSTEM_TEARDOWN = (
    "你是口播文案教练。把给定的一篇抖音口播文案拆解成可复用的结构模板，供另一位创作者"
    "（AI资讯口播、轻松接地气人设）套用到自己的选题上。只学结构和节奏，不改写内容。"
    "输出五件套："
    "hook_type：钩子类型（如：悬念反问/数字冲击/反常识/直接爆点）；"
    "structure：段落结构骨架，数组，每段标功能（如：第1段抛冲突）；"
    "rhythm：节奏特征（句长、换行密度、口头禅等，一句话）；"
    "golden_pattern：金句模式（这篇的金句是怎么造出来的）；"
    "cta_type：结尾CTA类型。"
    '严格输出 JSON：{"hook_type":"","structure":[""],"rhythm":"","golden_pattern":"","cta_type":""}'
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def teardown_script(text: str, llm) -> dict:
    """拆解一篇口播文案，返回五件套 dict。"""
    text = (text or "").strip()
    if len(text) < 30:
        raise ValueError("文案太短（至少30字），拆不出结构")
    data = llm.chat_json(SYSTEM_TEARDOWN, text)
    return normalize_teardown(data)


def normalize_teardown(data: dict) -> dict:
    structure = data.get("structure")
    if isinstance(structure, str):
        structure = [s.strip() for s in structure.split("；") if s.strip()]
    if not isinstance(structure, list):
        structure = []
    return {
        "hook_type": str(data.get("hook_type", "")).strip()[:40],
        "structure": [str(s).strip()[:60] for s in structure if str(s).strip()][:8],
        "rhythm": str(data.get("rhythm", "")).strip()[:120],
        "golden_pattern": str(data.get("golden_pattern", "")).strip()[:120],
        "cta_type": str(data.get("cta_type", "")).strip()[:40],
    }


def save_style(name: str, source_text: str, teardown: dict) -> int:
    return execute(
        "INSERT INTO styles(name, source_text, teardown, created_at) VALUES (?,?,?,?)",
        (name.strip()[:30], source_text, json.dumps(normalize_teardown(teardown), ensure_ascii=False), _now()),
    )


def list_styles() -> list[dict]:
    rows = query("SELECT * FROM styles ORDER BY id DESC")
    for r in rows:
        r["teardown"] = json.loads(r["teardown"])
    return rows


def get_style(style_id: int) -> dict | None:
    r = query_one("SELECT * FROM styles WHERE id=?", (style_id,))
    if r:
        r["teardown"] = json.loads(r["teardown"])
    return r


def update_style(style_id: int, patch: dict) -> dict | None:
    if get_style(style_id) is None:
        return None
    if "name" in patch:
        execute("UPDATE styles SET name=? WHERE id=?", (str(patch["name"]).strip()[:30], style_id))
    if "teardown" in patch:
        execute(
            "UPDATE styles SET teardown=? WHERE id=?",
            (json.dumps(normalize_teardown(patch["teardown"]), ensure_ascii=False), style_id),
        )
    return get_style(style_id)


def delete_style(style_id: int) -> bool:
    if get_style(style_id) is None:
        return False
    execute("DELETE FROM styles WHERE id=?", (style_id,))
    return True


# ---------- 学结构不照搬：连续片段查重 ----------

def _normalize(text: str) -> str:
    import re

    return re.sub(r"[\s，。！？、：；,.!?;:\"“”‘’（）()\-_|…—]", "", (text or "").lower())


def copied_fragment(script: str, source_text: str, window: int = 12) -> str | None:
    """在 script 中找与 source_text 连续 window 字相同的片段；找到视为照搬。"""
    src = _normalize(source_text)
    if len(src) < window:
        return None
    script_norm = _normalize(script)
    for i in range(0, len(script_norm) - window + 1):
        seg = script_norm[i : i + window]
        if seg in src:
            return seg
    return None
