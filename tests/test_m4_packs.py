"""M4 测试：字数校验、风险词扫描、卡片生成、素材包生成、接口。"""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.services.script_checks import (
    check_lengths,
    count_script_chars,
    lengths_feedback,
    scan_risks,
)

TODAY = time.strftime('%Y-%m-%d')


# ---------- 字数 ----------

def test_count_script_chars_excludes_punct():
    assert count_script_chars("你好，世界！") == 4
    assert count_script_chars("GPT-5发布") == 6  # 连字符不计数：GPT5+发布
    assert count_script_chars("GPT-5 发布了") == 7  # GPT5+发布了，连字符不计
    assert count_script_chars("  \n ") == 0


def test_check_lengths_ranges():
    good_15 = "字" * 95
    bad_15 = "字" * 30
    checks = check_lengths({"15s": good_15, "30s": bad_15, "60s": ""})
    assert checks["15s"]["ok"] is True
    assert checks["30s"]["ok"] is False
    assert checks["60s"]["ok"] is False
    assert checks["60s"]["range"] == (270, 360)


def test_lengths_feedback():
    checks = check_lengths({"15s": "字" * 30, "30s": "字" * 150, "60s": "字" * 300})
    fb = lengths_feedback(checks)
    assert "15s: 不合格" in fb
    assert "30s: OK" in fb


# ---------- 风险词 ----------

def test_scan_risks_finds_words():
    text = "这是最好的模型，全网第一，秒杀同行"
    risks = scan_risks(text)
    words = {r["word"] for r in risks}
    assert "最好" in words
    assert "全网第一" in words
    assert "秒杀" in words
    assert all("suggestion" in r for r in risks)


def test_scan_risks_clean_text():
    assert scan_risks("这款模型跑分提升明显，官方给出了对比数据") == []


def test_scan_risks_no_false_positive_on_recent():
    # 「最近」不应命中「最X」绝对化词
    risks = scan_risks("最近发布的新模型")
    assert all(r["word"] != "最近" for r in risks)


# ---------- 卡片生成 ----------

def test_make_card(tmp_path):
    from PIL import Image

    from app.services.cardgen import make_card

    out = tmp_path / "card.png"
    make_card("AI大地震", "某模型跑分暴涨三倍", "AI口播工作台", out)
    assert out.exists()
    img = Image.open(out)
    assert img.size == (1080, 1440)


# ---------- 素材包生成 ----------

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("app.services.packs.card_dir", lambda: tmp_path / "cards")
    from app import config as app_config
    from app import db as app_db

    app_config.ensure_dirs()
    app_db.init_db()
    yield tmp_path


def _seed_hotspot_with_sources():
    from app.db import execute

    hid = execute(
        f"INSERT INTO hotspots(title, why, angles, suggested_length, score, sources_count, day, created_at, updated_at) "
        f"VALUES ('GPT-5.5 发布', '大版本更新', '[\"价格角度\"]', '30s', 90, 2, {TODAY}, 'x', 'x')"
    )
    execute(
        "INSERT INTO items(source_key, source_type, title, url, summary, category, image_url, raw_text, fetched_at, day, event_id) "
        f"VALUES ('official','official','官方公告','http://o/1','发布了','大模型','https://img.example/1.jpg','官方公告全文内容','x',{TODAY},?)",
        (hid,),
    )
    execute(
        "INSERT INTO items(source_key, source_type, title, url, summary, category, image_url, raw_text, fetched_at, day, event_id) "
        f"VALUES ('qbitai','media','媒体评测','http://m/1','评测出炉','大模型','https://img.example/2.jpg','媒体评测全文','x',{TODAY},?)",
        (hid,),
    )
    return hid


GOOD_SCRIPT_15 = "好家伙，" + "字节" * 40 + "。"  # 83字（15s档80-110）
GOOD_SCRIPT_30 = "开头钩子。" + "内容" * 65 + "。"  # 134字
GOOD_SCRIPT_60 = "六十秒版本开头。" + "细节" * 140 + "。"  # 288字


def _fake_scripts_response():
    """第一阶段响应：只有三档口播稿。"""
    return {"scripts": {"15s": GOOD_SCRIPT_15, "30s": GOOD_SCRIPT_30, "60s": GOOD_SCRIPT_60}}


def _fake_assets_response():
    """第二阶段响应：字幕/发布文案/卡片。"""
    return {
        "captions": {
            "15s": [{"t": "0-3s", "text": "GPT-5.5 来了", "gold": True},
                    {"t": "3-15s", "text": "API 价格砍半", "gold": False}],
            "30s": [{"t": "0-3s", "text": "钩子", "gold": False}],
            "60s": [{"t": "0-5s", "text": "钩子", "gold": False}],
        },
        "publish": {"titles": ["GPT-5.5 炸场", "价格腰斩", "这次真变了"],
                    "tags": ["#AI", "#大模型"], "cover_text": "GPT-5.5"},
        "cards": [{"title": "GPT-5.5", "point": "API价格砍半"}, {"title": "跑分", "point": "全面超越上一代"}],
        "checklist": ["字数达标", "有钩子"],
    }


def _fake_llm_response():
    """兼容旧用例名：返回素材响应（字幕等）。"""
    return _fake_assets_response()


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, system, user, retries=2):
        self.calls.append(user)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_generate_pack_success(fresh_db):
    hid = _seed_hotspot_with_sources()
    from app.services import packs

    llm = ScriptedLLM([_fake_scripts_response(), _fake_assets_response()])
    result = packs.generate_pack(hid, None, llm)
    assert result["warnings"] == []

    from app.db import query_one
    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    assert pack is not None
    scripts = json.loads(pack["scripts"])
    assert len(scripts["15s"]) > 50
    captions = json.loads(pack["captions"])
    assert captions["15s"][0]["gold"] is True
    publish = json.loads(pack["publish"])
    assert len(publish["titles"]) == 3
    candidates = json.loads(pack["image_candidates"])
    remotes = [c for c in candidates if c["type"] == "remote"]
    # 来源有 2 张原文图 → 不再生成文字卡片（按需补位策略）
    assert len(remotes) >= 2
    assert all(c["type"] == "remote" for c in candidates)
    assert pack["wordcount_ok"] == 1

    # 热点状态流转 pending → packed
    h = query_one("SELECT status FROM hotspots WHERE id=?", (hid,))
    assert h["status"] == "packed"


def test_generate_pack_wordcount_retry(fresh_db):
    hid = _seed_hotspot_with_sources()
    from app.services import packs

    bad = _fake_scripts_response()
    bad["scripts"] = {"15s": "太短", "30s": "还是太短", "60s": "六十秒也不够"}
    llm = ScriptedLLM([bad, _fake_scripts_response(), _fake_assets_response()])  # 不合格 → 重写成功 → 素材
    result = packs.generate_pack(hid, None, llm)
    assert len(llm.calls) == 3
    assert "字数" in llm.calls[1]
    assert result["warnings"] == []

    from app.db import query_one
    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    assert pack["wordcount_ok"] == 1


def test_generate_pack_risks_marked(fresh_db):
    hid = _seed_hotspot_with_sources()
    from app.services import packs

    risky = _fake_scripts_response()
    risky["scripts"]["15s"] = "好家伙，这是最好的模型，" + "字节" * 40
    llm = ScriptedLLM([risky, risky])  # 风险词自动修复一轮失败 → 保留标注
    result = packs.generate_pack(hid, None, llm)
    assert any("风险词" in w for w in result["warnings"])

    from app.db import query_one
    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    risks = json.loads(pack["risks"])
    assert any(r["word"] == "最好" for r in risks)


def test_generate_pack_regenerate_overwrites(fresh_db):
    hid = _seed_hotspot_with_sources()
    from app.services import packs

    packs.generate_pack(hid, None, ScriptedLLM([_fake_scripts_response(), _fake_assets_response()]))
    packs.generate_pack(hid, None, ScriptedLLM([_fake_scripts_response(), _fake_assets_response()]))
    from app.db import query
    rows = query("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    assert len(rows) == 1  # v1 覆盖不新增


# ---------- 接口 ----------

@pytest.fixture
def client(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_generate_via_api_with_fake_llm(client, monkeypatch):
    hid = _seed_hotspot_with_sources()
    import app.llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_llm", lambda: ScriptedLLM([_fake_scripts_response(), _fake_assets_response()]))
    r = client.post(f"/api/hotspots/{hid}/pack", json={}).json()
    assert "job_id" in r

    import time
    job = None
    for _ in range(100):
        job = client.get(f"/api/jobs/{r['job_id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done", job.get("error")
    assert job["result"]["pack_id"] > 0

    # detail 接口带出 pack
    detail = client.get(f"/api/hotspots/{hid}").json()
    assert detail["pack"]["scripts"]["15s"]
    assert detail["hotspot"]["status"] == "packed"


def test_generate_without_key_fails_gracefully(client, monkeypatch):
    hid = _seed_hotspot_with_sources()
    import app.llm as llm_mod

    def no_key():
        raise llm_mod.LLMError("尚未配置智谱 API key，请先到「设置」页完成配置")

    monkeypatch.setattr(llm_mod, "get_llm", no_key)
    r = client.post(f"/api/hotspots/{hid}/pack", json={}).json()
    import time
    for _ in range(100):
        job = client.get(f"/api/jobs/{r['job_id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "error"
    assert "设置" in job["error"]


def test_select_images_api(client):
    hid = _seed_hotspot_with_sources()
    from app.services import packs

    pack_id = packs.generate_pack(hid, None, ScriptedLLM([_fake_scripts_response(), _fake_assets_response()]))["pack_id"]
    r = client.put(f"/api/packs/{pack_id}/images", json={"selected": [1, 0]})
    assert r.json()["selected"] == [1, 0]
    # 越界下标被过滤
    r = client.put(f"/api/packs/{pack_id}/images", json={"selected": [0, 99]})
    assert r.json()["selected"] == [0]


def test_card_image_file(client):
    """无原文图来源 → 补文字卡片，卡片接口可出 PNG。"""
    from app.db import execute
    from app.services import packs

    hid = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, sources_count, day, created_at, updated_at) "
        f"VALUES ('无图事件', 'w', '[]', '30s', 90, 1, {TODAY}, 'x', 'x')"
    )
    execute(
        "INSERT INTO items(source_key, source_type, title, url, summary, category, raw_text, fetched_at, day, event_id) "
        f"VALUES ('36kr','media','纯文字稿','http://m/8','摘要','AI产品','正文内容','x',{TODAY},?)",
        (hid,),
    )
    pack_id = packs.generate_pack(hid, None, ScriptedLLM([_fake_scripts_response(), _fake_assets_response()]))["pack_id"]

    import json as _json

    from app.db import query_one

    candidates = _json.loads(query_one("SELECT image_candidates FROM packs WHERE hotspot_id=?", (hid,))["image_candidates"])
    assert candidates and candidates[0]["type"] == "card"  # 补位卡片生成

    r = client.get(f"/api/packs/{pack_id}/images/0/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_remote_dedupe_across_sources():
    """跨来源同图（不同尺寸参数）只留一张。"""
    from app.services.packs import _remote_asset_key

    assert _remote_asset_key("https://c.example/a/IMG.png?w=640") == _remote_asset_key("https://c.example/a/IMG.png?w=3840")
    assert _remote_asset_key("https://s.example/p/1.png!720") == _remote_asset_key("https://s.example/p/1.png")


# ---------- 原文充实与档位级重试（2026-10-03 用户反馈） ----------

def test_migration_adds_images_column(tmp_path, monkeypatch):
    """老库（无 images 列）启动时自动补列。"""
    db_file = tmp_path / "home" / "data" / "workbench.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, source_key TEXT, source_type TEXT, "
        "title TEXT, url TEXT, summary TEXT, summary_zh TEXT, category TEXT, image_url TEXT, "
        "raw_text TEXT, published_at TEXT, fetched_at TEXT, day TEXT, is_read INTEGER, is_starred INTEGER, event_id INTEGER)"
    )
    conn.execute(
        "INSERT INTO items(title, url, day) VALUES ('老数据', 'http://old/1', '2026-10-02')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("WORKBENCH_HOME", str(tmp_path / "home"))
    from app.db import init_db, query_one

    init_db()
    row = query_one("SELECT * FROM items WHERE title='老数据'")
    assert "images" in row  # 列补上了，老数据还在


def test_pack_prompt_contains_fulltext(fresh_db):
    """写稿提示词必须包含来源文章原文，AI 才有料可写。"""
    hid = _seed_hotspot_with_sources()
    from app.services import packs
    from app.services.hotspots import get_hotspot, hotspot_sources

    prompt = packs.build_pack_prompt(get_hotspot(hid), hotspot_sources(hid), None, "", "")
    assert "官方公告全文内容" in prompt  # raw_text 进了 payload
    assert "fulltext" in prompt
    assert "从这里面挖" in prompt  # 写作指引


def test_pack_candidates_use_image_gallery(fresh_db):
    """配图候选应使用文章图集（多张原文图），不止第一张。"""
    import json as _json

    from app.db import execute
    from app.services import packs

    hid = execute(
        "INSERT INTO hotspots(title, why, angles, suggested_length, score, sources_count, day, created_at, updated_at) "
        f"VALUES ('图集事件', 'w', '[]', '30s', 90, 1, {TODAY}, 'x', 'x')"
    )
    gallery = ["https://img.example/1.jpg", "https://img.example/2.jpg", "https://img.example/3.jpg"]
    execute(
        "INSERT INTO items(source_key, source_type, title, url, summary, category, image_url, images, raw_text, fetched_at, day, event_id) "
        f"VALUES ('ifanr','media','图文评测','http://m/9','摘要','AI产品',?,?,'长正文','x',{TODAY},?)",
        (gallery[0], _json.dumps(gallery), hid),
    )
    llm = ScriptedLLM([_fake_scripts_response(), _fake_assets_response()])
    packs.generate_pack(hid, None, llm)

    from app.db import query_one

    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    candidates = _json.loads(pack["image_candidates"])
    remote = [c for c in candidates if c["type"] == "remote"]
    assert len(remote) == 3  # 图集里三张都成了候选


def test_wordcount_retry_only_failing_slots(fresh_db):
    """15s 不合格时只重写 15s，合格档位保留第一次的稿子。"""
    hid = _seed_hotspot_with_sources()
    from app.services import packs

    first = _fake_scripts_response()
    first["scripts"]["15s"] = "太短"  # 15s 不合格，30s/60s 合格
    second = {"scripts": {"15s": GOOD_SCRIPT_15}}  # 重试只交 15s

    llm = ScriptedLLM([first, second, _fake_assets_response()])
    result = packs.generate_pack(hid, None, llm)
    assert len(llm.calls) == 3
    assert "15s" in llm.calls[1] and "60s" not in llm.calls[1].split("严格输出")[-1][:40]
    assert "fulltext" in llm.calls[1]  # 重试时同样带原文

    import json as _json

    from app.db import query_one

    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    scripts = _json.loads(pack["scripts"])
    assert scripts["15s"] == GOOD_SCRIPT_15
    assert scripts["30s"] == GOOD_SCRIPT_30  # 保留第一次的合格稿
    assert pack["wordcount_ok"] == 1
    assert result["warnings"] == []


def test_scripts_hashtags_stripped(fresh_db):
    """话题标签混进口播稿会被剥掉。"""
    hid = _seed_hotspot_with_sources()
    from app.services import packs

    scripts = _fake_scripts_response()
    scripts["scripts"]["60s"] = GOOD_SCRIPT_60 + " #AI前沿模型 #Gemini4"
    llm = ScriptedLLM([scripts, _fake_assets_response()])
    packs.generate_pack(hid, None, llm)

    import json as _json

    from app.db import query_one

    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    s60 = _json.loads(pack["scripts"])["60s"]
    assert "#" not in s60
    assert "GOOD" not in s60  # sanity


def test_risk_words_auto_fixed(fresh_db):
    """风险词自动修复一轮：模型交回合规版本 → 无风险词警告。"""
    hid = _seed_hotspot_with_sources()
    from app.services import packs

    risky = _fake_scripts_response()
    risky["scripts"]["15s"] = "好家伙，这是最好的模型，" + "字节" * 40
    fixed = _fake_scripts_response()
    fixed["scripts"]["15s"] = "好家伙，这个模型很能打，" + "字节" * 40
    llm = ScriptedLLM([risky, fixed, _fake_assets_response()])
    result = packs.generate_pack(hid, None, llm)
    assert not any("风险词" in w for w in result["warnings"])

    import json as _json

    from app.db import query_one

    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    assert "最好" not in _json.loads(pack["scripts"])["15s"]


def test_model_escalation_on_short_scripts(fresh_db, monkeypatch):
    """flash 两轮重试仍不达标 → 失败档位自动换 air 重写。"""
    from app.services import packs

    hid = _seed_hotspot_with_sources()

    # 可升级的假 LLM：带 api_key，配置模型是 flash
    class EscalatableLLM(ScriptedLLM):
        api_key = "fake-key"

    import app.config as app_config
    app_config.update_config({"model": "glm-4-flash"})

    import app.llm as llm_mod

    made = []

    class FakeStrongClient:
        def __init__(self, api_key, model):
            made.append(model)
            self.chat_json = lambda system, user, retries=2: {
                "scripts": {"15s": GOOD_SCRIPT_15, "30s": GOOD_SCRIPT_30, "60s": GOOD_SCRIPT_60}
            }

    monkeypatch.setattr(llm_mod, "LLMClient", FakeStrongClient)

    bad = _fake_scripts_response()
    bad["scripts"] = {"15s": "太短", "30s": "还是短", "60s": "也不够"}
    # 序列：flash 初稿(差) → flash 重试×2(仍差) → 升级 air 修好 → 素材
    llm = EscalatableLLM([bad, bad, bad, _fake_assets_response()])
    result = packs.generate_pack(hid, None, llm)
    assert made == ["glm-4-air"]  # 升级模型第一轮就修好了字数，循环提前结束
    assert result["warnings"] == []

    import json as _json

    from app.db import query_one

    pack = query_one("SELECT * FROM packs WHERE hotspot_id=?", (hid,))
    assert pack["wordcount_ok"] == 1


def test_no_escalation_without_api_key(fresh_db):
    """测试桩（无 api_key 属性）不触发升级，直接带警告收尾。"""
    from app.services import packs

    hid = _seed_hotspot_with_sources()
    bad = _fake_scripts_response()
    bad["scripts"] = {"15s": "太短", "30s": "还是短", "60s": "也不够"}
    llm = ScriptedLLM([bad, bad, bad, _fake_assets_response()])
    result = packs.generate_pack(hid, None, llm)
    assert any("字数未完全达标" in w for w in result["warnings"])
