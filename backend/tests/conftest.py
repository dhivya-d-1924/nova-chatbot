import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from tests.fake_llm import STATE, serve_in_thread  # noqa: E402

PASSWORD = "Correct-Horse-9"


@pytest.fixture(scope="session")
def fake_llm_url():
    url, srv = serve_in_thread()
    yield url
    srv.shutdown()


class Api:
    """Test client wrapper that keeps the CSRF token like the browser app does."""

    def __init__(self, app):
        self.app = app
        self.c = app.test_client()
        self.csrf = None
        self.user = None

    def _h(self, headers=None):
        h = dict(headers or {})
        if self.csrf:
            h.setdefault("X-CSRF-Token", self.csrf)
        return h

    def get(self, path, **kw):
        return self.c.get(path, headers=self._h(kw.pop("headers", None)), **kw)

    def post(self, path, json=None, **kw):
        return self.c.post(path, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, path, json=None, **kw):
        return self.c.put(path, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, path, json=None, **kw):
        return self.c.patch(path, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, path, json=None, **kw):
        return self.c.delete(path, json=json, headers=self._h(kw.pop("headers", None)), **kw)

    def _accept(self, r):
        if r.is_json and r.json and "csrf" in r.json:
            self.csrf = r.json["csrf"]
            self.user = r.json["user"]
        return r

    def register(self, email, name="Test User", password=PASSWORD):
        return self._accept(self.post("/api/auth/register", {"name": name, "email": email, "password": password}))

    def login(self, email, password=PASSWORD):
        return self._accept(self.post("/api/auth/login", {"email": email, "password": password}))

    def accept(self, r):
        return self._accept(r)

    def stream(self, payload, path="/api/chat/stream"):
        r = self.post(path, payload)
        events = []
        if r.mimetype == "text/event-stream":
            for block in r.get_data(as_text=True).split("\n\n"):
                for line in block.split("\n"):
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
        return r, events


@pytest.fixture
def make_app(tmp_path, fake_llm_url):
    from nova import security

    def _make(**overrides):
        db_url = os.environ.get("TEST_DATABASE_URL")
        if db_url:  # e.g. postgresql+psycopg://postgres@/nova_test?host=/run/pg&port=5432 : start each app from an empty schema
            from sqlalchemy import create_engine, text
            eng = create_engine(db_url)
            with eng.begin() as c:
                c.execute(text("DROP SCHEMA public CASCADE"))
                c.execute(text("CREATE SCHEMA public"))
            eng.dispose()
        cfg = {"APP_ENV": "testing", "DATABASE_URL": db_url or f"sqlite:///{tmp_path}/test.db", "_SKIP_ENV_FILE": "1",
               "LLM_API_KEY": "test-key", "LLM_BASE_URL": fake_llm_url, "LLM_MODEL": "fake-model",
               "LLM_MAX_RETRIES": "0", "ALLOWED_ORIGINS": "http://localhost:5173", "APP_URL": "http://localhost:5173",
               "SEARCH_PROVIDER": "none", "REGISTER_RATE_LIMIT_PER_HOUR": "1000"}
        cfg.update({k: str(v) for k, v in overrides.items()})
        from nova import create_app
        app = create_app(cfg)
        security.limiter.reset()
        STATE["calls"] = 0
        return app
    return _make


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def api(app):
    return Api(app)


@pytest.fixture
def new_api(app):
    """Factory for additional independent browser sessions against the same app."""
    return lambda: Api(app)


@pytest.fixture
def admin(new_api):
    a = new_api()
    r = a.register("admin@example.com", "Admin")
    assert r.status_code == 201 and r.json["user"]["role"] == "admin"
    return a


@pytest.fixture
def user(admin, new_api):
    u = new_api()
    r = u.register("user@example.com", "User")
    assert r.status_code == 201 and r.json["user"]["role"] == "user"
    return u


@pytest.fixture
def outbox(monkeypatch):
    """Capture account emails (verification and reset links)."""
    sent = []

    def fake_send(to, subject, body, link):
        sent.append({"to": to, "subject": subject, "link": link})
        return True
    import nova.auth_routes
    monkeypatch.setattr(nova.auth_routes, "send_account_email", fake_send)
    return sent


def token_from(link, param):
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(link).query)[param][0]
