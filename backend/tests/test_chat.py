"""Chat streaming against a real OpenAI-compatible HTTP server, provider failures, quotas, summaries, conversation tools."""
import json

from sqlalchemy import func, select, update

from nova import db
from tests.conftest import Api
from tests.fake_llm import STATE


def new_conv(api):
    return api.post("/api/conversations").json["id"]


def test_stream_reply_is_saved_and_usage_recorded(admin):
    cid = new_conv(admin)
    r, ev = admin.stream({"conversation_id": cid, "message": "hello world"})
    assert r.status_code == 200 and r.headers["X-Accel-Buffering"] == "no"
    text = "".join(e["text"] for e in ev if e["type"] == "delta")
    assert text == "Echo: hello world"
    assert ev[0]["type"] == "user" and ev[0]["message"]["id"]  # the stored question's id, so the UI can edit it
    done = ev[-1]
    assert done["type"] == "done" and done["message"]["content"] == text and done["message"]["created_at"]
    conv = admin.get(f"/api/conversations/{cid}").json
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"] and conv["title"] == "hello world"
    with db.engine.connect() as c:
        u = c.execute(select(db.usage)).first()
    assert u.kind == "chat" and u.prompt_tokens > 0 and u.completion_tokens > 0 and u.estimated == 0 and u.status == "ok"


def test_non_streaming_endpoint(admin):
    cid = new_conv(admin)
    r = admin.post("/api/chat", {"conversation_id": cid, "message": "capital of France?"})
    assert r.status_code == 200 and "Paris" in r.json["message"]["content"]


def test_regenerate_replaces_last_reply(admin):
    cid = new_conv(admin)
    admin.stream({"conversation_id": cid, "message": "first"})
    r, ev = admin.stream({"conversation_id": cid, "regenerate": True})
    assert ev[-1]["type"] == "done"
    msgs = admin.get(f"/api/conversations/{cid}").json["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_edit_message_truncates_later_history(admin):
    cid = new_conv(admin)
    admin.stream({"conversation_id": cid, "message": "one"})
    admin.stream({"conversation_id": cid, "message": "two"})
    first_user = admin.get(f"/api/conversations/{cid}").json["messages"][0]
    r, ev = admin.stream({"conversation_id": cid, "message": "one edited", "edit_message_id": first_user["id"]})
    assert ev[-1]["message"]["content"] == "Echo: one edited"
    msgs = admin.get(f"/api/conversations/{cid}").json["messages"]
    assert [m["content"] for m in msgs] == ["one edited", "Echo: one edited"] and msgs[0]["edited_at"]


def test_provider_error_before_any_text(admin):
    cid = new_conv(admin)
    r, ev = admin.stream({"conversation_id": cid, "message": "please fail500"})
    assert ev[-1]["type"] == "error" and "500" in ev[-1]["message"] and ev[-1]["saved"] is None
    with db.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(db.errors)).scalar() == 1
        assert c.execute(select(db.usage.c.status)).scalar() == "error"
    # the question stays so the user can retry; no fake assistant reply was stored
    assert [m["role"] for m in admin.get(f"/api/conversations/{cid}").json["messages"]] == ["user"]


def test_mid_stream_failure_keeps_partial_reply_marked(admin):
    cid = new_conv(admin)
    r, ev = admin.stream({"conversation_id": cid, "message": "failmid now"})
    assert any(e["type"] == "delta" for e in ev)
    err = ev[-1]
    assert err["type"] == "error" and err["saved"] and "interrupted" in err["saved"]["content"]


def test_provider_unreachable_gives_clear_error(make_app):
    app = make_app(LLM_BASE_URL="http://127.0.0.1:9/v1", LLM_TIMEOUT_SECONDS="5")
    a = Api(app)
    a.register("a@example.com")
    cid = new_conv(a)
    r, ev = a.stream({"conversation_id": cid, "message": "hi"})
    assert ev[-1]["type"] == "error" and "Could not reach the AI provider" in ev[-1]["message"]


def test_not_configured_is_explicit(make_app):
    app = make_app(LLM_API_KEY="", LLM_BASE_URL="")
    a = Api(app)
    a.register("a@example.com")
    cid = new_conv(a)
    r = a.post("/api/chat/stream", {"conversation_id": cid, "message": "hi"})
    assert r.status_code == 503 and r.json["code"] == "llm_not_configured"
    assert a.get(f"/api/conversations/{cid}").json["messages"] == []  # nothing stored on failure


def test_daily_token_quota(make_app):
    app = make_app(DAILY_TOKEN_LIMIT_PER_USER="1")
    a = Api(app)
    a.register("a@example.com")
    cid = new_conv(a)
    a.stream({"conversation_id": cid, "message": "hi"})
    r = a.post("/api/chat/stream", {"conversation_id": cid, "message": "again"})
    assert r.status_code == 429 and r.json["code"] == "quota_exceeded"


def test_monthly_budget(make_app):
    app = make_app(MONTHLY_BUDGET_USD="0.0000001", LLM_PRICE_INPUT_PER_1M="10", LLM_PRICE_OUTPUT_PER_1M="10")
    a = Api(app)
    a.register("a@example.com")
    cid = new_conv(a)
    a.stream({"conversation_id": cid, "message": "hi"})
    r = a.post("/api/chat/stream", {"conversation_id": cid, "message": "again"})
    assert r.status_code == 503 and r.json["code"] == "budget_exceeded"


def test_concurrency_limit(make_app):
    from nova import security
    app = make_app(MAX_CONCURRENT_STREAMS_PER_USER="1")
    a = Api(app)
    a.register("a@example.com")
    cid = new_conv(a)
    assert security.limiter.acquire(f"streams:{a.user['id']}", 1, 60)  # simulate a reply already in progress
    r = a.post("/api/chat/stream", {"conversation_id": cid, "message": "hi"})
    assert r.status_code == 429 and r.json["code"] == "too_many_streams"
    security.limiter.release(f"streams:{a.user['id']}")
    r, ev = a.stream({"conversation_id": cid, "message": "hi"})
    assert ev[-1]["type"] == "done"
    r, ev = a.stream({"conversation_id": cid, "message": "hi again"})  # slot was released after the first reply
    assert ev[-1]["type"] == "done"


def test_long_conversations_are_summarized(make_app):
    app = make_app(MAX_HISTORY_MESSAGES="4")
    a = Api(app)
    a.register("a@example.com")
    cid = new_conv(a)
    for i in range(5):
        a.stream({"conversation_id": cid, "message": f"message {i}"})
    with db.engine.connect() as c:
        conv = c.execute(select(db.conversations).where(db.conversations.c.id == cid)).first()
    assert conv.summary and conv.summary.startswith("SUMMARY")
    sys_prompt = STATE["last_request"]["messages"][0]["content"]
    assert "Summary of the earlier part" in sys_prompt


def test_empty_and_oversized_messages_rejected(admin):
    cid = new_conv(admin)
    assert admin.post("/api/chat/stream", {"conversation_id": cid, "message": "   "}).json["code"] == "empty_message"
    assert admin.post("/api/chat/stream", {"conversation_id": cid, "message": "x" * 9000}).json["code"] == "too_long"
    assert admin.post("/api/chat/stream", {"conversation_id": cid, "message": 5}).status_code == 400
    assert admin.post("/api/chat/stream", ["not", "an", "object"]).status_code == 400


def test_conversation_rename_pin_archive_search_export(admin):
    a, b = new_conv(admin), new_conv(admin)
    admin.stream({"conversation_id": a, "message": "talk about zebras"})
    admin.stream({"conversation_id": b, "message": "talk about lions"})
    assert admin.patch(f"/api/conversations/{a}", {"title": "Zebra notes"}).json["title"] == "Zebra notes"
    assert admin.patch(f"/api/conversations/{a}", {"title": ""}).status_code == 400
    admin.patch(f"/api/conversations/{a}", {"pinned": True})
    assert admin.get("/api/conversations").json[0]["id"] == a
    admin.patch(f"/api/conversations/{b}", {"archived": True})
    assert [c["id"] for c in admin.get("/api/conversations").json] == [a]
    assert [c["id"] for c in admin.get("/api/conversations?archived=1").json] == [b]
    assert [c["id"] for c in admin.get("/api/conversations?q=lions&archived=1").json] == [b]
    hits = admin.get("/api/search?q=zebras").json
    assert hits and hits[0]["conversation_id"] == a and "zebras" in hits[0]["snippet"]
    md = admin.get(f"/api/conversations/{a}/export?format=md")
    assert md.status_code == 200 and "# Zebra notes" in md.get_data(as_text=True)
    js = admin.get(f"/api/conversations/{a}/export?format=json")
    assert json.loads(js.get_data(as_text=True))["messages"][0]["content"] == "talk about zebras"
    # search wildcards are escaped
    assert admin.get("/api/search?q=%25%25").json == []


def test_sending_unarchives(admin):
    cid = new_conv(admin)
    admin.patch(f"/api/conversations/{cid}", {"archived": True})
    admin.stream({"conversation_id": cid, "message": "back again"})
    assert cid in [c["id"] for c in admin.get("/api/conversations").json]


def test_feedback(admin):
    cid = new_conv(admin)
    _, ev = admin.stream({"conversation_id": cid, "message": "rate me"})
    mid = ev[-1]["message"]["id"]
    assert admin.post(f"/api/messages/{mid}/feedback", {"rating": 1}).json["rating"] == 1
    assert admin.post(f"/api/messages/{mid}/feedback", {"rating": 5}).status_code == 400
    assert admin.get(f"/api/conversations/{cid}").json["messages"][1]["rating"] == 1
    assert admin.get("/api/admin/stats").json["feedback"]["up"] == 1


def test_clear_and_delete_conversation(admin):
    cid = new_conv(admin)
    admin.stream({"conversation_id": cid, "message": "temporary"})
    assert admin.delete(f"/api/conversations/{cid}/messages").status_code == 204
    assert admin.get(f"/api/conversations/{cid}").json["messages"] == []
    assert admin.delete(f"/api/conversations/{cid}").status_code == 204
    assert admin.get(f"/api/conversations/{cid}").status_code == 404
    with db.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(db.messages)).scalar() == 0


def test_stream_uses_usage_estimate_when_provider_omits_it(make_app):
    app = make_app(LLM_STREAM_USAGE="false")
    a = Api(app)
    a.register("a@example.com")
    cid = new_conv(a)
    a.stream({"conversation_id": cid, "message": "hello"})
    with db.engine.connect() as c:
        assert c.execute(select(db.usage.c.estimated)).scalar() == 1


def test_admin_usage_metrics(admin):
    cid = new_conv(admin)
    admin.stream({"conversation_id": cid, "message": "hello"})
    admin.stream({"conversation_id": cid, "message": "fail500"})
    u = admin.get("/api/admin/usage?days=7").json
    assert u["totals"]["requests"] == 2 and u["totals"]["failed"] == 1
    assert u["per_day"][-1]["requests"] == 2 and u["by_model"][0]["model"] == "fake-model"
    with db.engine.begin() as c:
        c.execute(update(db.usage).values(cost_usd=0.5))
    assert admin.get("/api/admin/usage").json["totals"]["cost_usd"] == 1.0
