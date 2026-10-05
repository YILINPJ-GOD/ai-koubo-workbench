"""M6 测试：备份导出/导入、清空保护、备份列表。"""
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient


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


def _seed_one_item():
    from app.db import execute

    return execute(
        "INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day) "
        "VALUES ('qbitai','media','备份测试条目','http://s/1','摘要','大模型','x','2026-10-03')"
    )


def test_backup_db_creates_file(fresh_db):
    from app.services import backup

    _seed_one_item()
    p = backup.backup_db(tag="test")
    assert p.exists()
    # 备份文件可打开且数据一致
    conn = sqlite3.connect(p)
    n = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    conn.close()
    assert n == 1


def test_export_import_roundtrip(fresh_db, client):
    from app.config import db_path, load_config, save_config
    from app.db import query_one
    from app.services import backup

    _seed_one_item()
    save_config({**load_config(), "api_key": "roundtrip-key"})  # 确保 config.json 存在
    zip_path = fresh_db / "export.zip"
    backup.export_backup(zip_path)

    # zip 内容包含 db 与 config
    with zipfile.ZipFile(zip_path) as zf:
        assert "workbench.db" in zf.namelist()
        assert "config.json" in zf.namelist()

    # 破坏当前数据，再从备份恢复
    from app.db import execute

    execute("DELETE FROM items")
    assert query_one("SELECT COUNT(*) AS n FROM items")["n"] == 0

    result = backup.import_backup(zip_path)
    assert "workbench.db" in result["restored"]
    assert query_one("SELECT COUNT(*) AS n FROM items")["n"] == 1
    # 恢复前自动做了安全备份
    assert result["safety_backup"]


def test_import_rejects_invalid_zip(fresh_db, tmp_path):
    from app.services import backup

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("random.txt", "not a backup")
    with pytest.raises(ValueError, match="workbench"):
        backup.import_backup(bad)


def test_clear_all_data(fresh_db):
    from app.db import query_one
    from app.services import backup

    _seed_one_item()
    from app.config import save_config, load_config

    save_config({**load_config(), "api_key": "keep-me"})
    result = backup.clear_all_data()
    assert result["safety_backup"]
    assert query_one("SELECT COUNT(*) AS n FROM items")["n"] == 0
    # config（含 key）保留
    from app.config import load_config

    assert load_config()["api_key"] == "keep-me"


def test_backup_api_export_import(client, fresh_db):
    _seed_one_item()
    r = client.post("/api/backup/export")
    assert r.status_code == 200
    content = r.content
    assert content[:2] == b"PK"  # zip magic

    # 导入同一份
    import io

    files = {"file": ("backup.zip", io.BytesIO(content), "application/zip")}
    r2 = client.post("/api/backup/import", files=files)
    assert r2.status_code == 200
    assert r2.json()["restart_required"] is True


def test_backup_api_rejects_non_zip(client):
    import io

    files = {"file": ("not.txt", io.BytesIO(b"hello"), "text/plain")}
    assert client.post("/api/backup/import", files=files).status_code == 400


def test_clear_api_requires_confirm(client):
    assert client.post("/api/data/clear", json={}).status_code == 400
    r = client.post("/api/data/clear", json={"confirm": True})
    assert r.status_code == 200


def test_backup_list_api(client):
    from app.services import backup

    _seed_one_item()
    backup.backup_db(tag="x")
    body = client.get("/api/backup/list").json()
    assert len(body["backups"]) >= 1
    assert body["meta"]["items"] == 1


# ---------- 安全加固（独立审查发现：F1 zip slip / F8 句柄 / M4 WAL） ----------

def test_import_rejects_zip_slip(fresh_db):
    """恶意 zip 的 images/../../xxx 条目被拒绝落盘（审查F1 zip slip）。"""
    from app.services import backup

    evil = fresh_db / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("workbench.db", b"fake")
        zf.writestr("images/../../evil.bat", b"malicious")

    backup.import_backup(evil)
    # 恶意文件没有写到 data 目录外
    assert not (fresh_db / "evil.bat").exists()
    assert not (fresh_db.parent / "evil.bat").exists()
    assert not (fresh_db / "home" / "data" / "images" / "evil.bat").exists()


def test_import_cleans_stale_wal(fresh_db):
    """导入后清理旧 -wal/-shm，防旧帧重放（审查M4/F9）。"""
    from app.services import backup

    good = fresh_db / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("workbench.db", b"new-db-content")
    data_dir = fresh_db / "home" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "workbench.db-wal").write_bytes(b"stale-wal")
    (data_dir / "workbench.db-shm").write_bytes(b"stale-shm")

    backup.import_backup(good)
    assert not (data_dir / "workbench.db-wal").exists()
    assert not (data_dir / "workbench.db-shm").exists()


def test_backup_db_consistent_under_wal(fresh_db):
    """运行期备份走 sqlite backup API：WAL 未合并时也能拿到一致快照。"""
    import sqlite3

    from app.db import execute
    from app.services import backup

    execute("INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day) "
            "VALUES ('qbitai','media','wal测试','http://w/1','s','大模型','x','2026-10-04')")
    # 制造未 checkpoint 的 WAL 内容：写入后不关闭连接就备份
    conn = sqlite3.connect(str(fresh_db / "home" / "data" / "workbench.db"))
    conn.execute("INSERT INTO items(source_key, source_type, title, url, summary, category, fetched_at, day) "
                 "VALUES ('qbitai','media','wal内写入','http://w/2','s','大模型','x','2026-10-04')")
    conn.commit()
    p = backup.backup_db(tag="wal-test")
    # 保持 conn 打开（模拟运行期）
    check = sqlite3.connect(str(p))
    n = check.execute("SELECT COUNT(*) FROM items WHERE title='wal内写入'").fetchone()[0]
    check.close()
    conn.close()
    assert n == 1  # backup API 拿到了 WAL 里的数据（裸拷贝可能拿不到）
