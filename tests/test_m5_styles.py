"""M5 测试：拆解五件套、模板 CRUD、查重、套用生成。"""
import json
import time

TODAY = time.strftime('%Y-%m-%d')

import pytest
from fastapi.testclient import TestClient

from app.services.styles import copied_fragment, normalize_teardown


# ---------- 拆解清洗 ----------

def test_normalize_teardown():
    data = {
        "hook_type": "数字冲击",
        "structure": "第1段抛冲突；第2段给数据；第3段升华",
        "rhythm": "短句为主",
        "golden_pattern": "对比式金句",
        "cta_type": "关注引导",
        "extra": "多余字段",
    }
    t = normalize_teardown(data)
    assert t["structure"] == ["第1段抛冲突", "第2段给数据", "第3段升华"]
    assert "extra" not in t
    assert t["hook_type"] == "数字冲击"


def test_normalize_teardown_list_structure():
    t = normalize_teardown({"structure": ["钩子", "反转"], "hook_type": ""})
    assert t["structure"] == ["钩子", "反转"]


# ---------- 查重 ----------

def test_copied_fragment_detects_copy():
    source = "就在昨晚OpenAI悄悄放出了GPT5点5没有任何预告直接上线"
    script = "开头钩子。就在昨晚OpenAI悄悄放出了GPT5点5没有任何预告直接上线。后续分析。"
    frag = copied_fragment(script, source, window=12)
    assert frag is not None


def test_copied_fragment_clean_rewrite():
    source = "就在昨晚OpenAI悄悄放出了GPT5点5没有任何预告直接上线"
    script = "家人们，昨晚 AI 圈又有大动静，OpenAI 的新模型直接上线了，连个发布会都没开"
    assert copied_fragment(script, source, window=12) is None


def test_copied_fragment_short_source():
    assert copied_fragment("任意稿子", "太短", window=12) is None


# ---------- 服务与接口 ----------

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_HOME", str(tmp_path / "home"))
    from app import config as app_config
    from app import db as app_db

    app_config.ensure_dirs()
    app_db.init_db()
    yield tmp_path


@pytest.fixture
def client(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


class FakeLLM:
    def __init__(self, payload=None, err=None):
        self.payload = payload
        self.err = err

    def chat_json(self, system, user, retries=2):
        if self.err:
            raise self.err
        return self.payload


def test_teardown_too_short():
    from app.services import styles

    with pytest.raises(ValueError, match="太短"):
        styles.teardown_script("太短了", FakeLLM())


def test_teardown_service():
    from app.services import styles

    llm = FakeLLM({
        "hook_type": "悬念反问",
        "structure": ["第1段抛问题", "第2段给答案"],
        "rhythm": "短句快节奏",
        "golden_pattern": "数字对比",
        "cta_type": "评论互动",
    })
    t = styles.teardown_script("这是一段足够长的口播文案，用来通过长度校验。" * 3, llm)
    assert t["hook_type"] == "悬念反问"
    assert len(t["structure"]) == 2


def test_style_crud_api(client):
    teardown = {"hook_type": "数字冲击", "structure": ["钩子"], "rhythm": "快",
                "golden_pattern": "对比", "cta_type": "关注"}
    r = client.post("/api/styles", json={"name": "数据轰炸型", "source_text": "原文" * 50, "teardown": teardown})
    sid = r.json()["id"]
    assert sid > 0

    body = client.get("/api/styles").json()
    assert len(body["styles"]) == 1
    assert body["styles"][0]["name"] == "数据轰炸型"
    assert body["styles"][0]["teardown"]["hook_type"] == "数字冲击"

    r = client.put(f"/api/styles/{sid}", json={"name": "改名模板"})
    assert r.json()["name"] == "改名模板"

    assert client.delete(f"/api/styles/{sid}").json()["ok"] is True
    assert client.get("/api/styles").json()["styles"] == []
    assert client.delete(f"/api/styles/{sid}").status_code == 404


def test_teardown_api_job(client, monkeypatch):
    import app.llm as llm_mod

    payload = {"hook_type": "反问", "structure": ["A"], "rhythm": "r",
               "golden_pattern": "g", "cta_type": "c"}
    monkeypatch.setattr(llm_mod, "get_llm", lambda: FakeLLM(payload))

    r = client.post("/api/styles/teardown", json={"text": "足够长的文案。" * 20}).json()
    assert "job_id" in r
    for _ in range(100):
        job = client.get(f"/api/jobs/{r['job_id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert job["result"]["hook_type"] == "反问"


def test_teardown_api_too_short(client):
    assert client.post("/api/styles/teardown", json={"text": "太短"}).status_code == 400


def test_generate_with_style_and_plagiarism_warning(fresh_db):
    from app.db import execute, query_one
    from app.services import packs

    hid = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, sources_count, day, created_at, updated_at) "
        "VALUES ('事件X', 'w', '[]', '30s', 88, 1, '2026-10-03', 'x', 'x')"
    )
    source_text = "就在昨晚OpenAI悄悄放出了GPT5点5没有任何预告直接上线" * 3
    sid = execute(
        "INSERT INTO styles(name, source_text, teardown, created_at) VALUES (?,?,?,?)",
        ("模板A", source_text, json.dumps({"hook_type": "s", "structure": [], "rhythm": "r", "golden_pattern": "g", "cta_type": "c"}, ensure_ascii=False), "x"),
    )

    script = "好家伙。" + "就在昨晚OpenAI悄悄放出了GPT5点5没有任何预告直接上线" + "。后续分析" + "字节" * 55
    llm = FakeLLM({
        "scripts": {"15s": script[:80], "30s": script, "60s": script + "字节" * 130},
        "captions": {"15s": [], "30s": [], "60s": []},
        "publish": {"titles": ["t1", "t2", "t3"], "tags": ["#AI"], "cover_text": "X"},
        "cards": [{"title": "卡片", "point": "副题"}],
        "checklist": [],
    })
    result = packs.generate_pack(hid, sid, llm)
    assert any("照搬" in w for w in result["warnings"])

    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    assert pack["style_id"] == sid
    assert "模板A" in pack["style_note"]

    style_row = query_one("SELECT used_count FROM styles WHERE id=?", (sid,))
    assert style_row["used_count"] == 1


def test_generate_with_style_clean(fresh_db):
    from app.db import execute
    from app.services import packs

    hid = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, sources_count, day, created_at, updated_at) "
        "VALUES ('事件Y', 'w', '[]', '30s', 88, 1, '2026-10-03', 'x', 'x')"
    )
    sid = execute(
        "INSERT INTO styles(name, source_text, teardown, created_at) VALUES (?,?,?,?)",
        ("模板B", "完全不同的一段原文内容" * 10, json.dumps({"hook_type": "s", "structure": [], "rhythm": "r", "golden_pattern": "g", "cta_type": "c"}), "x"),
    )
    good = "钩子。" + "内容" * 70
    llm = FakeLLM({
        "scripts": {"15s": "钩子。" + "内容" * 30, "30s": good, "60s": "钩子。" + "内容" * 145},
        "captions": {"15s": [], "30s": [], "60s": []},
        "publish": {"titles": ["a", "b", "c"], "tags": [], "cover_text": "Y"},
        "cards": [{"title": "卡", "point": "点"}],
        "checklist": [],
    })
    result = packs.generate_pack(hid, sid, llm)
    assert not any("照搬" in w for w in result["warnings"])


# ---------- 视频链接提取文案 ----------

def test_extract_url_from_share_text():
    from app.services.transcribe import extract_url

    assert extract_url("看看这个 https://v.douyin.com/abcDEF/ 复制打开抖音") == "https://v.douyin.com/abcDEF/"
    assert extract_url("https://www.bilibili.com/video/BV1xx。还有别的") == "https://www.bilibili.com/video/BV1xx"
    assert extract_url("没有链接的纯文案") == ""


def test_extract_text_from_link_flow(fresh_db, monkeypatch):
    """下载+转写流程：函数边界打桩，验证串联与清理。"""
    from pathlib import Path

    from app.services import transcribe

    calls = []

    def fake_download(url, out_dir):
        calls.append(("download", url))
        return Path(out_dir) / "video.mp4"

    def fake_transcribe(path):
        calls.append(("transcribe", str(path)))
        return "这是一段从视频里转出来的口播文案，足够长。"

    monkeypatch.setattr(transcribe, "download_video", fake_download)
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)

    text = transcribe.extract_text_from_link("https://v.douyin.com/abc/", progress=lambda m: calls.append(("msg", m)))
    assert text.startswith("这是一段从视频里转出来的口播文案")
    assert ("download", "https://v.douyin.com/abc/") in calls
    assert any(c[0] == "msg" for c in calls)  # 有进度汇报


def test_extract_text_rejects_no_link(fresh_db):
    from app.services import transcribe

    import pytest

    with pytest.raises(transcribe.TranscribeError, match="没有识别到链接"):
        transcribe.extract_text_from_link("纯文字没有链接")


def test_extract_text_api_job(client, monkeypatch):
    import app.services.transcribe as tr_mod

    monkeypatch.setattr(
        tr_mod, "extract_text_from_link",
        lambda url, progress=None: "从链接转出来的文案内容，足够长。",
    )
    r = client.post("/api/styles/extract-text", json={"link": "https://v.douyin.com/xyz/"}).json()
    assert "job_id" in r
    for _ in range(100):
        job = client.get(f"/api/jobs/{r['job_id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert job["result"]["text"].startswith("从链接转出来")


def test_extract_text_api_no_link(client):
    assert client.post("/api/styles/extract-text", json={"link": "没链接"}).status_code == 400
