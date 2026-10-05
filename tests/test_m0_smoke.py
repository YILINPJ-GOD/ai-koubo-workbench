"""M0 冒烟测试：应用可启动、数据库建表、配置默认值、任务系统、备份滚动。"""
import time

from fastapi.testclient import TestClient


def test_health():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["db_ok"] is True
        assert body["key_configured"] is False


def test_db_tables_created():
    from app.db import init_db, query

    init_db()
    tables = {
        r["name"] for r in query("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"items", "hotspots", "packs", "styles", "fetch_runs", "app_state", "sources"} <= tables


def test_sources_seeded():
    from app.db import init_db, query

    init_db()
    keys = {r["key"] for r in query("SELECT key FROM sources")}
    assert {"official", "jiqizhixin", "qbitai", "weibo_hot", "baidu_hot", "hackernews"} <= keys


def test_index_served():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "AI口播工作台" in r.text


def test_config_defaults():
    from app.config import load_config

    cfg = load_config()
    assert cfg["model"] == "glm-4-flash"
    assert cfg["sources"]["official"] is True
    assert cfg["style_pref"]["tone"] == "轻松接地气"


def test_job_lifecycle():
    from app.services import jobs

    def work(job_id):
        jobs.update_job(job_id, done=1, total=2, stage="half", message="进行中")
        return 42

    jid = jobs.start_job(work, total=2)
    job = _wait_done(jid)
    assert job["status"] == "done"
    assert job["result"] == 42
    assert job["done"] == 1 and job["total"] == 2


def test_job_error_captured():
    from app.services import jobs

    def bad(job_id):
        raise ValueError("boom")

    jid = jobs.start_job(bad)
    job = _wait_done(jid)
    assert job["status"] == "error"
    assert "boom" in job["error"]


def _wait_done(job_id, timeout=5.0):
    from app.services import jobs

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.get_job(job_id)
        if job and job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("任务超时未完成")


def test_backup_rotation_keeps_seven(tmp_path, monkeypatch):
    from app.services import backup

    bdir = tmp_path / "backups"
    bdir.mkdir()
    monkeypatch.setattr(backup, "backups_dir", lambda: bdir)
    for i in range(9):
        (bdir / f"workbench-2026010{i}-000000-auto.db").write_text("x", encoding="utf-8")
    backup._rotate()
    files = list(bdir.glob("workbench-*.db"))
    assert len(files) == 7
    # 最旧的两份应被删除
    names = {f.name for f in files}
    assert "workbench-20260100-000000-auto.db" not in names
    assert "workbench-20260101-000000-auto.db" not in names


def test_sources_seeded_with_qwen():
    from app.db import init_db, query

    init_db()
    keys = {r["key"] for r in query("SELECT key FROM sources")}
    assert "qwen" in keys


def test_startup_bat_has_watchdog():
    """启动脚本带看护循环：意外退出自动重启（修：服务静默退出后"打不开"）。"""
    from pathlib import Path

    bat = Path(__file__).resolve().parent.parent / "启动.bat"
    text = bat.read_text(encoding="utf-8")
    assert ":loop" in text and "goto loop" in text  # 看护循环
    assert "uvicorn" in text  # 服务启动命令


def test_host_header_validation():
    """安全回归：恶意 Host 被拒（防 DNS rebinding），本机 Host 放行（审查F2）。"""
    from pathlib import Path

    main_src = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert "TrustedHostMiddleware" in main_src

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        # TestClient 默认 host=testserver（在白名单）
        assert c.get("/api/health").status_code == 200
        # 恶意 host 被拒
        r = c.get("/api/health", headers={"Host": "evil.example"})
        assert r.status_code == 400


def test_startup_bat_no_browser_loop():
    """回归：看护循环重启服务时不得反复弹浏览器（用户报告"一直在重复打开"）。

    浏览器打开必须在 :loop 之前只出现一次，且循环前要有端口占用检测。
    """
    from pathlib import Path

    bat = Path(__file__).resolve().parent.parent / "启动.bat"
    text = bat.read_text(encoding="utf-8")
    # 看护循环体内不得有浏览器打开命令（修：服务重启时反复弹浏览器）
    loop_body = text.split(":loop", 1)[1]
    assert "start \"\"" not in loop_body
    # 端口占用检测存在，且在 :loop 之前（已有实例只开页面不起服务）
    assert "netstat" in text and "findstr" in text
    assert text.index("netstat") < text.index(":loop")
