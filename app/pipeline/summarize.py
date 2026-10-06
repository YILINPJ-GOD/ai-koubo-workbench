"""AI 批量摘要与分类：海外条目附带中文翻译；「无关」条目被丢弃。"""
import json

SYSTEM = (
    "你是资讯编辑，为一位做AI领域口播短视频的创作者处理资讯。"
    "对每条输入资讯输出：中文一句话摘要（不超过40字，含关键信息）、分类"
    "（大模型/AI产品/科技大事件/无关 之一）、海外资讯的中文翻译摘要（中文资讯留空）、"
    "region：按资讯内容的主体归属判「国内」或「海外」——"
    "谷歌/OpenAI/Anthropic/Meta/xAI 等海外公司的动态=海外，"
    "华为/小米/阿里/字节/DeepSeek/Qwen/智谱等中国公司动态=国内，"
    "以报道媒体的中外国籍无关。"
    '严格输出 JSON：{"results":[{"i":序号,"summary":"...","category":"...","summary_zh":"...","region":"国内|海外"}]}'
)

CHUNK = 8


def summarize_items(items: list[dict], llm) -> dict[int, dict]:
    """items: [{i, title, text, overseas}]，返回 {i: {summary, category, summary_zh}}。

    LLM 判为「无关」的条目不在返回结果中。个别批次失败时该批次条目
    降级用标题截断作摘要（category 置空待定），不阻塞整体流水线。
    """
    result: dict[int, dict] = {}
    for start in range(0, len(items), CHUNK):
        chunk = items[start : start + CHUNK]
        payload = [
            {
                "i": it["i"],
                "title": it["title"],
                "text": (it.get("text") or it["title"])[:600],
                "overseas": bool(it.get("overseas")),
            }
            for it in chunk
        ]
        user = "待处理资讯列表：\n" + json.dumps(payload, ensure_ascii=False)
        try:
            data = llm.chat_json(SYSTEM, user)
        except Exception:  # noqa: BLE001 —— 整批失败才标题降级
            for it in chunk:
                result[it["i"]] = {
                    "summary": it["title"][:40],
                    "category": "",
                    "summary_zh": "",
                    "region": "",
                }
            continue
        for row in data.get("results", []) if isinstance(data, dict) else []:
            i = row.get("i")
            # 单条坏序号只跳过该条（审查M3：此前会连累整批好数据一起降级）
            try:
                idx = int(i)
            except (TypeError, ValueError):
                continue
            if str(row.get("category", "")).strip() == "无关":
                continue
            result[idx] = {
                "summary": str(row.get("summary", "")).strip()[:60],
                "category": str(row.get("category", "")).strip(),
                "summary_zh": str(row.get("summary_zh", "")).strip(),
                "region": str(row.get("region", "")).strip(),
            }
    return result
