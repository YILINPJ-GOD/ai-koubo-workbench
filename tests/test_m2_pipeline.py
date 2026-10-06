"""M2 测试：源解析（离线 fixture）、去重、AI 摘要、刷新流水线、资讯接口。"""
import json

import pytest

from app.pipeline.dedup import dedup_drafts, title_similarity
from app.pipeline.summarize import summarize_items

# ---------- 标题相似度 / 去重 ----------

def test_title_similarity_identical():
    assert title_similarity("OpenAI 发布 GPT-5", "OpenAI发布GPT-5") == 1.0


def test_title_similarity_subword():
    assert title_similarity("AI芯片", "英伟达发布全新AI芯片，性能翻倍") >= 0.9


def test_title_similarity_different():
    assert title_similarity("苹果发布会定档", "某地明天下雨") < 0.5


def _d(title, url, source_key="qbitai", source_type="media"):
    return {
        "source_key": source_key, "source_type": source_type, "title": title,
        "url": url, "published_at": "", "summary_html": "", "rank": 0,
    }


def test_dedup_url():
    kept, dup = dedup_drafts(
        [_d("A", "http://x/1"), _d("B", "http://x/1")], set(), []
    )
    assert len(kept) == 1 and dup == 1


def test_dedup_against_existing():
    kept, dup = dedup_drafts([_d("A", "http://x/new")], {"http://x/new"}, [])
    assert kept == [] and dup == 1


def test_dedup_similar_title():
    kept, dup = dedup_drafts(
        [_d("OpenAI发布GPT-5模型", "http://x/a"), _d("OpenAI 发布 GPT-5 模型", "http://x/b")],
        set(), [],
    )
    assert len(kept) == 1 and dup == 1


def test_dedup_keeps_different():
    kept, dup = dedup_drafts(
        [_d("苹果发布会定档", "http://x/a"), _d("某地明天下雨", "http://x/b")], set(), []
    )
    assert len(kept) == 2 and dup == 0


# ---------- 源解析 fixture（离线） ----------

RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>T</title>
<item><title>OpenAI 发布新模型</title><link>https://openai.com/a</link>
<pubDate>Wed, 01 Oct 2026 08:00:00 GMT</pubDate><description>正文摘要</description></item>
<item><title></title><link>https://openai.com/b</link></item>
<item><title>第二条新闻标题</title><link>https://openai.com/c</link></item>
</channel></rss>"""


def test_parse_rss():
    from app.pipeline.fetchers import parse_rss

    drafts = parse_rss(RSS_SAMPLE, "official", "official")
    assert len(drafts) == 2  # 空标题条目被跳过
    assert drafts[0]["url"] == "https://openai.com/a"
    assert drafts[0]["published_at"] != ""


def test_parse_baidu_hot():
    from app.pipeline.fetchers import parse_baidu_hot

    data = {"data": {"cards": [{"content": [
        {"word": "某AI公司发布新模型", "url": "https://b.baidu.com/1", "desc": "d", "index": 1},
        {"word": "某地今天下雨了", "url": "https://b.baidu.com/2", "index": 2},
        {"word": "", "index": 3},
    ]}]}}
    drafts = parse_baidu_hot(data)
    assert len(drafts) == 1  # 只有科技相关的保留
    assert drafts[0]["source_type"] == "trending"


def test_parse_weibo_hot():
    from app.pipeline.fetchers import parse_weibo_hot

    data = {"data": {"realtime": [
        {"word": "芯片新突破", "note": "n", "rank": 1},
        {"word": "某明星官宣", "rank": 2},
    ]}}
    drafts = parse_weibo_hot(data)
    assert len(drafts) == 1 and drafts[0]["title"] == "芯片新突破"


def test_parse_hackernews():
    from app.pipeline.fetchers import parse_hackernews

    data = {"hits": [
        {"title": "Show HN: My AI tool", "url": "https://x.dev", "created_at": "2026-10-01T00:00:00Z", "points": 100, "objectID": "1"},
        {"title": "", "objectID": "2"},
        {"title": "No URL story", "url": None, "objectID": "3"},
    ]}
    drafts = parse_hackernews(data)
    assert len(drafts) == 2
    assert drafts[1]["url"].startswith("https://news.ycombinator.com/item?id=3")
    assert drafts[1]["source_type"] == "overseas"


def test_official_partial_failure(monkeypatch):
    """官方源：单 feed 失败不影响其他 feed。"""
    from app.pipeline import fetchers
    from app.pipeline.http import SourceError

    calls = []
    def fake_get_text(client, url):
        calls.append(url)
        if "bad" in url:
            raise SourceError("HTTP 500")
        return RSS_SAMPLE

    monkeypatch.setattr(fetchers, "get_text", fake_get_text)
    monkeypatch.setattr(fetchers, "OFFICIAL_FEEDS", ["https://good.example/rss", "https://bad.example/rss"])
    drafts = fetchers.fetch_official(client=None)
    assert len(drafts) == 2
    assert len(calls) == 2

    monkeypatch.setattr(fetchers, "OFFICIAL_FEEDS", ["https://bad1.example/rss", "https://bad2.example/rss"])
    with pytest.raises(SourceError):
        fetchers.fetch_official(client=None)


# ---------- AI 摘要 ----------

class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, system, user, retries=2):
        self.calls.append(user)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_summarize_batches_and_drops():
    items = [
        {"i": 0, "title": "新模型发布", "text": "text0", "overseas": False},
        {"i": 1, "title": "无关娱乐新闻", "text": "text1", "overseas": False},
        {"i": 2, "title": "Overseas AI news", "text": "text2", "overseas": True},
    ]
    llm = FakeLLM([{"results": [
        {"i": 0, "summary": "某公司发布新模型", "category": "大模型", "summary_zh": ""},
        {"i": 1, "summary": "娱乐新闻", "category": "无关", "summary_zh": ""},
        {"i": 2, "summary": "AI 工具发布", "category": "AI产品", "summary_zh": "某AI工具发布"},
    ]}])
    out = summarize_items(items, llm)
    assert 0 in out and 2 in out and 1 not in out
    assert out[2]["summary_zh"] == "某AI工具发布"


def test_summarize_batch_failure_falls_back():
    items = [{"i": 0, "title": "某条挺长的新闻标题在这里", "text": "t", "overseas": False}]
    llm = FakeLLM([RuntimeError("LLM down")])
    out = summarize_items(items, llm)
    assert out[0]["summary"].startswith("某条挺长")
    assert out[0]["category"] == ""


# ---------- 刷新流水线（假抓取器 + 假 LLM，不触网） ----------

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_HOME", str(tmp_path / "home"))
    from app import config as app_config
    from app import db as app_db

    app_config.ensure_dirs()
    app_db.init_db()
    yield tmp_path


def test_refresh_pipeline_end_to_end(fresh_db, monkeypatch):
    from app.pipeline import run as run_mod

    drafts = [
        _d("模型A发布", "http://s/1"),
        _d("模型A发布", "http://s/1"),  # 批内重复
        _d("另一条AI新闻", "http://s/2", source_key="ifanr"),
    ]

    def fake_fetch_official(client):
        return drafts

    monkeypatch.setattr(run_mod, "FETCHERS", {"official": fake_fetch_official})
    monkeypatch.setattr(run_mod, "_existing_urls", lambda: set())
    monkeypatch.setattr(run_mod, "_recent_titles", lambda: [])
    monkeypatch.setattr(run_mod.extract, "extract_fulltext", lambda url: ("正文内容", ["https://img/1.jpg"]))

    summaries = {0: {"summary": "模型A发布了", "category": "大模型", "summary_zh": ""},
                 1: {"summary": "另一条", "category": "AI产品", "summary_zh": ""}}
    monkeypatch.setattr(run_mod, "_summarize_with_fallback", lambda d: summaries)

    stats = run_mod.refresh_pipeline("test-job")["stats"]
    assert stats["raw"] == 3
    assert stats["dup"] == 1
    assert stats["kept"] == 2
    assert stats["per_source"]["official"]["fetched"] == 3

    from app.db import query
    items = query("SELECT * FROM items ORDER BY id")
    assert len(items) == 2
    assert items[0]["image_url"] == "https://img/1.jpg"
    assert items[0]["summary"] == "模型A发布了"


def test_refresh_records_source_error(fresh_db, monkeypatch):
    from app.pipeline import run as run_mod
    from app.pipeline.http import SourceError

    def bad(client):
        raise SourceError("HTTP 403")

    monkeypatch.setattr(run_mod, "FETCHERS", {"weibo_hot": bad})
    monkeypatch.setattr(run_mod, "_summarize_with_fallback", lambda d: {})

    result = run_mod.refresh_pipeline("test-job")["stats"]
    assert result["per_source"]["weibo_hot"]["error"] == "HTTP 403"
    assert result["kept"] == 0

    from app.db import query_one
    row = query_one("SELECT * FROM fetch_runs ORDER BY id DESC LIMIT 1")
    assert row["status"] == "done"


def test_refresh_persists_last_refresh_state(fresh_db, monkeypatch):
    from app.pipeline import run as run_mod

    monkeypatch.setattr(run_mod, "FETCHERS", {}, raising=False)
    monkeypatch.setattr(run_mod, "_summarize_with_fallback", lambda d: {})
    run_mod.refresh_pipeline("test-job")

    from app.db import get_state
    assert get_state("last_refresh_at") != ""


# ---------- 资讯接口 ----------

@pytest.fixture
def client(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _seed_items():
    from app.db import execute

    return [
        execute(
            "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day, is_read) VALUES (?,?,?,?,?,?,?,?,?)",
            ("qbitai", "media", f"AI新闻{i}", f"http://s/{i}", f"摘要{i}", "大模型", "2026-10-03T08:00:00", "2026-10-03", 1 if i == 0 else 0),
        )
        for i in range(3)
    ]


def test_feed_list_and_filters(client):
    _seed_items()
    body = client.get("/api/feed").json()
    assert body["total"] == 3

    body = client.get("/api/feed", params={"unread": True}).json()
    assert body["total"] == 2

    body = client.get("/api/feed", params={"q": "AI新闻1"}).json()
    assert body["total"] == 1

    body = client.get("/api/feed", params={"source_type": "overseas"}).json()
    assert body["total"] == 0


def test_item_read_star(client):
    ids = _seed_items()
    r = client.patch(f"/api/items/{ids[0]}", json={"is_read": False, "is_starred": True})
    assert r.json()["is_read"] == 0
    assert r.json()["is_starred"] == 1

    body = client.get("/api/feed", params={"starred": True}).json()
    assert body["total"] == 1


def test_item_404(client):
    assert client.patch("/api/items/999", json={"is_read": True}).status_code == 404


def test_read_all(client):
    _seed_items()
    r = client.post("/api/feed/read-all").json()
    assert r["marked"] == 2
    body = client.get("/api/feed", params={"unread": True}).json()
    assert body["total"] == 0


# ---------- 抓取降级与图片解析（2026-10-03 配图反馈） ----------

HTML_WITH_PICTURE = '''
<html><head><meta property="og:image" content="https://cdn.example/hero.png"></head><body>
<p>正文段落。</p>
<picture><source srcset="https://cdn.example/a.png?w=640 640w, https://cdn.example/a.png?w=3840 3840w" type="image/webp">
<img src="https://cdn.example/a.png?w=3840"></picture>
<img src="https://cdn.example/upload-banner.png">
<img src="https://cdn.example/qrcode.png">
<img src="https://cdn.example/logo.png">
<img src="data:image/png;base64,xxx">
</body></html>'''


def test_parse_article_picture_srcset_and_filters():
    from app.pipeline.extract import parse_article

    text, imgs = parse_article(HTML_WITH_PICTURE, "https://example.com/post")
    assert "正文段落" in text
    # og:image 在最前
    assert imgs[0] == "https://cdn.example/hero.png"
    # srcset 取最大宽度版本（3840w）
    assert "w=3840" in imgs[1]
    # upload- 开头图不被 ad- 误伤；qrcode/logo/data: 被过滤
    assert any("upload-banner" in u for u in imgs)
    assert not any("qrcode" in u or "logo" in u for u in imgs)
    assert all(not u.startswith("data:") for u in imgs)


def test_extract_falls_back_on_403(monkeypatch):
    """直连 403 时自动降级浏览器伪装（curl_cffi）。"""
    import httpx

    from app.pipeline import extract as ex

    calls = []

    class FakeDirect:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            calls.append(("direct", url))
            raise httpx.HTTPStatusError("403", request=None, response=type("R", (), {"status_code": 403})())

    class FakeCurlResp:
        status_code = 200
        text = HTML_WITH_PICTURE

    class FakeCurl:
        def get(self, url, **kw):
            calls.append(("curl", url, kw.get("impersonate")))
            return FakeCurlResp()

    monkeypatch.setattr(ex, "http", lambda: FakeDirect())
    monkeypatch.setattr(ex, "_get_curl_cffi", lambda: FakeCurl())
    text, imgs = ex.extract_fulltext("https://openai.com/index/x")
    assert calls[0][0] == "direct" and calls[1][0] == "curl"
    assert calls[1][2] == "chrome124"
    assert "正文段落" in text and imgs


def test_extract_no_fallback_module(monkeypatch):
    """curl_cffi 不可用时保留直连失败的原样（返回空）。"""
    import httpx

    from app.pipeline import extract as ex

    class FakeDirect:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            raise httpx.HTTPStatusError("403", request=None, response=type("R", (), {"status_code": 403})())

    monkeypatch.setattr(ex, "http", lambda: FakeDirect())
    monkeypatch.setattr(ex, "_get_curl_cffi", lambda: None)
    text, imgs = ex.extract_fulltext("https://blocked.example/x")
    assert text == "" and imgs == []


# ---------- 历史归档与热度排序（2026-10-04 EchoBird 风格改版） ----------

def test_feed_archive_days(client):
    _seed_items()
    body = client.get("/api/feed/archive").json()
    assert body["days"] and body["days"][0]["day"] == "2026-10-03"
    assert body["days"][0]["n"] == 3


def test_feed_hot_sort(client):
    from app.db import execute

    ids = _seed_items()
    hid = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at) "
        "VALUES ('超热事件', 'w', '[]', '30s', 95, '2026-10-03', 'x', 'x')"
    )
    low = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at) "
        "VALUES ('冷门事件', 'w', '[]', '30s', 40, '2026-10-03', 'x', 'x')"
    )
    execute("UPDATE items SET event_id=? WHERE id=?", (hid, ids[1]))
    execute("UPDATE items SET event_id=? WHERE id=?", (low, ids[2]))

    body = client.get("/api/feed", params={"sort": "hot"}).json()
    first = body["items"][0]
    assert first["title"] == "AI新闻1"  # 挂在95分热点上的排最前
    assert first["event_score"] == 95
    assert first["source_name"] == "量子位"  # 来源名带出


# ---------- 资讯国内/国外分区（2026-10-04 用户反馈） ----------

def test_feed_region_filter(client):
    from app.db import execute

    ids = _seed_items()  # qbitai = 国内
    execute(
        "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day) "
        "VALUES ('hackernews','overseas','Overseas news','http://hn/1','sum','大模型','x','2026-10-03')"
    )
    body = client.get("/api/feed", params={"region": "国内"}).json()
    assert body["total"] == 3
    assert all(i["source_key"] != "hackernews" for i in body["items"])

    body = client.get("/api/feed", params={"region": "海外"}).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Overseas news"


def test_sources_have_region(client):
    from app.db import query

    rows = query("SELECT key, region FROM sources")
    regions = {r["key"]: r["region"] for r in rows}
    assert regions["qbitai"] == "国内"
    assert regions["hackernews"] == "海外"
    assert regions["official"] == "海外"
    assert regions["qwen"] == "国内"


# ---------- 今日待办（收藏→首页待办）与旧闻过滤（2026-10-04 用户反馈） ----------

def test_starred_becomes_todo(client):
    from app.db import execute

    ids = _seed_items()
    client.patch(f"/api/items/{ids[1]}", json={"is_starred": True})

    body = client.get("/api/todo").json()
    assert len(body["todos"]) == 1
    assert body["todos"][0]["id"] == ids[1]

    # 完成后退出待办
    client.patch(f"/api/items/{ids[1]}", json={"todo_done": True})
    body = client.get("/api/todo").json()
    assert body["todos"] == []

    # 首页 payload 带待办
    payload = client.get("/api/today").json()
    assert payload["todos"] == []


def test_stale_news_filtered():
    """原文发布时间超过15天的旧闻不入库。"""
    from datetime import datetime, timedelta, timezone

    from app.pipeline.run import _is_stale

    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fresh = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert _is_stale(old) is True
    assert _is_stale(fresh) is False
    assert _is_stale("") is False  # 缺失视为新鲜
    assert _is_stale("not-a-date") is False


# ---------- 收藏转选题 + 内容主体地区（2026-10-04 用户反馈） ----------

def test_promote_item_to_hotspot(client):
    from app.db import execute

    ids = _seed_items()
    client.patch(f"/api/items/{ids[0]}", json={"is_starred": True})
    r = client.post(f"/api/items/{ids[0]}/promote")
    assert r.status_code == 200
    hid = r.json()["hotspot_id"]

    detail = client.get(f"/api/hotspots/{hid}").json()
    assert detail["hotspot"]["status"] == "pending"
    assert detail["hotspot"]["sources_count"] == 1  # 来源挂上了
    assert len(detail["sources"]) == 1

    # 待办自动完成
    body = client.get("/api/todo").json()
    assert body["todos"] == []

    # 重复点击转选题：返回同一张卡，不产生重复（审查L8）
    r2 = client.post(f"/api/items/{ids[0]}/promote")
    assert r2.json()["hotspot_id"] == hid
    assert r2.json().get("existed") is True


def test_item_region_from_content(fresh_db, monkeypatch):
    """region 按内容主体判定（LLM 输出），而不是媒体站点国籍。"""
    from app.pipeline.run import _is_stale  # noqa: F401 确认模块可导入

    from app.db import query_one

    # 模拟：爱范儿（国内站点）报道 Gemini（海外事件）
    from app.config import SOURCE_REGIONS

    assert SOURCE_REGIONS["ifanr"] == "国内"  # 站点是国内
    # summarize 输出 region=海外 时应覆盖站点映射
    # 直接验证落库路径：_summarize_with_fallback 返回 region 后写入 items.region
    from app.pipeline import run as run_mod

    drafts = [{
        "source_key": "ifanr", "source_type": "media", "title": "Gemini 4 发布",
        "url": "http://x/1", "published_at": "", "summary_html": "", "rank": 0,
    }]
    monkeypatch.setattr(run_mod, "FETCHERS", {"ifanr": lambda c: drafts})
    monkeypatch.setattr(run_mod, "_existing_urls", lambda: set())
    monkeypatch.setattr(run_mod, "_recent_titles", lambda: [])
    monkeypatch.setattr(
        run_mod, "_summarize_with_fallback",
        lambda d: {0: {"summary": "s", "category": "大模型", "summary_zh": "", "region": "海外"}},
    )
    run_mod.refresh_pipeline("job")

    row = query_one("SELECT region FROM items WHERE title='Gemini 4 发布'")
    assert row["region"] == "海外"  # 内容主体优先于站点国籍

    # region 过滤
    import time as _t

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        body = c.get("/api/feed", params={"region": "海外"}).json()
        assert body["total"] == 1
        body = c.get("/api/feed", params={"region": "国内"}).json()
        assert body["total"] == 0


def test_read_all_scoped_to_filters(client):
    """「全部标为已读」只作用于当前筛选范围（修：之前会全库标掉）。"""
    from app.db import execute, query_one

    ids = _seed_items()  # 3 条国内 qbitai，category=大模型
    execute(
        "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day, region) "
        "VALUES ('hackernews','overseas','海外未读','http://hn/9','sum','AI产品','x','2026-10-03','海外')"
    )

    # 在 category=大模型 筛选下点全部已读
    r = client.post("/api/feed/read-all", params={"category": "大模型"}).json()
    assert r["marked"] == 2  # 种子第1条本就是已读，海外条不在该筛选

    # 海外那条不受影响
    assert query_one("SELECT is_read FROM items WHERE title='海外未读'")["is_read"] == 0

    # 海外范围下再点
    r = client.post("/api/feed/read-all", params={"region": "海外"}).json()
    assert r["marked"] == 1
    assert query_one("SELECT is_read FROM items WHERE title='海外未读'")["is_read"] == 1


def test_feed_category_filter_no_500(client):
    """回归：分类过滤曾因列名歧义（items/hotspots 都有 category）必然 500。"""
    _seed_items()  # category=大模型
    from app.db import execute

    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, category) "
        "VALUES ('某热点', 'w', '[]', '30s', 90, '2026-10-03', 'x', 'x', '大模型')"
    )
    r = client.get("/api/feed", params={"category": "大模型"})
    assert r.status_code == 200  # 修复前此处 500
    body = r.json()
    assert body["total"] == 3
    assert all(i["category"] == "大模型" for i in body["items"])


# ---------- 抓取/摘要健壮性（审查M2/M3） ----------

def test_parse_hot_rejects_null_shape():
    """热搜接口返回 data:null 时抛 ValueError（由 run 兜底），不再 AttributeError 炸全场。"""
    import pytest

    from app.pipeline.fetchers import parse_baidu_hot, parse_hackernews, parse_weibo_hot

    for fn in (parse_baidu_hot, parse_weibo_hot):
        with pytest.raises(ValueError):
            fn({"data": None})
    with pytest.raises(ValueError):
        parse_hackernews({"hits": None})
    with pytest.raises(ValueError):
        parse_hackernews([1, 2])  # 非 dict


def test_summarize_bad_index_only_skips_one():
    """LLM 返回一个坏序号：只跳过该条，好数据不再整批降级（审查M3）。"""
    items = [{"i": i, "title": f"标题{i}", "text": "t", "overseas": False} for i in range(3)]
    llm = FakeLLM([{"results": [
        {"i": 0, "summary": "好摘要0", "category": "大模型", "summary_zh": ""},
        {"i": "bad", "summary": "坏序号", "category": "大模型", "summary_zh": ""},
        {"i": 2, "summary": "好摘要2", "category": "AI产品", "summary_zh": ""},
    ]}])
    out = summarize_items(items, llm)
    assert out[0]["summary"] == "好摘要0"
    assert out[2]["summary"] == "好摘要2"
    assert 1 not in out  # 坏序号条目跳过（走标题降级由上层处理）


def test_refresh_survives_fetcher_crash(fresh_db, monkeypatch):
    """单源抛非 SourceError 异常：其余源照常，fetch_runs 正常收尾（审查M2）。"""
    from app.pipeline import run as run_mod

    def boom(client):
        raise AttributeError("'NoneType' object has no attribute 'get'")

    monkeypatch.setattr(run_mod, "FETCHERS", {"baidu_hot": boom})
    monkeypatch.setattr(run_mod, "_summarize_with_fallback", lambda d: {})

    result = run_mod.refresh_pipeline("job")
    assert result["stats"]["per_source"]["baidu_hot"]["error"].startswith("AttributeError")

    from app.db import query_one
    row = query_one("SELECT status FROM fetch_runs ORDER BY id DESC LIMIT 1")
    assert row["status"] == "done"  # 修复前永久 running


def test_summarize_region_rule_names_zhipu():
    """地区判断规则里的公司名不能有错字（"智硬"会让智谱动态归类失灵）。"""
    from app.pipeline import summarize

    assert "智谱" in summarize.SYSTEM
    assert "智硬" not in summarize.SYSTEM
