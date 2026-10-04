"""M2 源可用性验证：实测 6 个抓取源能否返回数据，输出结论。"""
import json
import sys

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

OFFICIAL_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://rsshub.app/twitter/user/OpenAI",
]

def head_lines(s, n=120):
    return " | ".join(s.replace("\n", " ").split())[:n]

results = {}

with httpx.Client(headers={"User-Agent": UA}, timeout=12, follow_redirects=True) as c:
    # 1. 官方源 RSS 候选
    for url in OFFICIAL_FEEDS:
        key = f"official:{url.split('/')[2]}"
        try:
            r = c.get(url)
            ok = r.status_code == 200 and ("<item" in r.text or "<entry" in r.text)
            results[key] = f"HTTP {r.status_code}, rss={ok}, len={len(r.text)}"
        except Exception as e:
            results[key] = f"FAIL {type(e).__name__}: {str(e)[:80]}"

    # 2. 机器之心
    try:
        r = c.get("https://www.jiqizhixin.com/rss")
        ok = r.status_code == 200 and "<item" in r.text
        results["jiqizhixin"] = f"HTTP {r.status_code}, rss={ok}, len={len(r.text)}"
    except Exception as e:
        results["jiqizhixin"] = f"FAIL {type(e).__name__}: {str(e)[:80]}"

    # 3. 量子位
    try:
        r = c.get("https://www.qbitai.com/feed")
        ok = r.status_code == 200 and "<item" in r.text
        results["qbitai"] = f"HTTP {r.status_code}, rss={ok}, len={len(r.text)}"
    except Exception as e:
        results["qbitai"] = f"FAIL {type(e).__name__}: {str(e)[:80]}"

    # 4. 微博热搜
    try:
        r = c.get("https://weibo.com/ajax/side/hotSearch")
        n = 0
        if r.status_code == 200:
            data = r.json()
            realtime = data.get("data", {}).get("realtime", [])
            n = len(realtime)
            results["weibo_hot"] = f"HTTP {r.status_code}, items={n}, first={head_lines(realtime[0].get('word','')) if realtime else '-'}"
        else:
            results["weibo_hot"] = f"HTTP {r.status_code}"
    except Exception as e:
        results["weibo_hot"] = f"FAIL {type(e).__name__}: {str(e)[:80]}"

    # 5. 百度热搜
    try:
        r = c.get("https://top.baidu.com/api/board?platform=wise&tab=realtime")
        n = 0
        first = "-"
        if r.status_code == 200:
            data = r.json()
            cards = data.get("data", {}).get("cards", [])
            for card in cards:
                for item in card.get("content", []):
                    n += 1
                    if n == 1:
                        first = head_lines(item.get("word", ""))
        results["baidu_hot"] = f"HTTP {r.status_code}, items={n}, first={first}"
    except Exception as e:
        results["baidu_hot"] = f"FAIL {type(e).__name__}: {str(e)[:80]}"

    # 6. Hacker News
    try:
        r = c.get("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=5")
        n = 0
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            n = len(hits)
            results["hackernews"] = f"HTTP {r.status_code}, items={n}, first={head_lines(hits[0].get('title','')) if hits else '-'}"
        else:
            results["hackernews"] = f"HTTP {r.status_code}"
    except Exception as e:
        results["hackernews"] = f"FAIL {type(e).__name__}: {str(e)[:80]}"

print(json.dumps(results, ensure_ascii=False, indent=2))
