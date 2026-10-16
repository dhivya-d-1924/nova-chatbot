"""Registration, login, logout, sessions, CSRF, lockout, password reset, verification, MFA, account lifecycle."""
import time

from sqlalchemy import select, update

from nova import db, security
from tests.conftest import PASSWORD, Api, token_from


def test_register_sets_httponly_cookie_and_no_token_in_body(api):
    r = api.register("a@example.com")
    assert r.status_code == 201
    cookie = r.headers["Set-Cookie"]
    assert "nova_session=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/api" in cookie
    assert "token" not in r.json and r.json["csrf"]
    assert api.get("/api/auth/me").json["user"]["email"] == "a@example.com"


def test_session_token_is_stored_hashed(api):
    r = api.register("a@example.com")
    raw = r.headers["Set-Cookie"].split("nova_session=")[1].split(";")[0]
    with db.engine.connect() as c:
        ids = [s.id for s in c.execute(select(db.sessions))]
    assert raw not in ids and len(ids) == 1 and len(ids[0]) == 64


def test_state_change_requires_csrf_token(api):
    api.register("a@example.com")
    good = api.csrf
    api.csrf = None
    r = api.post("/api/conversations")
    assert r.status_code == 403 and r.json["code"] == "csrf"
    api.csrf = "wrong"
    assert api.post("/api/conversations").status_code == 403
    api.csrf = good
    assert api.post("/api/conversations").status_code == 201


def test_cross_site_origin_is_blocked(api):
    api.register("a@example.com")
    r = api.post("/api/conversations", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json["code"] == "bad_origin"
    assert api.post("/api/conversations", headers={"Origin": "http://localhost:5173"}).status_code == 201


def test_unauthenticated_access_is_rejected(api):
    for path in ("/api/conversations", "/api/documents", "/api/memory", "/api/settings", "/api/account/sessions"):
        r = api.get(path)
        assert r.status_code == 401 and r.json["code"] == "unauthenticated", path


def test_first_account_is_admin_only_when_bootstrap_enabled(make_app):
    app = make_app(BOOTSTRAP_FIRST_ADMIN="false")
    a = Api(app)
    assert a.register("first@example.com").json["user"]["role"] == "user"


def test_admin_emails_ignored_without_verification_in_production_mode(make_app):
    # testing profile with production-like flags: ADMIN_EMAILS should not grant admin to an unverified signup
    app = make_app(BOOTSTRAP_FIRST_ADMIN="false", ADMIN_EMAILS="boss@example.com", REQUIRE_EMAIL_VERIFICATION="true")
    a = Api(app)
    r = a.register("boss@example.com")
    assert r.status_code == 201 and r.json["verification_required"] is True
    with db.engine.connect() as c:
        assert c.execute(select(db.users.c.role).where(db.users.c.email == "boss@example.com")).scalar() == "user"


def test_admin_emails_promoted_after_verification(make_app, outbox):
    app = make_app(BOOTSTRAP_FIRST_ADMIN="false", ADMIN_EMAILS="boss@example.com", REQUIRE_EMAIL_VERIFICATION="true")
    a = Api(app)
    a.register("boss@example.com")
    assert a.login("boss@example.com").json["code"] == "email_unverified"
    token = token_from(outbox[-1]["link"], "verify")
    assert a.post("/api/auth/verify", {"token": token}).status_code == 200
    r = a.login("boss@example.com")
    assert r.status_code == 200 and r.json["user"]["role"] == "admin" and r.json["user"]["email_verified"]


def test_registration_closed(make_app, new_api):
    app = make_app(REGISTRATION_OPEN="false")
    a = Api(app)
    assert a.register("first@example.com").status_code == 201  # bootstrap admin still allowed
    b = Api(app)
    r = b.register("second@example.com")
    assert r.status_code == 403 and r.json["code"] == "registration_closed"


def test_password_policy(api):
    assert api.register("a@example.com", password="short").status_code == 400
    assert api.register("a@example.com", password="password123").json["code"] == "weak_password"
    assert api.register("a@example.com", password="a@example.com").json["code"] == "weak_password"


def test_duplicate_email_is_rejected_case_insensitively(api, new_api):
    api.register("a@example.com")
    assert new_api().register("A@Example.com").status_code == 409


def test_login_wrong_password_is_generic(admin, new_api):
    a = new_api()
    r1 = a.login("admin@example.com", "nope-nope-nope")
    r2 = a.login("nobody@example.com", "nope-nope-nope")
    assert r1.status_code == r2.status_code == 401 and r1.json["error"] == r2.json["error"]


def test_lockout_after_repeated_failures(admin, new_api):
    a = new_api()
    for _ in range(4):
        assert a.login("admin@example.com", "wrong-password-1").status_code == 401
    r = a.login("admin@example.com", "wrong-password-1")
    assert r.status_code == 429 and r.json["code"] == "locked"
    # even the right password is refused while locked
    assert a.login("admin@example.com").status_code == 429
    with db.engine.begin() as c:
        c.execute(update(db.users).values(locked_until=db.now_us() - 1))
    assert a.login("admin@example.com").status_code == 200
    with db.engine.connect() as c:
        assert c.execute(select(db.users.c.failed_logins).where(db.users.c.email == "admin@example.com")).scalar() == 0


def test_logout_revokes_server_session(api):
    r = api.register("a@example.com")
    cookie = r.headers["Set-Cookie"].split(";")[0]
    assert api.post("/api/auth/logout").status_code == 204
    assert api.get("/api/auth/me").status_code == 401
    # replaying the old cookie does not work: the session is gone server-side
    other = api.app.test_client()
    assert other.get("/api/auth/me", headers={"Cookie": cookie}).status_code == 401


def test_logout_all_and_session_management(admin, new_api):
    second = new_api()
    second.login("admin@example.com")
    sessions = admin.get("/api/account/sessions").json
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert admin.delete(f"/api/account/sessions/{other['id']}").status_code == 204
    assert second.get("/api/auth/me").status_code == 401
    assert admin.post("/api/auth/logout-all").status_code == 204
    assert admin.get("/api/auth/me").status_code == 401


def test_idle_and_absolute_session_expiry(api):
    api.register("a@example.com")
    with db.engine.begin() as c:
        c.execute(update(db.sessions).values(last_seen=db.now_us() - 8 * 24 * 3600 * 10**6))
    assert api.get("/api/auth/me").status_code == 401
    api.login("a@example.com")
    with db.engine.begin() as c:
        c.execute(update(db.sessions).values(expires_at=db.now_us() - 1))
    assert api.get("/api/auth/me").status_code == 401


def test_password_reset_flow_is_single_use_and_revokes_sessions(admin, new_api, outbox):
    other_device = new_api()
    other_device.login("admin@example.com")
    anon = new_api()
    r = anon.post("/api/auth/forgot", {"email": "admin@example.com"})
    assert r.status_code == 200
    same = anon.post("/api/auth/forgot", {"email": "missing@example.com"})
    assert same.json["message"] == r.json["message"]  # no account enumeration
    token = token_from(outbox[-1]["link"], "reset")
    r = anon.accept(anon.post("/api/auth/reset", {"token": token, "password": "Brand-New-Pass-7"}))
    assert r.status_code == 200 and anon.get("/api/auth/me").status_code == 200
    assert other_device.get("/api/auth/me").status_code == 401
    assert admin.get("/api/auth/me").status_code == 401
    assert anon.post("/api/auth/reset", {"token": token, "password": "Another-Pass-8"}).json["code"] == "bad_token"
    assert new_api().login("admin@example.com", "Brand-New-Pass-7").status_code == 200


def test_change_password_requires_current_and_signs_out_other_devices(admin, new_api):
    other = new_api()
    other.login("admin@example.com")
    r = admin.post("/api/account/password", {"current_password": "wrong", "new_password": "Brand-New-Pass-7"})
    assert r.status_code == 403
    r = admin.post("/api/account/password", {"current_password": PASSWORD, "new_password": "Brand-New-Pass-7"})
    assert r.status_code == 200
    assert admin.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401


def test_mfa_enrolment_login_replay_and_recovery(admin, new_api):
    r = admin.post("/api/account/mfa/setup", {"password": PASSWORD})
    secret = r.json["secret"]
    assert r.json["uri"].startswith("otpauth://totp/")
    with db.engine.connect() as c:
        stored = c.execute(select(db.users.c.mfa_secret).where(db.users.c.email == "admin@example.com")).scalar()
    assert secret not in stored  # encrypted at rest
    assert admin.post("/api/account/mfa/enable", {"code": "000000"}).status_code == 400
    r = admin.post("/api/account/mfa/enable", {"code": security.totp_now(secret)})
    assert r.status_code == 200 and len(r.json["recovery_codes"]) == 8
    codes = r.json["recovery_codes"]

    b = new_api()
    r = b.login("admin@example.com")
    assert r.json["mfa_required"] and "csrf" not in r.json
    assert b.get("/api/auth/me").status_code == 401
    assert b.post("/api/auth/mfa", {"mfa_token": r.json["mfa_token"], "code": "123456"}).status_code == 401
    # the code used at enrolment can't be replayed
    used = security.totp_now(secret)
    r2 = b.post("/api/auth/mfa", {"mfa_token": r.json["mfa_token"], "code": used})
    assert r2.status_code == 401
    # a recovery code works once
    r3 = b.accept(b.post("/api/auth/mfa", {"mfa_token": r.json["mfa_token"], "code": codes[0]}))
    assert r3.status_code == 200 and b.get("/api/auth/me").status_code == 200
    c2 = new_api()
    t = c2.login("admin@example.com").json["mfa_token"]
    assert c2.post("/api/auth/mfa", {"mfa_token": t, "code": codes[0]}).status_code == 401


def test_mfa_token_is_tamper_proof(admin, new_api):
    b = new_api()
    r = b.post("/api/auth/mfa", {"mfa_token": "forged.token.value", "code": "123456"})
    assert r.status_code == 400 and r.json["code"] == "mfa_expired"


def test_disabled_user_is_signed_out_everywhere(admin, user):
    uid = user.user["id"]
    assert admin.patch(f"/api/admin/users/{uid}", {"disabled": True}).status_code == 200
    assert user.get("/api/auth/me").status_code == 401
    r = Api(user.app).login("user@example.com")
    assert r.status_code == 403 and r.json["code"] == "disabled"


def test_cannot_remove_last_admin(admin):
    uid = admin.user["id"]
    assert admin.patch(f"/api/admin/users/{uid}", {"role": "user"}).status_code == 400
    r = admin.delete("/api/account", {"password": PASSWORD, "confirm": "DELETE"})
    assert r.status_code == 400 and r.json["code"] == "last_admin"


def test_account_export_and_delete(user):
    cid = user.post("/api/conversations").json["id"]
    user.stream({"conversation_id": cid, "message": "hello there"})
    r = user.get("/api/account/export")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    data = r.get_json(force=True)
    assert data["conversations"][0]["messages"][0]["content"] == "hello there"
    assert user.delete("/api/account", {"password": PASSWORD, "confirm": "nope"}).status_code == 400
    assert user.delete("/api/account", {"password": PASSWORD, "confirm": "DELETE"}).status_code == 204
    with db.engine.connect() as c:
        assert not c.execute(select(db.users).where(db.users.c.email == "user@example.com")).first()
        assert not c.execute(select(db.conversations).where(db.conversations.c.id == cid)).first()
        assert not c.execute(select(db.messages).where(db.messages.c.conversation_id == cid)).first()
    assert user.get("/api/auth/me").status_code == 401


def test_settings_sync_between_devices(admin, new_api):
    r = admin.put("/api/settings", {"theme": "light", "voice_lang": "ta-IN", "auto_read": True, "agent_mode": "coding"})
    assert r.status_code == 200
    phone = new_api()
    phone.login("admin@example.com")
    s = phone.get("/api/auth/me").json["user"]["settings"]
    assert s["theme"] == "light" and s["voice_lang"] == "ta-IN" and s["auto_read"] is True and s["agent_mode"] == "coding"
    assert admin.put("/api/settings", {"theme": "neon"}).status_code == 400
    assert admin.put("/api/settings", {"auto_read": "yes"}).status_code == 400


def test_audit_log_records_security_events(admin, new_api):
    new_api().login("admin@example.com", "wrong-password-x")
    r = admin.get("/api/admin/audit?action=login")
    actions = {i["action"] for i in r.json["items"]}
    assert "login.failed" in actions
    blob = str(r.json)
    assert "wrong-password-x" not in blob and PASSWORD not in blob


def test_rate_limit_on_auth(make_app):
    app = make_app(AUTH_RATE_LIMIT_PER_MINUTE="3")
    a = Api(app)
    codes = [a.login("x@example.com", "whatever-pass").status_code for _ in range(4)]
    assert codes[-1] == 429


def test_spoofed_forwarded_for_does_not_bypass_rate_limit(make_app):
    app = make_app(AUTH_RATE_LIMIT_PER_MINUTE="2")  # TRUSTED_PROXY_COUNT=0: header is ignored
    a = Api(app)
    codes = [a.post("/api/auth/login", {"email": "x@example.com", "password": "whatever-pass"},
                    headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(3)]
    assert codes[-1] == 429


def test_verification_link_resend_is_generic(make_app, outbox):
    app = make_app(REQUIRE_EMAIL_VERIFICATION="true")
    a = Api(app)
    a.register("v@example.com")
    r1 = a.post("/api/auth/resend-verification", {"email": "v@example.com"})
    r2 = a.post("/api/auth/resend-verification", {"email": "ghost@example.com"})
    assert r1.json["message"] == r2.json["message"]
    assert len(outbox) == 2  # registration + resend; nothing for the unknown address


def test_totp_algorithm_matches_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890" (base32 GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ) at T=59 -> 287082 (6 digits)
    assert security.totp_now("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59) == "287082"
    assert security.totp_now("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=1111111109) == "081804"
    assert time.time() > 0
