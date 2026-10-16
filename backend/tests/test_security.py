"""Response headers, CORS, SSRF guards, calculator safety, config validation and error hygiene."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from nova import tools
from nova.config import ConfigError, Settings


def test_security_headers_on_api(api):
    r = api.get("/api/health")
    h = r.headers
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert h["Cache-Control"] == "no-store" and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["Referrer-Policy"] and h["Permissions-Policy"].startswith("camera=()") and h["X-Request-Id"]


def test_hsts_only_when_secure(make_app):
    app = make_app(COOKIE_SECURE="true")
    assert "Strict-Transport-Security" in app.test_client().get("/api/health").headers


def test_cors_allowlist(app):
    c = app.test_client()
    ok = c.options("/api/conversations", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
    assert ok.headers.get("Access-Control-Allow-Credentials") == "true"
    bad = c.options("/api/conversations", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "Access-Control-Allow-Origin" not in bad.headers


def test_unexpected_errors_do_not_leak_details(app):
    def boom():
        raise RuntimeError("database password is hunter2")
    app.view_functions["data.live"] = lambda: boom()
    r = app.test_client().get("/api/health/live")
    assert r.status_code == 500 and "hunter2" not in r.get_data(as_text=True) and r.json["code"] == "server_error"


def test_unknown_api_route_is_json_404(api):
    r = api.get("/api/does-not-exist")
    assert r.status_code == 404 and r.is_json


def test_method_not_allowed_is_json(api):
    r = api.c.put("/api/health")
    assert r.status_code == 405 and r.is_json


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:5000/", "http://169.254.169.254/latest/meta-data",
                                 "http://[::1]/", "http://10.0.0.5/", "file:///etc/passwd", "gopher://x", "http://0.0.0.0/"])
def test_ssrf_blocked(url):
    with pytest.raises(tools.ToolError):
        tools.check_url(url)


def test_dns_rebinding_guard_checks_connected_peer():
    """Even if a hostname passed check_url, connecting to a private address is refused at socket level."""
    srv = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    with pytest.raises(tools.ToolError):
        tools._guarded_create_connection(("127.0.0.1", srv.server_port), timeout=2)
    srv.server_close()


def test_redirects_are_rechecked(monkeypatch):
    handler = tools._SafeRedirect()
    with pytest.raises(tools.ToolError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/admin")


def test_allowed_domains(monkeypatch):
    monkeypatch.setenv("ALLOWED_DOMAINS", "example.com")
    with pytest.raises(tools.ToolError):
        tools.check_url("https://not-allowed.org/")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.215.14", 443))])
    assert tools.check_url("https://docs.example.com/page")


@pytest.mark.parametrize("expr", ["__import__('os').system('id')", "open('/etc/passwd')", "().__class__", "9**9**9**9",
                                  "a" * 300, "lambda: 1"])
def test_calculator_rejects_unsafe_input(expr):
    with pytest.raises(tools.ToolError):
        tools.calculate(expr)


def test_calculator_math():
    assert tools.calculate("48213 * 9127").endswith("= 440040051")
    assert tools.calculate("sqrt(144)").endswith("= 12.0")


def test_email_tool_validates_recipient():
    for bad in ("", "a@b", "x@y.com, z@w.com", "x@y.com\nBcc: q@r.com"):
        with pytest.raises(tools.ToolError):
            tools.send_email(bad, "s", "b")


def _prod(**env):
    base = {"APP_ENV": "production", "SECRET_KEY": "x" * 40, "APP_URL": "https://chat.example.com",
            "ALLOWED_ORIGINS": "https://chat.example.com", "LLM_API_KEY": "sk-real", "DATABASE_URL": "sqlite:////data/chat.db"}
    base.update(env)
    return Settings().load({**base, "_SKIP_ENV_FILE": "1"})


def test_production_config_validation():
    assert _prod().errors == []
    assert any("SECRET_KEY" in e for e in _prod(SECRET_KEY="").errors)
    assert any("SECRET_KEY" in e for e in _prod(SECRET_KEY="short").errors)
    assert any("'*'" in e for e in _prod(ALLOWED_ORIGINS="*").errors)
    assert any("https" in e for e in _prod(APP_URL="http://chat.example.com").errors)
    assert any("FLASK_DEBUG" in e for e in _prod(FLASK_DEBUG="1").errors)
    assert any("placeholder" in e for e in _prod(LLM_API_KEY="sk-your-key-here").errors)
    assert any("COOKIE_SAMESITE=None" in e for e in _prod(COOKIE_SAMESITE="None", COOKIE_SECURE="false").errors)
    assert any("number" in e for e in _prod(RATE_LIMIT_PER_MINUTE="lots").errors)
    s = _prod()
    assert s.COOKIE_SECURE is True and s.BOOTSTRAP_FIRST_ADMIN is False and s.LOG_FORMAT == "json"
    with pytest.raises(ConfigError):
        _prod(SECRET_KEY="").validate()


def test_production_warns_about_unsafe_choices():
    s = _prod(BOOTSTRAP_FIRST_ADMIN="true", WEB_CONCURRENCY="4")
    assert any("first registrant" in w for w in s.warnings) and any("REDIS_URL" in w for w in s.warnings)


def test_frontend_csp_has_no_unsafe_inline_scripts():
    from nova import security
    assert "script-src 'self';" in security.CSP
