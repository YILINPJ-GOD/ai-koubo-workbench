"""M1 测试：JSON 修复解析、LLM 重试与错误转译、设置接口。"""
import pytest
from fastapi.testclient import TestClient

from app.llm import LLMClient, LLMError, extract_json


# ---------- extract_json ----------

def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = '好的，以下是结果：\n```json\n{"a": [1, 2], "b": "x"}\n```\n希望有帮助'
    assert extract_json(text) == {"a": [1, 2], "b": "x"}


def test_extract_json_trailing_comma():
    assert extract_json('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}


def test_extract_json_wrapped_in_prose():
    text = '结果如下 {"title": "GPT-5", "score": 90} 以上'
    assert extract_json(text) == {"title": "GPT-5", "score": 90}


def test_extract_json_array():
    assert extract_json('[{"i": 1}, {"i": 2}]') == [{"i": 1}, {"i": 2}]


def test_extract_json_invalid_raises():
    with pytest.raises(LLMError):
        extract_json("完全不是JSON的回复，抱歉")


# ---------- LLMClient 重试与错误处理 ----------

def test_chat_retries_then_raises(monkeypatch):
    client = LLMClient("fake-key")
    calls = []

    def flaky(system, user):
        calls.append(1)
        raise LLMError("网络抖动")

    monkeypatch.setattr(client, "_raw", flaky)
    with pytest.raises(LLMError):
        client.chat("s", "u", retries=2)
    assert len(calls) == 3  # 首次 + 2 次重试


def test_auth_error_no_retry(monkeypatch):
    client = LLMClient("bad-key")
    calls = []

    def auth_fail(system, user):
        calls.append(1)
        raise LLMError("API key 无效或未授权，请到「设置」页检查智谱 API key")

    monkeypatch.setattr(client, "_raw", auth_fail)
    with pytest.raises(LLMError):
        client.chat("s", "u", retries=2)
    assert len(calls) == 1  # key 错误不重试


def test_chat_empty_response_retries(monkeypatch):
    client = LLMClient("fake-key")
    outs = ["", "  ", "好的回复"]

    def scripted(system, user):
        return outs.pop(0)

    monkeypatch.setattr(client, "_raw", scripted)
    assert client.chat("s", "u", retries=2) == "好的回复"


def test_chat_json_recovers_from_bad_output(monkeypatch):
    client = LLMClient("fake-key")
    responses = ["这不是JSON", '```json\n{"ok": true}\n```']

    def scripted(system, user):
        return responses.pop(0)

    monkeypatch.setattr(client, "_raw", scripted)
    assert client.chat_json("s", "u") == {"ok": True}


def test_get_llm_without_key_raises():
    from app import config as app_config
    from app import llm as llm_mod

    cfg = app_config.load_config()
    cfg["api_key"] = ""
    app_config.save_config(cfg)
    llm_mod._default = None
    with pytest.raises(LLMError, match="设置"):
        llm_mod.get_llm()


# ---------- 设置接口 ----------

@pytest.fixture
def clean_home(monkeypatch, tmp_path):
    """每个测试独立数据目录。"""
    monkeypatch.setenv("WORKBENCH_HOME", str(tmp_path / "home"))
    from app import config as app_config

    app_config.ensure_dirs()
    yield tmp_path


@pytest.fixture
def client(clean_home):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_settings_roundtrip(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["has_api_key"] is False
    assert {s["key"] for s in body["sources"]} == {
        "official", "qwen", "jiqizhixin", "qbitai", "ifanr", "36kr",
        "weibo_hot", "baidu_hot", "hackernews",
    }

    r = client.put("/api/settings", json={"api_key": "test-key-1234567890", "model": "glm-4-air"})
    assert r.json()["ok"] is True

    body = client.get("/api/settings").json()
    assert body["has_api_key"] is True
    assert "****" in body["api_key_masked"]
    assert body["model"] == "glm-4-air"


def test_settings_rejects_bad_model(client):
    client.put("/api/settings", json={"model": "not-a-model"})
    assert client.get("/api/settings").json()["model"] == "glm-4-flash"


def test_settings_accepts_new_glm_models(client):
    """智谱新模型按 glm- 前缀放行，硬编码名单会挡住新模型。"""
    client.put("/api/settings", json={"model": "glm-4.6"})
    assert client.get("/api/settings").json()["model"] == "glm-4.6"
    client.put("/api/settings", json={"model": "glm-4.5-air"})
    assert client.get("/api/settings").json()["model"] == "glm-4.5-air"


def test_settings_rejects_injection_model(client):
    client.put("/api/settings", json={"model": "glm-4; drop table users"})
    assert client.get("/api/settings").json()["model"] == "glm-4-flash"
    client.put("/api/settings", json={"model": "GLM-4-PLUS"})
    assert client.get("/api/settings").json()["model"] == "glm-4-flash"  # 大写不放行，避免大小写绕过


def test_settings_masked_key_not_overwritten(client):
    client.put("/api/settings", json={"api_key": "real-key-abcdef123456"})
    # 前端把掩码串原样传回时，不应覆盖真实 key
    client.put("/api/settings", json={"api_key": "sk-ab****cdef"})
    from app.config import load_config

    assert load_config()["api_key"] == "real-key-abcdef123456"


def test_settings_source_toggle(client):
    client.put("/api/settings", json={"sources": {"weibo_hot": False}})
    from app.config import load_config

    assert load_config()["sources"]["weibo_hot"] is False
    assert load_config()["sources"]["qbitai"] is True


def test_settings_style_pref(client):
    client.put("/api/settings", json={"style_pref": {"tone": "轻松接地气", "extra": "别用家人们开头"}})
    from app.config import load_config

    assert load_config()["style_pref"]["extra"] == "别用家人们开头"


def test_test_connection_without_key(client):
    body = client.post("/api/settings/test").json()
    assert body["ok"] is False
    assert "API key" in body["message"]


def test_test_connection_with_key(client, monkeypatch):
    client.put("/api/settings", json={"api_key": "fake-key"})
    import app.llm as llm_mod

    monkeypatch.setattr(llm_mod.LLMClient, "_raw", lambda self, s, u: "pong")
    body = client.post("/api/settings/test").json()
    assert body["ok"] is True
    assert "pong" in body["message"]


def test_test_connection_auth_failure_message(client, monkeypatch):
    client.put("/api/settings", json={"api_key": "bad-key"})

    import app.llm as llm_mod

    def auth_fail(self, s, u):
        raise LLMError("API key 无效或未授权，请到「设置」页检查智谱 API key")

    monkeypatch.setattr(llm_mod.LLMClient, "_raw", auth_fail)
    body = client.post("/api/settings/test").json()
    assert body["ok"] is False
    assert "设置" in body["message"]


def test_test_connection_with_unsaved_key(client, monkeypatch):
    """未保存的 key 直接验证：不落盘，也能测出真实结果。"""
    import app.llm as llm_mod

    monkeypatch.setattr(llm_mod.LLMClient, "_raw", lambda self, s, u: "pong")
    body = client.post("/api/settings/test", json={"api_key": "brand-new-key-123456"}).json()
    assert body["ok"] is True

    # 该 key 不应被写入配置
    from app.config import load_config

    assert load_config()["api_key"] == ""


def test_test_connection_unsaved_bad_key(client, monkeypatch):
    import app.llm as llm_mod

    def auth_fail(self, s, u):
        raise LLMError("API key 无效或未授权，请到「设置」页检查智谱 API key")

    monkeypatch.setattr(llm_mod.LLMClient, "_raw", auth_fail)
    body = client.post("/api/settings/test", json={"api_key": "wrong-key"}).json()
    assert body["ok"] is False
    assert "无效" in body["message"]


def test_test_connection_prefers_saved_when_no_body(client, monkeypatch):
    client.put("/api/settings", json={"api_key": "saved-key-abcdef"})
    import app.llm as llm_mod

    seen = {}

    def capture(self, s, u):
        seen["key"] = self.api_key
        return "pong"

    monkeypatch.setattr(llm_mod.LLMClient, "_raw", capture)
    client.post("/api/settings/test", json={}).json()
    assert seen["key"] == "saved-key-abcdef"
