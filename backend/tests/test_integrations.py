"""Internal app integrations (any REST API) and per-user Google Workspace."""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select

from tests.conftest import Api

SECRET = "hr-api-key-123"


class HR(BaseHTTPRequestHandler):
    """A tiny internal HR app."""
    seen = []

    def _send(self, code, body, headers=None):
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _record(self, body=None):
        HR.seen.append({"method": self.command, "path": self.path, "key": self.headers.get("X-API-Key"),
                        "user": self.headers.get("X-Nova-User-Email"), "body": body})

    def do_GET(self):
        self._record()
        if self.headers.get("X-API-Key") != SECRET:
            return self._send(401, {"error": "bad key"})
        if self.path.startswith("/api/employees/"):
            emp = urllib.parse.unquote(self.path.split("/api/employees/")[1].split("?")[0])
            return self._send(200, {"id": emp, "name": "Asha Rao", "leave_balance": 12})
        if self.path.startswith("/api/redirect"):
            return self._send(302, {}, {"Location": "http://example.com/"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self._record(body)
        return self._send(201, {"ticket": "T-1001", "title": body.get("title")})

    def log_message(self, *a):
        pass


@pytest.fixture
def hr():
    HR.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), HR)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/api"
    srv.shutdown()


def add_hr(admin, base, **extra):
    r = admin.post("/api/admin/integrations", {"name": "HR system", "slug": "hr", "description": "Employees and leave",
                                               "base_url": base, "auth_type": "header", "auth_header": "X-API-Key",
                                               "secret": SECRET, **extra})
    assert r.status_code == 201, r.json
    iid = r.json["id"]
    r = admin.post(f"/api/admin/integrations/{iid}/actions", {
        "name": "get_employee", "label": "Look up an employee", "description": "Get an employee's details and leave balance by id",
        "method": "GET", "path": "/employees/{employee_id}",
        "params": [{"name": "employee_id", "in": "path", "description": "Employee id like E7"}]})
    assert r.status_code == 201, r.json
    r = admin.post(f"/api/admin/integrations/{iid}/actions", {
        "name": "create_ticket", "label": "Raise an HR ticket", "description": "Create an HR helpdesk ticket", "method": "POST",
        "path": "/tickets", "approval": False,
        "params": [{"name": "title", "required": True}, {"name": "priority", "type": "integer"}]})
    assert r.status_code == 201, r.json
    return iid, r.json


def tool_events(api, cid, text):
    r, ev = api.stream({"conversation_id": cid, "message": text, "tools": True})
    return ev


def new_conv(api):
    return api.post("/api/conversations").json["id"]


# ------------------------------------------------------------------ admin setup
def test_admin_registers_app_and_secret_is_never_returned(admin, hr):
    iid, data = add_hr(admin, hr)
    assert data["has_secret"] and "secret" not in data and SECRET not in json.dumps(data)
    acts = {a["name"]: a for a in data["actions"]}
    assert acts["create_ticket"]["approval"] is True  # writes always need approval, even if unticked
    assert acts["get_employee"]["approval"] is False and acts["get_employee"]["tool"] == "hr__get_employee"
    listed = admin.get("/api/admin/integrations").json
    assert SECRET not in json.dumps(listed)
    from nova import db
    with db.engine.connect() as c:
        stored = c.execute(select(db.integrations.c.secret)).scalar()
    assert stored and SECRET not in stored  # encrypted at rest


def test_admin_validation(admin, hr, new_api):
    base = {"name": "X", "slug": "x1", "base_url": hr}
    assert admin.post("/api/admin/integrations", {**base, "slug": "Bad Slug"}).status_code == 400
    assert admin.post("/api/admin/integrations", {**base, "base_url": "ftp://files"}).status_code == 400
    assert admin.post("/api/admin/integrations", {**base, "auth_type": "header"}).status_code == 400
    iid = admin.post("/api/admin/integrations", base).json["id"]
    assert admin.post("/api/admin/integrations", base).status_code == 409
    bad = [
        {"path": "/a/{id}", "params": []},  # placeholder without a parameter
        {"path": "/a", "params": [{"name": "id", "in": "path"}]},  # path parameter not in the path
        {"path": "/a", "params": [{"name": "q", "in": "body"}]},  # GET with a body
        {"path": "/a", "params": [{"name": "q", "type": "date"}]},
    ]
    for b in bad:
        r = admin.post(f"/api/admin/integrations/{iid}/actions", {"name": "act", "label": "A", "description": "d",
                                                                  "method": "GET", **b})
        assert r.status_code == 400, b
    user = new_api()
    user.register("staff@example.com")
    assert user.get("/api/admin/integrations").status_code == 403
    assert user.post("/api/admin/integrations", base).status_code == 403


def test_admin_test_button_calls_the_app(admin, hr):
    iid, data = add_hr(admin, hr)
    aid = next(a["id"] for a in data["actions"] if a["name"] == "get_employee")
    r = admin.post(f"/api/admin/integrations/{iid}/actions/{aid}/test", {"args": {"employee_id": "E7"}})
    assert r.json["ok"] and "Asha Rao" in r.json["result"] and r.json["summary"] == "HTTP 200"
    assert HR.seen[-1]["key"] == SECRET and HR.seen[-1]["user"] == "admin@example.com"
    # values are URL-encoded: they can't walk out of the path
    admin.post(f"/api/admin/integrations/{iid}/actions/{aid}/test", {"args": {"employee_id": "../../admin"}})
    assert HR.seen[-1]["path"] == "/api/employees/..%2F..%2Fadmin"
    missing = admin.post(f"/api/admin/integrations/{iid}/actions/{aid}/test", {"args": {}})
    assert not missing.json["ok"] and "employee_id" in missing.json["error"]


def test_blocked_addresses_and_redirects(admin, hr):
    r = admin.post("/api/admin/integrations", {"name": "Meta", "slug": "meta", "base_url": "http://169.254.169.254/latest"})
    iid = r.json["id"]
    r = admin.post(f"/api/admin/integrations/{iid}/actions", {"name": "creds", "label": "c", "description": "d", "method": "GET",
                                                              "path": "/meta-data"})
    aid = r.json["actions"][0]["id"]
    res = admin.post(f"/api/admin/integrations/{iid}/actions/{aid}/test", {"args": {}}).json
    assert not res["ok"] and "not allowed" in res["error"]
    iid2 = admin.post("/api/admin/integrations", {"name": "R", "slug": "redir", "base_url": hr, "auth_type": "header",
                                                     "auth_header": "X-API-Key", "secret": SECRET}).json["id"]
    aid2 = admin.post(f"/api/admin/integrations/{iid2}/actions", {"name": "go", "label": "g", "description": "d",
                                                                  "method": "GET", "path": "/redirect"}).json["actions"][0]["id"]
    res = admin.post(f"/api/admin/integrations/{iid2}/actions/{aid2}/test", {"args": {}}).json
    assert not res["ok"] and "redirect" in res["error"]


def test_allowed_hosts_setting(make_app, hr):
    app = make_app(INTEGRATION_ALLOWED_HOSTS="hr.corp.example")
    admin = Api(app)
    admin.register("admin@example.com", "Admin")
    r = admin.post("/api/admin/integrations", {"name": "HR", "slug": "hr", "base_url": hr})
    assert r.status_code == 400 and r.json["code"] == "host_not_allowed"


def test_type_checks_on_arguments():
    from types import SimpleNamespace

    from nova.integrations import rest
    from nova.tools import ToolError
    integ = SimpleNamespace(base_url="http://hr.local/api", auth_type="none", auth_header=None, secret=None, send_user=0)
    act = SimpleNamespace(method="POST", path="/tickets", params=json.dumps([{"name": "priority", "type": "integer", "in": "body"},
                                                                            {"name": "urgent", "type": "boolean", "in": "body"}]))
    method, url, headers, body = rest.build_request(integ, act, {"priority": "3", "urgent": "yes"})
    assert (method, url, body) == ("POST", "http://hr.local/api/tickets", {"priority": 3, "urgent": True})
    with pytest.raises(ToolError):
        rest.build_request(integ, act, {"priority": "high"})


# ------------------------------------------------------------------ the assistant using the app
def test_assistant_reads_from_the_app(admin, hr, new_api):
    add_hr(admin, hr)
    user = new_api()
    user.register("staff@example.com", "Staff")
    names = {t["name"] for t in user.get("/api/tools").json["tools"]}
    assert {"hr__get_employee", "hr__create_ticket"} <= names
    ev = tool_events(user, new_conv(user), 'app hr__get_employee {"employee_id": "E7"}')
    done = next(e for e in ev if e["type"] == "done")["message"]
    assert "Asha Rao" in done["content"] and done["steps"][0]["status"] == "done"
    assert HR.seen[-1]["user"] == "staff@example.com"
    from nova import db
    with db.engine.connect() as c:
        log = c.execute(select(db.tool_log).where(db.tool_log.c.tool == "hr__get_employee")).first()
    assert log.status == "done" and '"app": "hr"' in log.args


def test_write_actions_wait_for_approval(admin, hr, new_api):
    add_hr(admin, hr)
    user = new_api()
    user.register("staff@example.com", "Staff")
    cid = new_conv(user)
    ev = tool_events(user, cid, 'app hr__create_ticket {"title": "Laptop broken", "priority": 2}')
    step = next(e for e in ev if e["type"] == "done")["message"]["steps"][0]
    assert step["status"] == "awaiting" and not any(s["method"] == "POST" for s in HR.seen)
    r, ev = user.stream({"pending_id": step["pending_id"], "approve": True}, path="/api/chat/resume")
    assert "T-1001" in next(e for e in ev if e["type"] == "done")["message"]["content"]
    post = [s for s in HR.seen if s["method"] == "POST"]
    assert len(post) == 1 and post[0]["body"] == {"title": "Laptop broken", "priority": 2}


def test_admin_only_and_disabled_apps_are_hidden(admin, hr, new_api):
    iid, _ = add_hr(admin, hr, audience="admin")
    user = new_api()
    user.register("staff@example.com", "Staff")
    assert not any(t["name"].startswith("hr__") for t in user.get("/api/tools").json["tools"])
    assert user.get("/api/integrations").json["apps"] == []
    assert any(t["name"] == "hr__get_employee" for t in admin.get("/api/tools").json["tools"])
    admin.patch(f"/api/admin/integrations/{iid}", {"enabled": False})
    assert not any(t["name"].startswith("hr__") for t in admin.get("/api/tools").json["tools"])


def test_users_see_available_apps(admin, hr, new_api):
    add_hr(admin, hr)
    user = new_api()
    user.register("staff@example.com", "Staff")
    data = user.get("/api/integrations").json
    assert data["apps"][0]["name"] == "HR system"
    assert {"label": "Raise an HR ticket", "approval": True} in data["apps"][0]["actions"]
    assert data["google"]["configured"] is False


# ------------------------------------------------------------------ Google Workspace
GOOGLE_CFG = {"GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "gsecret",
              "GOOGLE_ALLOWED_DOMAIN": "corp.example"}


@pytest.fixture
def gws(make_app, monkeypatch):
    app = make_app(**GOOGLE_CFG)
    from nova.integrations import google
    calls = []
    state = {"email": "asha@corp.example", "hd": "corp.example", "refresh_fails": False}

    def fake(method, url, data=None, headers=None, form=False, raw=False):
        calls.append((method, url, data))
        if url == google.TOKEN_URL and data.get("grant_type") == "authorization_code":
            return {"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 3600, "scope": " ".join(google.SCOPES)}
        if url == google.TOKEN_URL:
            if state["refresh_fails"]:
                raise google.GoogleError('400: {"error": "invalid_grant"}')
            return {"access_token": "AT-2", "expires_in": 3600}
        if url == google.USERINFO_URL:
            return {"email": state["email"], "email_verified": True, "hd": state["hd"]}
        if url == google.REVOKE_URL:
            return {}
        if "/calendar/v3/calendars/primary/events" in url and method == "GET":
            return {"items": [{"id": "ev1", "summary": "Team stand-up", "start": {"dateTime": "2026-10-22T09:30:00+05:30"},
                               "end": {"dateTime": "2026-10-22T09:45:00+05:30"}, "attendees": [{}, {}]}]}
        if "/calendar/v3/calendars/primary/events" in url:
            return {"id": "evnew1", "summary": data["summary"], "htmlLink": "https://calendar.google.com/event?eid=1",
                    "start": data["start"], "end": data["end"], "attendees": data.get("attendees", [])}
        if "/drive/v3/files?" in url:
            return {"files": [{"id": "file12345", "name": "Leave policy", "mimeType": "application/vnd.google-apps.document"}]}
        if "/drive/v3/files/file12345/export" in url:
            return b"Employees get 24 days of leave."
        if "/drive/v3/files/file12345" in url:
            return {"id": "file12345", "name": "Leave policy", "mimeType": "application/vnd.google-apps.document"}
        raise AssertionError(f"unexpected Google call {url}")

    monkeypatch.setattr(google, "_http", fake)
    api = Api(app)
    api.register("asha@corp.example", "Asha")
    return {"app": app, "api": api, "calls": calls, "state": state, "google": google}


def connect(api):
    url = api.get("/api/integrations/google/connect").json["url"]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return api.get(f"/api/integrations/google/callback?code=CODE&state={urllib.parse.quote(q['state'][0])}"), q


def test_google_connect_flow_and_tokens_encrypted(gws):
    api = gws["api"]
    assert not any(t["name"].startswith(("google_", "calendar_")) for t in api.get("/api/tools").json["tools"])
    r, q = connect(api)
    assert q["client_id"] == ["cid.apps.googleusercontent.com"] and q["hd"] == ["corp.example"]
    assert q["access_type"] == ["offline"] and "drive.readonly" in q["scope"][0]
    assert r.status_code == 302 and "status=connected" in r.headers["Location"]
    info = api.get("/api/integrations").json["google"]
    assert info["connected"] and info["account"] == "asha@corp.example"
    from nova import db
    with db.engine.connect() as c:
        row = c.execute(select(db.user_connections)).first()
    assert "AT-1" not in row.access_token and "RT-1" not in row.refresh_token
    names = {t["name"] for t in api.get("/api/tools").json["tools"]}
    assert {"calendar_events", "google_drive_read", "gmail_search", "email_reply"} <= names


def test_google_state_is_bound_to_the_user(gws):
    api = gws["api"]
    url = api.get("/api/integrations/google/connect").json["url"]
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    other = Api(gws["app"])
    other.register("mallory@corp.example", "Mallory")
    r = other.get(f"/api/integrations/google/callback?code=CODE&state={urllib.parse.quote(state)}")
    assert "status=error" in r.headers["Location"] and not other.get("/api/integrations").json["google"]["connected"]
    r = api.get("/api/integrations/google/callback?code=CODE&state=forged")
    assert "status=error" in r.headers["Location"]


def test_google_other_domain_is_refused(gws):
    gws["state"].update(email="someone@gmail.com", hd=None)
    r, _ = connect(gws["api"])
    assert "status=error" in r.headers["Location"] and "corp.example" in urllib.parse.unquote(r.headers["Location"])
    assert not gws["api"].get("/api/integrations").json["google"]["connected"]


def test_google_tools_calendar_drive_and_refresh(gws):
    api = gws["api"]
    connect(api)
    cid = new_conv(api)
    ev = tool_events(api, cid, "app calendar_events {}")
    assert "Team stand-up" in next(e for e in ev if e["type"] == "done")["message"]["content"]
    ev = tool_events(api, cid, 'app google_drive_read {"file_id": "file12345"}')
    assert "24 days of leave" in next(e for e in ev if e["type"] == "done")["message"]["content"]
    # an expired access token is refreshed automatically
    from nova import db
    with db.engine.begin() as c:
        c.execute(db.user_connections.update().values(expires_at=0))
    tool_events(api, cid, "app calendar_events {}")
    assert any(u == gws["google"].TOKEN_URL and d.get("grant_type") == "refresh_token" for _, u, d in gws["calls"])


def test_google_create_event_needs_approval(gws):
    api = gws["api"]
    connect(api)
    ev = tool_events(api, new_conv(api), 'app calendar_create_event {"title": "1:1", "start": "2026-10-23T10:00:00+05:30", '
                                         '"end": "2026-10-23T10:30:00+05:30"}')
    step = next(e for e in ev if e["type"] == "done")["message"]["steps"][0]
    assert step["status"] == "awaiting"
    assert not any("/events" in u and m == "POST" for m, u, _ in gws["calls"])
    r, ev = api.stream({"pending_id": step["pending_id"], "approve": True}, path="/api/chat/resume")
    assert "Meeting created and confirmed by google" in next(e for e in ev if e["type"] == "done")["message"]["content"]


def test_google_revoked_access_and_disconnect(gws):
    api = gws["api"]
    connect(api)
    from nova import db
    with db.engine.begin() as c:
        c.execute(db.user_connections.update().values(expires_at=0))
    gws["state"]["refresh_fails"] = True
    ev = tool_events(api, new_conv(api), "app calendar_events {}")
    assert "Reconnect Google" in json.dumps(ev)
    assert not api.get("/api/integrations").json["google"]["connected"]
    gws["state"]["refresh_fails"] = False
    connect(api)
    assert api.delete("/api/integrations/google").status_code == 200
    assert any(u == gws["google"].REVOKE_URL for _, u, _ in gws["calls"])
    assert not any(t["name"].startswith(("google_", "calendar_")) for t in api.get("/api/tools").json["tools"])
