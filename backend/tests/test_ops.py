"""Migrations of an older database, backup/restore, health checks, maintenance, CLI and the shared limiter."""
import os
import shutil
import socket
import sqlite3
import subprocess
import time

import pytest
from sqlalchemy import select

from nova import backup, db, security
from tests.conftest import PASSWORD, Api

# Schema of the original (pre-upgrade) application, as shipped in the uploaded project.
LEGACY_SCHEMA = """
CREATE TABLE conversations (id VARCHAR(36) PRIMARY KEY, title VARCHAR(200) NOT NULL, created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL);
CREATE TABLE messages (id VARCHAR(36) PRIMARY KEY, conversation_id VARCHAR(36) NOT NULL, role VARCHAR(16) NOT NULL,
  content TEXT NOT NULL, created_at BIGINT NOT NULL);
CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255) UNIQUE NOT NULL, name VARCHAR(100) NOT NULL,
  password_hash VARCHAR(255) NOT NULL, role VARCHAR(16) NOT NULL, disabled INTEGER NOT NULL, created_at BIGINT NOT NULL,
  last_login BIGINT);
CREATE TABLE documents (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, filename VARCHAR(255) NOT NULL,
  kind VARCHAR(8) NOT NULL, pages INTEGER NOT NULL, chunk_count INTEGER NOT NULL, size INTEGER NOT NULL, created_at BIGINT NOT NULL);
CREATE TABLE pending_actions (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, conversation_id VARCHAR(36) NOT NULL,
  message_id VARCHAR(36), state TEXT NOT NULL, status VARCHAR(12) NOT NULL, created_at BIGINT NOT NULL);
"""


SQLITE_ONLY = pytest.mark.skipif(bool(os.environ.get("TEST_DATABASE_URL")), reason="SQLite file upgrade/backup (PostgreSQL uses pg_dump)")


@SQLITE_ONLY
def test_upgrade_from_original_schema_preserves_data(tmp_path, make_app):
    from werkzeug.security import generate_password_hash
    path = tmp_path / "test.db"
    con = sqlite3.connect(path)
    con.executescript(LEGACY_SCHEMA)
    con.execute("INSERT INTO users VALUES ('u1','old@example.com','Old',?, 'admin', 0, 1, NULL)",
                (generate_password_hash(PASSWORD),))
    con.execute("INSERT INTO conversations VALUES ('c1','Old chat',1,2)")
    con.execute("INSERT INTO messages VALUES ('m1','c1','user','hello from 2025',1)")
    con.execute("INSERT INTO pending_actions VALUES ('p1','u1','c1',NULL,'{}','open',1)")
    con.commit()
    con.close()
    app = make_app()
    with db.engine.connect() as c:
        u = c.execute(select(db.users).where(db.users.c.id == "u1")).first()
        assert u.email_verified == 1 and u.mfa_enabled == 0 and u.failed_logins == 0
        assert c.execute(select(db.conversations.c.pinned)).scalar() == 0
        assert c.execute(select(db.pending_actions.c.expires_at)).scalar() is not None  # old approvals closed
        assert c.execute(select(db.messages.c.content)).scalar() == "hello from 2025"
    assert db.schema_version() == db.LATEST_VERSION
    a = Api(app)
    assert a.login("old@example.com").status_code == 200
    # running migrations again is a no-op
    out = db.migrate()
    assert out["columns"] == [] and out["applied"] == []


@SQLITE_ONLY
def test_backup_and_restore_round_trip(tmp_path, admin):
    from nova.config import settings
    cid = admin.post("/api/conversations").json["id"]
    dest = backup.backup(settings.DATABASE_URL, str(tmp_path / "bk"))
    assert dest.exists() and backup.verify(dest)
    admin.delete(f"/api/conversations/{cid}")
    db.engine.dispose()
    backup.restore(settings.DATABASE_URL, str(dest))
    db.init_engine(settings.DATABASE_URL)
    with db.engine.connect() as c:
        assert c.execute(select(db.conversations.c.id).where(db.conversations.c.id == cid)).first()


def test_restore_refuses_corrupt_backup(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database")
    with pytest.raises((SystemExit, sqlite3.DatabaseError)):
        backup.restore(f"sqlite:///{tmp_path}/x.db", str(bad))


def test_health_endpoints(api):
    assert api.get("/api/health/live").json == {"status": "ok"}
    r = api.get("/api/health/ready")
    assert r.status_code == 200 and r.json["checks"]["database"] == "ok" and r.json["checks"]["schema"] == "ok"
    assert api.get("/api/health").json["configured"] is True


def test_readiness_fails_when_database_is_down(api, monkeypatch):
    def broken():
        raise RuntimeError("down")
    monkeypatch.setattr(db.engine, "connect", broken)
    r = api.get("/api/health/ready")
    assert r.status_code == 503 and r.json["checks"]["database"] == "unavailable"


def test_maintenance_cleans_expired_data(admin):
    from sqlalchemy import update

    from nova.maintenance import run_maintenance
    with db.engine.begin() as c:
        c.execute(update(db.sessions).values(expires_at=1))
    out = run_maintenance()
    assert out["sessions"] == 1


def test_cli_create_admin_and_check_config(app):
    runner = app.test_cli_runner()
    r = runner.invoke(args=["create-admin", "--email", "ops@example.com", "--password", "Ops-Strong-Pass-1"])
    assert r.exit_code == 0 and "Created administrator" in r.output
    with db.engine.connect() as c:
        u = c.execute(select(db.users).where(db.users.c.email == "ops@example.com")).first()
    assert u.role == "admin" and u.email_verified == 1
    r = runner.invoke(args=["create-admin", "--email", "ops@example.com"])
    assert "Promoted" in r.output
    assert runner.invoke(args=["check-config"]).exit_code == 0
    assert runner.invoke(args=["migrate"]).exit_code == 0


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def redis_url():
    exe = shutil.which("redis-server")
    if not exe:
        pytest.skip("redis-server not installed")
    port = _free_port()
    proc = subprocess.Popen([exe, "--port", str(port), "--save", "", "--appendonly", "no"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"redis://127.0.0.1:{port}/0"
    proc.terminate()
    proc.wait(5)


def test_redis_limiter_is_shared_between_workers(redis_url, make_app):
    make_app(REDIS_URL=redis_url, AUTH_RATE_LIMIT_PER_MINUTE="3")
    assert isinstance(security.limiter, security.RedisLimiter)
    worker_a, worker_b = security.RedisLimiter(redis_url), security.RedisLimiter(redis_url)
    worker_a.reset()
    results = [worker_a.hit("t:1", 3, 60), worker_b.hit("t:1", 3, 60), worker_a.hit("t:1", 3, 60), worker_b.hit("t:1", 3, 60)]
    assert results == [True, True, True, False]
    assert worker_a.acquire("s", 1, 60) and not worker_b.acquire("s", 1, 60)
    worker_b.release("s")
    assert worker_a.acquire("s", 1, 60)


def test_redis_unreachable_in_production_fails_fast(make_app):
    from nova.config import settings
    settings.load({"APP_ENV": "production", "REDIS_URL": "redis://127.0.0.1:1/0", "_SKIP_ENV_FILE": "1",
                   "SECRET_KEY": "x" * 40, "APP_URL": "https://a.example", "ALLOWED_ORIGINS": "https://a.example"})
    with pytest.raises(RuntimeError):
        security.init_limiter()
    settings.load({"APP_ENV": "testing", "_SKIP_ENV_FILE": "1"})
    security.init_limiter()
