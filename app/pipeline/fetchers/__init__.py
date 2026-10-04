"""抓取适配器：每个源一个 fetch()，纯解析函数与 HTTP 分离便于离线测试。

draft 统一结构：
{"source_key","source_type","title","url","published_at","summary_html","rank"}
"""
import time
from datetime import datetime, timezone

from ..http import SourceError, get_json, get_text

MAX_PER_SOURCE = 30

# ---------- RSS 类源（官方博客 / 中文媒体） ----------

OFFICIAL_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://blog.google/rss/",
]

MEDIA_FEEDS = {
    "qbitai": ["https://www.qbitai.com/feed"],
    "jiqizhixin": [  # 机器之心接口不稳定，尽力而为，失败在界面标红
        "https://www.jiqizhixin.com/rss.xml",
        "https://www.jiqizhixin.com/rss",
    ],
    "ifanr": ["https://www.ifanr.com/feed"],
    "36kr": ["https://36kr.com/feed"],
}


def _entry_time(entry) -> str:
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            try:
                return datetime.fromtimestamp(time.mktime(tp), tz=timezone.utc).isoformat()
            except (OverflowError, ValueError):
                pass
    return ""


def parse_rss(xml_text: str, source_key: str, source_type: str) -> list[dict]:
    """feedparser 解析，返回 draft 列表。"""
    import feedparser

    feed = feedparser.parse(xml_text)
    drafts = []
    for entry in feed.entries[:MAX_PER_SOURCE]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        drafts.append(
            {
                "source_key": source_key,
                "source_type": source_type,
                "title": title,
                "url": link,
                "published_at": _entry_time(entry),
                "summary_html": entry.get("summary", "") or "",
                "rank": 0,
            }
        )
    return drafts


def fetch_rss_source(client, feeds: list[str], source_key: str, source_type: str) -> list[dict]:
    """依次尝试多个 feed 地址，任一成功即返回。"""
    last_err: Exception | None = None
    for feed_url in feeds:
        try:
            xml_text = get_text(client, feed_url)
            drafts = parse_rss(xml_text, source_key, source_type)
            if drafts:
                return drafts
            last_err = SourceError("feed 解析出 0 条")
        except SourceError as e:
            last_err = e
    raise last_err or SourceError("无可用 feed 地址")


def fetch_official(client) -> list[dict]:
    """官方博客聚合：单个 feed 失败不影响其他，每个 feed 最多取 12 条。"""
    all_drafts: list[dict] = []
    errors: list[str] = []
    for feed_url in OFFICIAL_FEEDS:
        try:
            xml_text = get_text(client, feed_url)
            host = feed_url.split("/")[2]
            drafts = parse_rss(xml_text, "official", "official")[:12]
            all_drafts.extend(drafts)
        except SourceError as e:
            errors.append(f"{feed_url.split('/')[2]}: {e}")
    if not all_drafts and errors:
        raise SourceError("; ".join(errors)[:120])
    return all_drafts


def make_rss_fetcher(source_key: str, source_type: str, feeds: list[str]):
    def _fetch(client) -> list[dict]:
        return fetch_rss_source(client, feeds, source_key, source_type)

    return _fetch


# ---------- 热榜类源 ----------

# 科技相关关键词预过滤（热榜条目量大，先粗筛再交 LLM 判定）
TECH_KEYWORDS = [
    "AI", "ai", "人工智能", "大模型", "模型", "芯片", "半导体", "智能", "机器人",
    "科技", "互联网", "数码", "手机", "卫星", "火箭", "星舰", "自动驾驶", "算法",
    "OpenAI", "ChatGPT", "GPT", "DeepSeek", "英伟达", "苹果", "谷歌", "微软",
    "华为", "小米", "特斯拉", "百度", "阿里", "腾讯", "字节", "量子", "无人机",
    "显卡", "GPU", "CPU", "算力", "操作系统", "游戏", "App", "APP",
    "应用", "软件", "数据", "网络安全", "黑客", "Bug", "bug",
    "新能源", "电动汽车", "发布会", "折叠屏", "元宇宙", "AR", "VR",
]


def tech_relevant(title: str) -> bool:
    return any(k in title for k in TECH_KEYWORDS)


def fetch_baidu_hot(client) -> list[dict]:
    data = get_json(client, "https://top.baidu.com/api/board?platform=pc&tab=realtime")
    return parse_baidu_hot(data)


def parse_baidu_hot(data: dict) -> list[dict]:
    drafts = []
    for card in data.get("data", {}).get("cards", []):
        for item in card.get("content", []):
            word = (item.get("word") or "").strip()
            url = item.get("url") or item.get("rawUrl") or ""
            desc = (item.get("desc") or "").strip()
            if not word:
                continue
            if not tech_relevant(word):
                continue
            drafts.append(
                {
                    "source_key": "baidu_hot",
                    "source_type": "trending",
                    "title": word,
                    "url": url or f"https://www.baidu.com/s?wd={word}",
                    "published_at": "",
                    "summary_html": desc,
                    "rank": item.get("index", 0),
                }
            )
    return drafts[:MAX_PER_SOURCE]


def fetch_weibo_hot(client) -> list[dict]:
    data = get_json(
        client,
        "https://weibo.com/ajax/side/hotSearch",
        referer="https://weibo.com/",
    )
    return parse_weibo_hot(data)


def parse_weibo_hot(data: dict) -> list[dict]:
    drafts = []
    for item in data.get("data", {}).get("realtime", []):
        word = (item.get("word") or "").strip()
        if not word or not tech_relevant(word):
            continue
        drafts.append(
            {
                "source_key": "weibo_hot",
                "source_type": "trending",
                "title": word,
                "url": f"https://s.weibo.com/weibo?q=%23{word}%23",
                "published_at": "",
                "summary_html": (item.get("note") or "").strip(),
                "rank": item.get("rank", 0),
            }
        )
    return drafts[:MAX_PER_SOURCE]


def fetch_hackernews(client) -> list[dict]:
    data = get_json(client, "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=40")
    return parse_hackernews(data)


def parse_hackernews(data: dict) -> list[dict]:
    drafts = []
    for hit in data.get("hits", []):
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        created = (hit.get("created_at") or "").strip()
        drafts.append(
            {
                "source_key": "hackernews",
                "source_type": "overseas",
                "title": title,
                "url": url,
                "published_at": created,
                "summary_html": "",
                "rank": hit.get("points", 0),
            }
        )
    return drafts[:MAX_PER_SOURCE]


FETCHERS = {
    "official": fetch_official,
    "qwen": make_rss_fetcher("qwen", "official", ["https://qwenlm.github.io/blog/index.xml"]),
    "jiqizhixin": make_rss_fetcher("jiqizhixin", "media", MEDIA_FEEDS["jiqizhixin"]),
    "qbitai": make_rss_fetcher("qbitai", "media", MEDIA_FEEDS["qbitai"]),
    "ifanr": make_rss_fetcher("ifanr", "media", MEDIA_FEEDS["ifanr"]),
    "36kr": make_rss_fetcher("36kr", "media", MEDIA_FEEDS["36kr"]),
    "weibo_hot": fetch_weibo_hot,
    "baidu_hot": fetch_baidu_hot,
    "hackernews": fetch_hackernews,
}
