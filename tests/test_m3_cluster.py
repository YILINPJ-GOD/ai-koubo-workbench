"""M3 测试：打分选择、事件清洗、热点落库合并、续集标记、今日聚合、接口。"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.pipeline.cluster import (
    build_cluster_payload,
    parse_events,
    upsert_hotspots,
)
from app.pipeline.score import normalize_event, pick_must_and_backup

TODAY = time.strftime('%Y-%m-%d')


# ---------- 打分与清洗 ----------

def test_pick_caps():
    events = [{"score": s} for s in [95, 90, 85, 82, 78, 70, 65, 62, 50, 40]]
    must, backup = pick_must_and_backup(events)
    assert len(must) == 4
    assert [events[i]["score"] for i in must] == [95, 90, 85, 82]
    assert len(backup) == 4
    assert [events[i]["score"] for i in backup] == [78, 70, 65, 62]


def test_pick_empty_day():
    must, backup = pick_must_and_backup([{"score": 55}, {"score": 30}])
    assert must == [] and backup == []


def test_normalize_event_cleans():
    ev = normalize_event({
        "title": "  某事件  ",
        "score": "999",
        "suggested_length": "90s",
        "angles": "单一角度",
        "item_ids": ["1", "x", 3],
        "why": "理由",
    })
    assert ev["title"] == "某事件"
    assert ev["score"] == 100
    assert ev["suggested_length"] == "30s"
    assert ev["angles"] == ["单一角度"]
    assert ev["item_ids"] == [1, 3]


def test_normalize_event_rejects_empty_title():
    assert normalize_event({"title": ""}) is None


def test_parse_events():
    events, comment = parse_events({
        "events": [{"title": "A", "score": 90, "item_ids": [1]}],
        "day_comment": "今天不错",
    })
    assert len(events) == 1 and comment == "今天不错"


# ---------- 落库 ----------

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_HOME", str(tmp_path / "home"))
    from app import config as app_config
    from app import db as app_db

    app_config.ensure_dirs()
    app_db.init_db()
    yield tmp_path


def _seed_items(rows):
    from app.db import execute

    ids = []
    for source_key, source_type, title in rows:
        ids.append(execute(
            "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (source_key, source_type, title, f"http://s/{title}", "摘要", "大模型",
             "2026-10-03T08:00:00", TODAY),
        ))
    return ids


def test_upsert_new_and_merge(fresh_db):
    ids = _seed_items([
        ("qbitai", "media", "事件甲-报道一"),
        ("ifanr", "media", "事件甲-报道二"),
        ("36kr", "media", "事件乙"),
    ])
    events = [
        {"title": "事件甲", "why": "w", "angles": ["a"], "suggested_length": "30s",
         "score": 85, "item_ids": [ids[0], ids[1]], "sequel_of": ""},
        {"title": "事件乙", "why": "w", "angles": [], "suggested_length": "15s",
         "score": 70, "item_ids": [ids[2]], "sequel_of": ""},
    ]
    stats = upsert_hotspots(events)
    assert stats["new"] == 2 and stats["must"] == 1 and stats["backup"] == 1

    from app.db import query, query_one
    rows = query("SELECT * FROM hotspots ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["sources_count"] == 2  # 两个独立来源
    assert rows[0]["is_must"] == 1
    # 70分卡原为备选；必做保底（不足4条）把它递补成必做
    assert rows[1]["is_must"] == 1 and rows[1]["is_backup"] == 0
    assert stats["backfilled"] == 1

    # 再次聚合同一事件：合并而不是新建
    stats2 = upsert_hotspots([events[0]])
    assert stats2["new"] == 0 and stats2["merged"] == 1
    assert len(query("SELECT * FROM hotspots")) == 2


def test_upsert_sequel_link(fresh_db):
    from app.db import execute, query_one

    old_id = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status) "
        "VALUES ('某大模型发布', '', '[]', '30s', 90, '2026-09-28', '2026-09-28T08:00:00', '2026-09-28T08:00:00', 'shot')"
    )
    _seed_items([("qbitai", "media", "后续进展")])
    ev = {"title": "某大模型发布后续", "why": "w", "angles": [], "suggested_length": "30s",
          "score": 75, "item_ids": [1], "sequel_of": "某大模型发布"}
    upsert_hotspots([ev])
    row = query_one("SELECT * FROM hotspots WHERE title='某大模型发布后续'")
    assert row["sequel_of"] == old_id


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, system, user, retries=2):
        self.last_user = user
        return self.payload


def test_cluster_and_store_empty_day(fresh_db, monkeypatch):
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    _seed_items([("qbitai", "media", "平淡新闻一")])
    fake = FakeLLM({"events": [], "day_comment": "今天全是软文，不值得做"})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)
    stats = cluster_mod.cluster_and_store()
    assert stats["new"] == 0

    from app.db import get_state, query
    assert query("SELECT * FROM hotspots") == []
    reason = get_state(f"empty_reason_{time.strftime('%Y-%m-%d')}")
    assert "不值得" in reason


def test_cluster_and_store_with_events(fresh_db, monkeypatch):
    import app.llm as llm_mod
    from app.pipeline import cluster as cluster_mod

    ids = _seed_items([("qbitai", "media", "新闻A"), ("official", "official", "新闻B")])
    fake = FakeLLM({"events": [
        {"title": "大事件", "why": "多家报道", "angles": ["角度一"], "suggested_length": "30s",
         "score": 88, "item_ids": ids, "sequel_of": ""},
    ], "day_comment": ""})
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake)
    stats = cluster_mod.cluster_and_store()
    assert stats["must"] == 1

    from app.db import get_state, query
    rows = query("SELECT * FROM hotspots")
    assert rows[0]["is_must"] == 1
    # items 关联到事件
    items = query("SELECT event_id FROM items")
    assert all(it["event_id"] == rows[0]["id"] for it in items)
    # 必做非空时清空理由
    assert get_state(f"empty_reason_{time.strftime('%Y-%m-%d')}") == ""


def test_cluster_payload_contains_history(fresh_db):
    from app.db import execute

    execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, created_at, updated_at, status) "
        "VALUES ('旧选题', '', '[]', '30s', 90, '2026-09-28', 'x', 'x', 'shot')"
    )
    payload = json.loads(build_cluster_payload(
        [{"id": 1, "title": "新", "summary": "s", "category": "", "source_type": "media"}],
        [{"id": 1, "title": "旧选题", "day": "2026-09-28", "status": "shot"}],
    ))
    assert payload["recent_done_topics"][0]["title"] == "旧选题"
    assert payload["today_items"][0]["i"] == 1


# ---------- 接口 ----------

@pytest.fixture
def client(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _seed_hotspot(status="pending", **kw):
    from app.db import execute

    return execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, day, cover, created_at, updated_at, status, is_must) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            kw.get("title", "测试热点"), "值得讲", json.dumps(["角度A"], ensure_ascii=False),
            "30s", 88, TODAY, "", "2026-10-03T08:00:00", "2026-10-03T08:00:00",
            status, kw.get("must", 1),
        ),
    )


def test_today_payload(client):
    _seed_hotspot()
    body = client.get("/api/today").json()
    assert body["date"] == time.strftime("%Y-%m-%d")
    assert len(body["must"]) == 1
    assert body["must"][0]["title"] == "测试热点"
    assert body["must"][0]["angles"] == ["角度A"]
    assert "funnel" in body and "key_configured" in body


def test_today_empty_day_reason(client):
    from app.db import set_state

    set_state(f"empty_reason_{time.strftime('%Y-%m-%d')}", "今天没有值得做的")
    body = client.get("/api/today").json()
    assert body["must"] == []
    assert "没有值得" in body["empty_reason"]


def test_hotspots_list_and_detail(client):
    hid = _seed_hotspot()
    body = client.get("/api/hotspots").json()
    assert len(body["hotspots"]) == 1

    body = client.get(f"/api/hotspots/{hid}").json()
    assert body["hotspot"]["title"] == "测试热点"
    assert body["pack"] is None

    assert client.get("/api/hotspots/999").status_code == 404


def test_mark_shot_flow(client):
    hid = _seed_hotspot(status="packed")
    body = client.post(f"/api/hotspots/{hid}/mark-shot").json()
    assert body["status"] == "shot"

    body = client.get("/api/hotspots", params={"status": "shot"}).json()
    assert len(body["hotspots"]) == 1


def test_reevaluate_job(client, monkeypatch):
    import app.api.today as today_api

    calls = []
    monkeypatch.setattr(today_api, "cluster_and_store", lambda job_id="": calls.append(job_id) or {"new": 0})
    r = client.post("/api/today/reevaluate").json()
    assert "job_id" in r
    for _ in range(50):
        job = client.get(f"/api/jobs/{r['job_id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert len(calls) == 1
