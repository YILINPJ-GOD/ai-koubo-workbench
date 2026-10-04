"""M2 源可用性补充探测：机器之心API、百度结构、微博镜像、更多官方源。"""
import json

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
out = {}

with httpx.Client(headers={"User-Agent": UA, "Referer": "https://www.jiqizhixin.com/"}, timeout=12, follow_redirects=True) as c:
    # 机器之心文章 API
    for url in [
        "https://www.jiqizhixin.com/api/v4/articles?page=1&per=5",
        "https://www.jiqizhixin.com/graphql?search=%7B%7D",
    ]:
        key = f"jqzx-api:{url.split('/')[3][:20]}"
        try:
            r = c.get(url)
            body = r.text[:300].replace("\n", " ")
            out[key] = f"HTTP {r.status_code} len={len(r.text)} body={body}"
        except Exception as e:
            out[key] = f"FAIL {type(e).__name__}: {str(e)[:60]}"

    # 百度热搜 pc 端
    try:
        r = c.get("https://top.baidu.com/api/board?platform=pc&tab=realtime")
        if r.status_code == 200:
            data = r.json()
            cards = data.get("data", {}).get("cards", [])
            contents = []
            for card in cards:
                for it in card.get("content", []):
                    contents.append(it.get("word", ""))
            out["baidu-pc"] = f"items={len(contents)} first={contents[:2]}"
        else:
            out["baidu-pc"] = f"HTTP {r.status_code}"
    except Exception as e:
        out["baidu-pc"] = f"FAIL {type(e).__name__}: {str(e)[:60]}"

    # 微博 m 站
    try:
        r = c.get(
            "https://m.weibo.cn/api/container/getIndex",
            params={"containerid": "106003type=25&t=3&disable_hot=1&filter_type=realtimehot"},
            headers={"User-Agent": UA, "Referer": "https://m.weibo.cn/", "MWeibo-Pwa": "1", "X-Requested-With": "XMLHttpRequest"},
        )
        if r.status_code == 200:
            data = r.json()
            cards = data.get("data", {}).get("cards", [])
            words = []
            for card in cards:
                for cb in card.get("card_group", []):
                    w = cb.get("desc", "")
                    if w:
                        words.append(w)
            out["weibo-m"] = f"HTTP 200 items={len(words)} first={words[:2]}"
        else:
            out["weibo-m"] = f"HTTP {r.status_code}"
    except Exception as e:
        out["weibo-m"] = f"FAIL {type(e).__name__}: {str(e)[:60]}"

    # 更多官方博客 RSS
    for url in [
        "https://www.anthropic.com/rss.xml",
        "https://blog.google/rss/",
        "https://openai.com/index/rss/",
    ]:
        host = url.split("/")[2]
        try:
            r = c.get(url)
            ok = r.status_code == 200 and ("<item" in r.text or "<entry" in r.text)
            out[f"official2:{host}{url.split(host)[1][:16]}"] = f"HTTP {r.status_code} rss={ok} len={len(r.text)}"
        except Exception as e:
            out[f"official2:{host}"] = f"FAIL {type(e).__name__}: {str(e)[:60]}"

print(json.dumps(out, ensure_ascii=False, indent=2))
