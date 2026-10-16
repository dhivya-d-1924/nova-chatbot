"""Cross-user data isolation (IDOR) and role-based access control on every resource type."""
import io

from tests.conftest import Api

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def setup_owner_data(owner):
    cid = owner.post("/api/conversations").json["id"]
    _, ev = owner.stream({"conversation_id": cid, "message": "secret plans"})
    mid = ev[-1]["message"]["id"]
    doc = owner.post("/api/documents", data={"file": (io.BytesIO(b"The vault code is 1234."), "secret.txt")},
                     content_type="multipart/form-data").json
    att = owner.post("/api/attachments", data={"file": (io.BytesIO(PNG), "x.png")}, content_type="multipart/form-data").json
    owner.put("/api/memory/settings", {"enabled": True})
    mem = owner.post("/api/memory", {"text": "Likes tea"}).json
    return {"cid": cid, "mid": mid, "doc": doc["id"], "att": att["id"], "mem": mem["id"]}


def test_other_user_cannot_touch_owner_resources(admin, user):
    ids = setup_owner_data(admin)
    checks = [
        user.get(f"/api/conversations/{ids['cid']}"),
        user.patch(f"/api/conversations/{ids['cid']}", {"title": "pwned"}),
        user.delete(f"/api/conversations/{ids['cid']}"),
        user.delete(f"/api/conversations/{ids['cid']}/messages"),
        user.get(f"/api/conversations/{ids['cid']}/export"),
        user.post(f"/api/messages/{ids['mid']}/feedback", {"rating": -1}),
        user.delete(f"/api/documents/{ids['doc']}"),
        user.post(f"/api/documents/{ids['doc']}/reindex"),
        user.get(f"/api/attachments/{ids['att']}"),
        user.delete(f"/api/attachments/{ids['att']}"),
        user.delete(f"/api/memory/{ids['mem']}"),
        user.post("/api/chat/stream", {"conversation_id": ids["cid"], "message": "hi"}),
        user.post("/api/handoffs", {"note": "help", "conversation_id": ids["cid"]}),
    ]
    assert all(r.status_code == 404 for r in checks), [r.status_code for r in checks]
    assert user.get("/api/conversations").json == []
    assert user.get("/api/documents").json == []
    assert user.get("/api/memory").json["items"] == []
    assert user.get("/api/search?q=secret").json == []
    # the owner's data is intact
    assert admin.get(f"/api/conversations/{ids['cid']}").json["title"] == "secret plans"


def test_injected_document_ids_are_ignored(admin, user):
    ids = setup_owner_data(admin)
    cid = user.post("/api/conversations").json["id"]
    _, ev = user.stream({"conversation_id": cid, "message": "what is the vault code?", "doc_ids": [ids["doc"]]})
    done = ev[-1]["message"]
    assert not done["sources"] and "1234" not in done["content"]


def test_injected_attachment_ids_are_rejected(admin, user):
    ids = setup_owner_data(admin)
    cid = user.post("/api/conversations").json["id"]
    r = user.post("/api/chat/stream", {"conversation_id": cid, "message": "describe", "attachment_ids": [ids["att"]]})
    assert r.status_code == 400 and r.json["code"] == "attachment_gone"


def test_admin_routes_require_admin_role(user):
    for method, path in [("get", "/api/admin/stats"), ("get", "/api/admin/users"), ("get", "/api/admin/usage"),
                         ("get", "/api/admin/audit"), ("get", "/api/admin/errors"), ("get", "/api/admin/handoffs"),
                         ("get", "/api/admin/evals"), ("post", "/api/admin/evals/run"),
                         ("patch", "/api/admin/users/x"), ("post", "/api/admin/handoffs/x/resolve")]:
        r = getattr(user, method)(path, json={}) if method != "get" else user.get(path)
        assert r.status_code == 403, path


def test_user_cannot_escalate_via_settings_or_profile(user):
    user.put("/api/settings", {"theme": "light", "role": "admin"})
    user.patch("/api/account/profile", {"name": "Hacker", "role": "admin"})
    assert user.get("/api/auth/me").json["user"]["role"] == "user"


def test_report_download_is_owner_only(admin, user):
    from nova.agent import tool_save_report
    ctx = {"uid": admin.user["id"]}
    _, _, extra = tool_save_report(ctx, {"title": "Private", "content": "numbers"})
    rid = extra["report_id"]
    assert admin.get(f"/api/reports/{rid}").status_code == 200
    assert user.get(f"/api/reports/{rid}").status_code == 404


def test_admin_user_list_paginates_and_filters(admin, new_api):
    for i in range(5):
        new_api().register(f"person{i}@example.com", f"Person {i}")
    r = admin.get("/api/admin/users?limit=2&page=2").json
    assert r["total"] == 6 and r["pages"] == 3 and len(r["items"]) == 2
    r = admin.get("/api/admin/users?q=person3").json
    assert [u["email"] for u in r["items"]] == ["person3@example.com"]
    assert admin.get("/api/admin/users?role=admin").json["total"] == 1
    assert admin.get("/api/admin/users?limit=abc").status_code == 400


def test_handoff_reply_reaches_owner_chat_only(admin, user):
    cid = user.post("/api/conversations").json["id"]
    hid = user.post("/api/handoffs", {"note": "I need a person", "conversation_id": cid}).json["id"]
    assert user.get("/api/handoffs").json[0]["status"] == "open"
    lst = admin.get("/api/admin/handoffs?status=open").json
    assert lst["total"] == 1 and lst["items"][0]["user"] == "user@example.com"
    r = admin.post(f"/api/admin/handoffs/{hid}/resolve", {"reply": "Hi, I'm here."})
    assert r.json["delivered_to_chat"] is True
    assert admin.post(f"/api/admin/handoffs/{hid}/resolve", {"reply": "again"}).status_code == 409
    msgs = user.get(f"/api/conversations/{cid}").json["messages"]
    assert "Hi, I'm here." in msgs[-1]["content"]


def test_handoff_for_deleted_conversation_is_not_orphaned(admin, user):
    cid = user.post("/api/conversations").json["id"]
    hid = user.post("/api/handoffs", {"note": "help", "conversation_id": cid}).json["id"]
    user.delete(f"/api/conversations/{cid}")
    r = admin.post(f"/api/admin/handoffs/{hid}/resolve", {"reply": "ok"})
    assert r.status_code == 200 and r.json["delivered_to_chat"] is False


def test_sessions_of_one_user_do_not_leak(admin, user):
    ids = {s["id"] for s in admin.get("/api/account/sessions").json}
    theirs = user.get("/api/account/sessions").json
    assert not ids & {s["id"] for s in theirs}
    assert user.delete(f"/api/account/sessions/{next(iter(ids))}").status_code == 404


def test_cookie_from_one_app_user_cannot_be_reused_with_other_csrf(admin, user):
    # the CSRF token is bound to the session: user's token does not work with admin's cookie
    forged = Api(admin.app)
    forged.c = admin.c
    forged.csrf = user.csrf
    assert forged.post("/api/conversations").status_code == 403
