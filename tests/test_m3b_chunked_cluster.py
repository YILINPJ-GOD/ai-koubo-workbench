"""M3 补充测试：分批聚合、跨批合并、单批失败容忍、LLM 超时参数。"""
import json
import time

import pytest

TODAY = time.strftime('%Y-%m-%d')


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_HOME", str(tmp_path / "home"))
    from app import config as app_config
    from app import db as app_db

    app_config.ensure_dirs()
    app_db.init_db()
    yield tmp_path


def _seed_items(n):
    from app.db import execute

    ids = []
    for i in range(n):
        ids.append(execute(
            "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"src{i % 3}", "media", f"资讯标题第{i}条", f"http://s/{i}", f"摘要{i}", "大模型",
             "2026-10-03T08:00:00", TODAY),
        ))
    return ids


class ChunkAwareLLM:
    """对每一批都返回 1 个事件；记录每批的条目数。标题完全不同，不应触发合并。"""

    def __init__(self):
        self.chunk_sizes = []
        self.topics = ["芯片大事件", "手机大事件", "卫星大事件", "机器人事件"]

    def chat_json(self, system, user, retries=2):
        payload = json.loads(user)
        self.chunk_sizes.append(len(payload["today_items"]))
        topic = self.topics[len(self.chunk_sizes) - 1]
        return {"events": [
            {"title": topic, "why": "w", "angles": [],
             "suggested_length": "30s", "score": 85, "item_ids": [it["i"] for it in payload["today_items"]],
             "sequel_of": ""}
        ], "day_comment": ""}


def test_cluster_chunked_over_25_items(fresh_db, monkeypatch):
    """61 条资讯应拆成 3 批调用，而不是一次巨型调用。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    ids = _seed_items(61)
    fake = ChunkAwareLLM()
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)

    stats = cluster_mod.cluster_and_store()
    assert fake.chunk_sizes == [25, 25, 11]
    assert stats["new"] == 3

    from app.db import query
    assert len(query("SELECT * FROM hotspots")) == 3
    # 每条资讯都挂上了事件
    linked = query("SELECT COUNT(*) AS n FROM items WHERE event_id IS NOT NULL")[0]["n"]
    assert linked == 61


def test_cluster_merges_cross_chunk_duplicates(fresh_db, monkeypatch):
    """不同批次里的同事件（标题相似）应合并为一张卡。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    ids = _seed_items(30)
    # 手动构造：两批返回相似标题的事件
    calls = {"n": 0}

    class TwoChunkLLM:
        def chat_json(self, system, user, retries=2):
            calls["n"] += 1
            payload = json.loads(user)
            chunk_ids = [it["i"] for it in payload["today_items"]]
            if calls["n"] == 1:
                title = "OpenAI 发布 GPT-5.5 并全面降价"
            else:
                title = "OpenAI发布GPT5.5，API价格砍半"  # 相似标题
            return {"events": [
                {"title": title, "why": "多家报道", "angles": ["角度A"],
                 "suggested_length": "30s", "score": 88 if calls["n"] == 1 else 90,
                 "item_ids": chunk_ids, "sequel_of": ""}
            ], "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: TwoChunkLLM())
    stats = cluster_mod.cluster_and_store()
    assert calls["n"] == 2
    assert stats["new"] == 1  # 合并成一张卡
    assert stats["must"] == 1

    from app.db import query, query_one
    h = query_one("SELECT * FROM hotspots")
    assert h["score"] == 90  # 取高分
    assert h["sources_count"] >= 2  # item_ids 并集后跨来源
    assert len(query("SELECT * FROM hotspots")) == 1


def test_cluster_partial_chunk_failure_tolerated(fresh_db, monkeypatch):
    """一批失败、其余成功：整体不报错，成功的批次照常落库。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    _seed_items(30)
    calls = {"n": 0}

    class FlakyLLM:
        def chat_json(self, system, user, retries=2):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("LLM 超时")
            payload = json.loads(user)
            return {"events": [
                {"title": "幸存事件", "why": "w", "angles": [], "suggested_length": "30s",
                 "score": 82, "item_ids": [it["i"] for it in payload["today_items"]], "sequel_of": ""}
            ], "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: FlakyLLM())
    stats = cluster_mod.cluster_and_store()
    assert stats["new"] == 1
    assert "partial_error" in stats


def test_cluster_all_chunks_fail_raises(fresh_db, monkeypatch):
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod
    from app.pipeline.http import SourceError

    _seed_items(5)

    class DeadLLM:
        def chat_json(self, system, user, retries=2):
            raise RuntimeError("LLM 全挂")

    monkeypatch.setattr(llm_mod, "get_llm", lambda: DeadLLM())
    with pytest.raises(SourceError):
        cluster_mod.cluster_and_store()


def test_merge_events_locally_pure():
    from app.pipeline.cluster import merge_events_locally

    events = [
        {"title": "甲事件", "why": "短", "angles": ["A"], "score": 80,
         "item_ids": [1, 2], "suggested_length": "30s", "sequel_of": ""},
        {"title": "甲事件（后续报道）", "why": "更长的理由描述", "angles": ["B"], "score": 90,
         "item_ids": [2, 3], "suggested_length": "60s", "sequel_of": "旧选题"},
    ]
    merged = merge_events_locally(events)
    assert len(merged) == 1
    assert merged[0]["score"] == 90
    assert merged[0]["item_ids"] == [1, 2, 3]
    assert merged[0]["angles"] == ["A", "B"]
    assert merged[0]["why"] == "更长的理由描述"
    assert merged[0]["sequel_of"] == "旧选题"


def test_llm_client_passes_timeout(monkeypatch):
    """LLM 客户端应给 SDK 传超时，防止单次调用无限挂起。"""
    import sys
    import types

    import app.llm as llm_mod

    captured = {}

    class FakeZhipuAI:
        def __init__(self, api_key=None, timeout=None):
            captured["timeout"] = timeout
            captured["api_key"] = api_key

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("stop here")

    fake_mod = types.ModuleType("zhipuai")
    fake_mod.ZhipuAI = FakeZhipuAI
    monkeypatch.setitem(sys.modules, "zhipuai", fake_mod)

    client = llm_mod.LLMClient("k")
    with pytest.raises(llm_mod.LLMError):
        client._raw("s", "u")
    assert captured["timeout"] == 180.0
    assert captured["api_key"] == "k"


def test_low_score_events_dropped(fresh_db, monkeypatch):
    """分数低于55的事件是噪音，不入库。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    _seed_items(3)

    class NoisyLLM:
        def chat_json(self, system, user, retries=2):
            payload = json.loads(user)
            ids = [it["i"] for it in payload["today_items"]]
            return {"events": [
                {"title": "重要事件", "why": "w", "angles": [], "suggested_length": "30s",
                 "score": 85, "item_ids": ids[:1], "sequel_of": ""},
                {"title": "普通琐事", "why": "w", "angles": [], "suggested_length": "15s",
                 "score": 40, "item_ids": ids[1:], "sequel_of": ""},
            ], "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: NoisyLLM())
    cluster_mod.cluster_and_store()
    from app.db import query
    titles = {r["title"] for r in query("SELECT title FROM hotspots")}
    assert titles == {"重要事件"}


def test_balance_must_by_origin():
    from app.pipeline.score import balance_must_by_origin

    events = [
        {"title": "海外A", "score": 95, "item_ids": [1]},
        {"title": "海外B", "score": 90, "item_ids": [2]},
        {"title": "海外C", "score": 85, "item_ids": [3]},
        {"title": "国内D", "score": 75, "item_ids": [4]},
        {"title": "国内E", "score": 70, "item_ids": [5]},
    ]
    # 全海外必做 → 换入最高分国内备选
    origin_of = lambda i: "海外" if i < 3 else "国内"  # noqa: E731
    must, backup = balance_must_by_origin(events, [0, 1, 2], [3, 4], origin_of)
    origins = {origin_of(i) for i in must}
    assert origins == {"海外", "国内"}
    assert 3 in must and 2 in backup  # 最低分海外被换出

    # 已经兼顾 → 原样返回
    must2, backup2 = balance_must_by_origin(events, [0, 3, 4], [1, 2], origin_of)
    assert must2 == [0, 3, 4]

    # 另一地区备选分数太低（<65）不换
    events_low = [{"title": f"e{i}", "score": s, "item_ids": [i]} for i, s in enumerate([95, 90, 85, 60])]
    origin_low = lambda i: "海外" if i < 3 else "国内"  # noqa: E731
    must3, backup3 = balance_must_by_origin(events_low, [0, 1, 2], [3], origin_low)
    assert must3 == [0, 1, 2]


# ---------- 必做保底递补（2026-10-04 用户反馈：必做至少4-6条） ----------

def _seed_yesterday_hotspots(rows):
    """种热点卡并各挂一条来源资讯（有来源才可递补）。"""
    from app.db import execute

    out = []
    for i, (title, score, status, must) in enumerate(rows):
        hid = execute(
            "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must, category) "
            "VALUES (?,?,?,?,?,?,?,?,?,?, '科技大事件')",
            (title, "w", "[]", "30s", score, "2026-10-02", "x", "2026-10-02T08:00:00", status, must),
        )
        execute(
            "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day, event_id) "
            "VALUES ('qbitai','media',?,?,'摘要','科技大事件','x','2026-10-02',?)",
            (f"来源：{title}", f"http://s/src-{hid}", hid),
        )
        out.append(hid)
    return out


def test_must_floor_backfills_from_library(fresh_db, monkeypatch):
    """今天只有 1 个必做 → 从库里递补未拍高分选题凑满 4；已拍不递补。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    _seed_items(2)
    yesterday = _seed_yesterday_hotspots([
        ("某大模型开源发布", 90, "pending", 0),
        ("某AI办公套件更新", 85, "packed", 0),
        ("某芯片产能扩张", 70, "packed", 0),
        ("某已拍过的发布会", 99, "shot", 0),  # 已拍永不递补
    ])

    class OneEventLLM:
        def chat_json(self, system, user, retries=2):
            payload = json.loads(user)
            ids = [it["i"] for it in payload["today_items"]]
            return {"events": [
                {"title": "今天唯一好选题", "why": "w", "angles": [], "suggested_length": "30s",
                 "score": 88, "item_ids": ids[:1], "sequel_of": ""},
            ], "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: OneEventLLM())
    cluster_mod.cluster_and_store()

    from app.db import query
    musts = query("SELECT title FROM hotspots WHERE is_must=1 ORDER BY score DESC")
    titles = {r["title"] for r in musts}
    assert len(musts) == 4  # 保底到 4
    assert "今天唯一好选题" in titles
    assert "某大模型开源发布" in titles
    assert "某AI办公套件更新" in titles
    assert "某已拍过的发布会" not in titles  # shot 不递补
    # 递补的卡 updated_at 刷新为今天（首页查询依赖）
    row = query("SELECT updated_at FROM hotspots WHERE title='某大模型开源发布'")[0]
    assert row["updated_at"].startswith(time.strftime("%Y-%m-%d"))


def test_must_floor_not_triggered_when_enough(fresh_db, monkeypatch):
    """今天已有 4+ 必做 → 不递补。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    _seed_items(5)
    _seed_yesterday_hotspots([("旧选题", 95, "pending", 0)])

    class FiveEventsLLM:
        topics = ["芯片大突破", "手机新形态", "卫星互联网", "机器人量产", "量子计算进展"]

        def chat_json(self, system, user, retries=2):
            payload = json.loads(user)
            ids = [it["i"] for it in payload["today_items"]]
            evs = [{"title": self.topics[i], "why": "w", "angles": [], "suggested_length": "30s",
                    "score": 90 - i, "item_ids": [ids[i]], "sequel_of": ""} for i in range(5)]
            return {"events": evs, "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: FiveEventsLLM())
    cluster_mod.cluster_and_store()

    from app.db import query
    musts = query("SELECT title FROM hotspots WHERE is_must=1")
    assert len(musts) == 5  # 今天自己的 5 个全在，无需递补
    assert all(r["title"] != "旧选题" for r in musts)
    assert {"芯片大突破", "量子计算进展"} <= {r["title"] for r in musts}


# ---------- 过时候补机制 + 昨日至今语义（2026-10-04 用户反馈） ----------

def test_expire_removes_and_blocks_backfill(fresh_db, monkeypatch):
    """标记过时 → 退出必做且不被递补；恢复后可再被递补。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod
    from app.services.hotspots import expire, reactivate

    _seed_items(2)
    ids = _seed_yesterday_hotspots([
        ("会过时的选题", 92, "pending", 0),
        ("常青选题", 80, "pending", 0),
    ])

    class OneEventLLM:
        def chat_json(self, system, user, retries=2):
            payload = json.loads(user)
            got = [it["i"] for it in payload["today_items"]]
            return {"events": [
                {"title": "今日新事件", "why": "w", "angles": [], "suggested_length": "30s",
                 "score": 70, "item_ids": got[:1], "sequel_of": ""},
            ], "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: OneEventLLM())
    cluster_mod.cluster_and_store()

    from app.db import query
    titles = {r["title"] for r in query("SELECT title FROM hotspots WHERE is_must=1")}
    # 递补时 92 分的会过时选题 + 80 分常青选题 + 今日 70 分，共 3，不满 4（库只有 3 张非 shot 卡）
    assert "会过时的选题" in titles

    # 用户标记过时 → 退出必做
    row = query("SELECT id FROM hotspots WHERE title='会过时的选题'")[0]
    expire(row["id"])
    titles2 = {r["title"] for r in query("SELECT title FROM hotspots WHERE is_must=1")}
    assert "会过时的选题" not in titles2

    # 递补不再选它：再跑一次聚类（无新事件也会尝试保底）
    cluster_mod.cluster_and_store()
    titles3 = {r["title"] for r in query("SELECT title FROM hotspots WHERE is_must=1")}
    assert "会过时的选题" not in titles3

    # 恢复 → 可再次进入递补池
    reactivate(row["id"])
    assert query("SELECT status FROM hotspots WHERE title='会过时的选题'")[0]["status"] == "pending"


def test_today_payload_includes_yesterday_updated(fresh_db, monkeypatch):
    """昨日至今语义：昨天更新过的必做卡今天仍在首页（跨午夜不消失）。"""
    from datetime import datetime, timedelta

    from app.db import execute
    from app.services.today import today_payload

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must) "
        "VALUES ('昨晚大事', 'w', '[]', '30s', 90, ?, 'x', ?, 'pending', 1)",
        (yesterday, f"{yesterday}T23:00:00"),
    )
    payload = today_payload()
    assert any(c["title"] == "昨晚大事" for c in payload["must"])


def test_expired_excluded_from_today(fresh_db):
    """过时的卡不出现在首页必做。"""
    from datetime import datetime, timedelta

    from app.db import execute
    from app.services.today import today_payload

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must) "
        "VALUES ('过时事件', 'w', '[]', '30s', 90, ?, 'x', ?, 'expired', 1)",
        (yesterday, f"{yesterday}T23:00:00"),
    )
    payload = today_payload()
    assert all(c["title"] != "过时事件" for c in payload["must"])


def test_hotspots_active_filter(fresh_db):
    """热点页"今日活跃"筛选 = 昨日至今更新过且未过时。"""
    from datetime import datetime, timedelta

    from app.db import execute
    from app.services.hotspots import list_hotspots

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    long_ago = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status) "
        "VALUES ('活跃卡', '', '[]', '30s', 90, ?, 'x', ?, 'pending')",
        (yesterday, f"{yesterday}T22:00:00"),
    )
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status) "
        "VALUES ('过时卡', '', '[]', '30s', 90, ?, 'x', ?, 'expired')",
        (yesterday, f"{yesterday}T22:00:00"),
    )
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status) "
        "VALUES ('老卡', '', '[]', '30s', 90, ?, 'x', ?, 'pending')",
        (long_ago, f"{long_ago}T22:00:00"),
    )
    titles = {r["title"] for r in list_hotspots(active=True)}
    assert titles == {"活跃卡"}


def test_expire_triggers_immediate_backfill(fresh_db, monkeypatch):
    """点过时后 API 层立刻递补：移出 1 个，马上补 1 个新的。"""
    from app.db import query
    from app.pipeline.cluster import ensure_must_floor
    from app.services.hotspots import expire

    # 库里造 5 张未拍卡（>保底线4），全部标为必做
    _seed_yesterday_hotspots([
        ("卡A", 95, "pending", 1),
        ("卡B", 90, "pending", 1),
        ("卡C", 85, "pending", 1),
        ("卡D", 80, "pending", 1),
        ("候补E", 75, "pending", 0),
    ])
    assert len(query("SELECT id FROM hotspots WHERE is_must=1")) == 4

    # 过时卡A → 应立刻递补候补E
    row = query("SELECT id FROM hotspots WHERE title='卡A'")[0]
    expire(row["id"])
    conn = __import__("app.db", fromlist=["connect"]).connect()
    try:
        ensure_must_floor(conn)
        conn.commit()
    finally:
        conn.close()

    musts = {r["title"] for r in query("SELECT title FROM hotspots WHERE is_must=1")}
    assert "卡A" not in musts
    assert "候补E" in musts
    assert len(musts) == 4  # 保住保底线


def test_today_orders_new_events_first(fresh_db):
    """首页必做按事件日期排序：今天的新事件排在昨天递补的旧事件前。"""
    from datetime import datetime, timedelta

    from app.db import execute
    from app.services.today import today_payload

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")
    # 先种昨天的高分旧事件（递补），再种今天的低分新事件
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must) "
        "VALUES ('昨日旧事件', '', '[]', '30s', 99, ?, 'x', ?, 'pending', 1)",
        (yesterday, f"{yesterday}T23:00:00"),
    )
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must) "
        "VALUES ('今日新事件', '', '[]', '30s', 81, ?, 'x', ?, 'pending', 1)",
        (today, now),
    )
    payload = today_payload()
    assert [c["title"] for c in payload["must"]][:1] == ["今日新事件"]


def test_offtopic_event_cannot_be_must(fresh_db, monkeypatch):
    """与 AI 定位无关的事件（category 无关/缺失）最多做备选，不得进必做。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    _seed_items(2)

    class OfftopicLLM:
        def chat_json(self, system, user, retries=2):
            payload = json.loads(user)
            ids = [it["i"] for it in payload["today_items"]]
            return {"events": [
                {"title": "某云操作系统发布", "why": "w", "angles": [], "suggested_length": "30s",
                 "score": 95, "item_ids": ids[:1], "category": "无关", "sequel_of": ""},
                {"title": "某大模型发布", "why": "w", "angles": [], "suggested_length": "30s",
                 "score": 82, "item_ids": ids[1:], "category": "大模型", "sequel_of": ""},
            ], "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: OfftopicLLM())
    cluster_mod.cluster_and_store()

    from app.db import query
    musts = {r["title"] for r in query("SELECT title FROM hotspots WHERE is_must=1")}
    assert "某云操作系统发布" not in musts  # 无关事件被压分
    assert "某大模型发布" in musts


def test_backfill_skips_duplicate_of_existing_must(fresh_db):
    """递补候选与在位必做是同事件（中英双源卡）→ 跳过，补真正的新卡。"""
    from app.db import execute
    from app.pipeline.cluster import ensure_must_floor

    today = time.strftime('%Y-%m-%d')
    now = today + "T09:00:00"
    # 在位必做：英文题
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must, category) "
        "VALUES ('Introducing Gemini 4 Argon', 'w', '[]', '30s', 90, ?, 'x', ?, 'pending', 1, '大模型')",
        (today, now),
    )
    # 候选1：中文重复卡（同事件）；候选2：真正的新卡
    dup = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must, category) "
        "VALUES ('推出Gemini 4 Argon：前沿智能的新时代。', 'w', '[]', '30s', 90, ?, 'x', ?, 'pending', 0, '大模型')",
        (today, now),
    )
    fresh = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must, category) "
        "VALUES ('某AI新品类事件', 'w', '[]', '30s', 85, ?, 'x', ?, 'pending', 0, 'AI产品')",
        (today, now),
    )
    for hid in (dup, fresh):
        execute(
            "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day, event_id) "
            "VALUES ('ifanr','media',?,?,'摘要','AI产品','x',?,?)",
            (f"来源{hid}", f"http://s/src-{hid}", today, hid),
        )

    conn = __import__("app.db", fromlist=["connect"]).connect()
    try:
        added = ensure_must_floor(conn)
        conn.commit()
    finally:
        conn.close()

    musts = {r["title"] for r in __import__("app.db", fromlist=["query"]).query(
        "SELECT title FROM hotspots WHERE is_must=1")}
    assert added == 1
    assert "推出Gemini 4 Argon：前沿智能的新时代。" not in musts  # 重复卡被跳过
    assert "某AI新品类事件" in musts  # 补的是真新卡


# ---------- 孤卡治理（2026-10-04 用户反馈：来源0的热点不用/找回） ----------

def test_hallucinated_ids_orphan_not_recommended(fresh_db, monkeypatch):
    """LLM 给了不存在的 item_ids 且标题找不回来源 → 事件不进必做/备选。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    _seed_items(2)

    class HallucinationLLM:
        def chat_json(self, system, user, retries=2):
            return {"events": [
                {"title": "完全无关的孤事件", "why": "w", "angles": [], "suggested_length": "30s",
                 "score": 95, "item_ids": [999, 888], "sequel_of": ""},  # 幻觉 id
            ], "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: HallucinationLLM())
    stats = cluster_mod.cluster_and_store()
    assert stats["orphan"] == 1

    from app.db import query
    cards = query("SELECT * FROM hotspots")
    assert len(cards) == 1  # 保留记录
    assert cards[0]["is_must"] == 0 and cards[0]["is_backup"] == 0


def test_orphan_source_recovered_by_title(fresh_db, monkeypatch):
    """幻觉 id 但标题能与当日资讯匹配 → 自动找回来源。"""
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    _seed_items(2)  # 标题：资讯标题第0条/第1条

    class RewriteLLM:
        def chat_json(self, system, user, retries=2):
            payload = json.loads(user)
            real_title = payload["today_items"][0]["title"]
            return {"events": [
                {"title": real_title + "，官方全文通报", "why": "w", "angles": [],
                 "suggested_length": "30s", "score": 88, "item_ids": [777], "sequel_of": ""},
            ], "day_comment": ""}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: RewriteLLM())
    stats = cluster_mod.cluster_and_store()
    assert stats["orphan"] == 0

    from app.db import query_one, query
    h = query_one("SELECT * FROM hotspots")
    assert h["is_must"] == 1  # 找回来源后正常推荐
    linked = query("SELECT event_id FROM items WHERE event_id IS NOT NULL")
    assert len(linked) == 1 and linked[0]["event_id"] == h["id"]


def test_backfill_requires_sources(fresh_db):
    """递补只递补有来源的热点（孤卡不递补）。"""
    from app.db import execute, query
    from app.pipeline.cluster import ensure_must_floor

    today = time.strftime('%Y-%m-%d')
    # 孤卡（无 items）+ 有来源卡
    orphan = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must, category) "
        "VALUES ('孤卡高分', 'w', '[]', '30s', 99, ?, 'x', 'x', 'pending', 0, '大模型')",
        (today,),
    )
    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status, is_must, category) "
        "VALUES ('有源卡', 'w', '[]', '30s', 80, ?, 'x', 'x', 'pending', 0, '大模型')",
        (today,),
    )
    execute(
        "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day, event_id) "
        "VALUES ('qbitai','media','有源卡来源','http://s/9','摘要','AI产品','x',?,?)",
        (today, execute("SELECT 0") + 0 or 0),
    )
    # 关联：把 item 挂到有源卡
    from app.db import query_one

    card = query_one("SELECT id FROM hotspots WHERE title='有源卡'")
    execute("UPDATE items SET event_id=? WHERE title='有源卡来源'", (card["id"],))

    conn = __import__("app.db", fromlist=["connect"]).connect()
    try:
        ensure_must_floor(conn)
        conn.commit()
    finally:
        conn.close()

    musts = {r["title"] for r in query("SELECT title FROM hotspots WHERE is_must=1")}
    assert "有源卡" in musts
    assert "孤卡高分" not in musts
