"""去重：URL 精确去重 + 近7天标题相似度合并。"""
import re
from difflib import SequenceMatcher

_PUNCT_RE = re.compile(
    "[\\s，。！？、：；「」『』（）()【】\\[\\]“”‘’·…—\\-_|\"']+"
)


def normalize_title(title: str) -> str:
    return _PUNCT_RE.sub("", (title or "").lower())


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # 短标题包含关系（如热搜词 vs 完整新闻标题）
    if len(na) <= 12 and na in nb:
        return 0.95
    if len(nb) <= 12 and nb in na:
        return 0.95
    return SequenceMatcher(None, na, nb).ratio()


SIM_THRESHOLD = 0.82


def dedup_drafts(drafts: list[dict], existing_urls: set[str], existing_titles: list[str]):
    """批内 + 库内去重。返回 (kept, dup_count)。

    existing_urls: 库内已有 URL（含同批已收录），existing_titles: 近期标题。
    """
    kept: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: list[str] = list(existing_titles)
    dup = 0
    for d in drafts:
        url = d["url"]
        if url in seen_urls or url in existing_urls:
            dup += 1
            continue
        if any(title_similarity(d["title"], t) >= SIM_THRESHOLD for t in seen_titles):
            dup += 1
            continue
        seen_urls.add(url)
        seen_titles.append(d["title"])
        kept.append(d)
    return kept, dup
