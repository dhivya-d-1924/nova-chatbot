"""WhatsApp, Microsoft Teams and email channels: provider verification, linking, chat, approvals and isolation."""
import hashlib
import hmac
import json
import time
from email.message import EmailMessage

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.fake_llm import STATE

WA_SECRET = "wa-app-secret"
VERIFY = "verify-token-1234567890"
TEAMS_APP = "11111111-2222-3333-4444-555555555555"
TENANT = "tenant-abc"
SERVICE = "https://smba.trafficmanager.net/emea/"
CHANNEL_CFG = {
    "WHATSAPP_ACCESS_TOKEN": "wa-token", "WHATSAPP_PHONE_NUMBER_ID": "1000", "WHATSAPP_APP_SECRET": WA_SECRET,
    "WHATSAPP_VERIFY_TOKEN": VERIFY, "TEAMS_APP_ID": TEAMS_APP, "TEAMS_APP_PASSWORD": "teams-secret",
    "TEAMS_TENANT_ID": TENANT, "EMAIL_CHANNEL_IMAP_HOST": "imap.corp.test", "EMAIL_CHANNEL_USER": "nova@corp.test",
    "EMAIL_CHANNEL_PASSWORD": "pw", "EMAIL_CHANNEL_AUTHSERV_ID": "mx.corp.test", "SMTP_HOST": "smtp.corp.test",
    "SMTP_FROM": "nova@corp.test",
}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def ch(make_app, monkeypatch):
    """App with all three channels configured; outbound calls are captured instead of sent."""
    app = make_app(**CHANNEL_CFG)
    from nova.channels import email_channel, teams, whatsapp
    out = {"wa": [], "teams": [], "email": [], "sent_email": []}
    monkeypatch.setattr(whatsapp, "_api", lambda p: out["wa"].append(p) or {})
    monkeypatch.setattr(teams, "_post", lambda url, conv, act, reply_to=None: out["teams"].append(act) or {})
    monkeypatch.setattr(teams, "_signing_key", lambda token: KEY.public_key())
    monkeypatch.setattr(email_channel, "smtp_send", lambda to, subj, body, from_addr=None, headers=None:
                        out["email"].append({"to": to, "subject": subj, "body": body, "headers": headers or {}}))
    import nova.tools as tools
    monkeypatch.setattr(tools, "send_email", lambda to, subj, body: out["sent_email"].append(to) or f"Email sent to {to}.")
    out["app"] = app
    return out


def wa_texts(out):
    return [p["text"]["body"] for p in out["wa"] if p.get("type") == "text"]


def wa_post(client, frm, text, mid=None, secret=WA_SECRET, name="Priya"):
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "1000"}, "contacts": [{"wa_id": frm, "profile": {"name": name}}],
        "messages": [{"from": frm, "id": mid or f"wamid.{time.time_ns()}", "type": "text", "text": {"body": text}}]}}]}]}
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/channels/whatsapp/webhook", data=raw, headers={"X-Hub-Signature-256": sig,
                                                                         "Content-Type": "application/json"})


def link_code(api, channel):
    r = api.post(f"/api/channels/{channel}/link-code")
    assert r.status_code == 200, r.json
    return r.json["code"]


def signed_in(app, email="staff@corp.test", name="Staff"):
    from tests.conftest import Api
    a = Api(app)
    assert a.register(email, name).status_code == 201
    return a


# ------------------------------------------------------------------ WhatsApp
def test_whatsapp_subscription_check(ch):
    c = ch["app"].test_client()
    ok = c.get(f"/api/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token={VERIFY}&hub.challenge=12345")
    assert ok.status_code == 200 and ok.get_data(as_text=True) == "12345"
    bad = c.get("/api/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1")
    assert bad.status_code == 403


def test_whatsapp_rejects_bad_signature(ch):
    calls = STATE["calls"]
    r = wa_post(ch["app"].test_client(), "919800000001", "hello", secret="not-the-secret")
    assert r.status_code == 401
    unsigned = ch["app"].test_client().post("/api/channels/whatsapp/webhook", json={"entry": []})
    assert unsigned.status_code == 401
    assert ch["wa"] == [] and STATE["calls"] == calls


def test_whatsapp_unknown_sender_gets_instructions_once(ch):
    c = ch["app"].test_client()
    assert wa_post(c, "919800000002", "hi").status_code == 200
    assert wa_post(c, "919800000002", "hello?").status_code == 200
    texts = wa_texts(ch)
    assert len(texts) == 1 and "link your account" in texts[0]
    assert STATE["calls"] == 0  # never reaches the AI


def test_whatsapp_link_chat_and_conversation_in_web(ch):
    api = signed_in(ch["app"])
    code = link_code(api, "whatsapp")
    c = ch["app"].test_client()
    wa_post(c, "919800000003", f"link {code}")
    assert "Linked to the Nova account" in wa_texts(ch)[-1]
    links = api.get("/api/channels").json["channels"]
    wa = next(x for x in links if x["id"] == "whatsapp")
    assert wa["enabled"] and len(wa["links"]) == 1 and "Priya" in wa["links"][0]["display"]
    assert "9800000003" not in wa["links"][0]["display"]  # number is masked

    wa_post(c, "919800000003", "What is our **leave** policy?")
    reply = wa_texts(ch)[-1]
    assert reply.startswith("Echo:") and "**" not in reply  # Markdown converted to WhatsApp style
    convs = api.get("/api/conversations").json
    assert any(cv["title"] == "WhatsApp chat" for cv in convs)

    # the code is single use: another phone can't reuse it
    wa_post(c, "919800000099", f"link {code}")
    assert "wrong or has expired" in wa_texts(ch)[-1]


def test_whatsapp_duplicate_delivery_is_handled_once(ch):
    api = signed_in(ch["app"])
    c = ch["app"].test_client()
    wa_post(c, "919800000004", f"link {link_code(api, 'whatsapp')}")
    before = len(wa_texts(ch))
    wa_post(c, "919800000004", "same message", mid="wamid.fixed")
    wa_post(c, "919800000004", "same message", mid="wamid.fixed")
    assert len(wa_texts(ch)) == before + 1


def test_whatsapp_approval_yes_runs_the_action_once(ch):
    api = signed_in(ch["app"])
    c = ch["app"].test_client()
    wa_post(c, "919800000005", f"link {link_code(api, 'whatsapp')}")
    wa_post(c, "919800000005", "please email bob@example.com")
    assert "Reply YES to approve" in wa_texts(ch)[-1] and ch["sent_email"] == []
    wa_post(c, "919800000005", "YES")
    assert ch["sent_email"] == ["bob@example.com"]
    assert "Email sent" in wa_texts(ch)[-1]
    wa_post(c, "919800000005", "yes")  # nothing pending any more: treated as a normal message
    assert ch["sent_email"] == ["bob@example.com"]


def test_whatsapp_approval_no_declines(ch):
    api = signed_in(ch["app"])
    c = ch["app"].test_client()
    wa_post(c, "919800000006", f"link {link_code(api, 'whatsapp')}")
    wa_post(c, "919800000006", "email boss@example.com")
    wa_post(c, "919800000006", "no")
    assert ch["sent_email"] == [] and "declined" in wa_texts(ch)[-1].lower()


def test_whatsapp_new_unlink_help_and_docs(ch):
    api = signed_in(ch["app"])
    c = ch["app"].test_client()
    wa_post(c, "919800000007", f"link {link_code(api, 'whatsapp')}")
    wa_post(c, "919800000007", "help")
    assert "docs <question>" in wa_texts(ch)[-1]
    wa_post(c, "919800000007", "docs what is the travel policy")
    assert "haven't uploaded any documents" in wa_texts(ch)[-1]
    wa_post(c, "919800000007", "new")
    assert "new chat" in wa_texts(ch)[-1].lower()
    wa_post(c, "919800000007", "unlink")
    assert "no longer linked" in wa_texts(ch)[-1]
    assert api.get("/api/channels").json["channels"][0]["links"] == []


def test_link_code_brute_force_is_limited(ch):
    c = ch["app"].test_client()
    for i in range(7):
        wa_post(c, "919800000008", f"link {100000 + i}")
    assert "Too many linking attempts" in wa_texts(ch)[-1]


def test_disabled_account_is_refused(ch):
    admin = signed_in(ch["app"], "admin@corp.test", "Admin")  # first account is the administrator in testing
    api = signed_in(ch["app"])
    c = ch["app"].test_client()
    wa_post(c, "919800000009", f"link {link_code(api, 'whatsapp')}")
    uid = api.user["id"]
    assert admin.patch(f"/api/admin/users/{uid}", {"disabled": True}).status_code == 200
    calls = STATE["calls"]
    wa_post(c, "919800000009", "hello")
    assert "disabled" in wa_texts(ch)[-1] and STATE["calls"] == calls


def test_links_are_private_and_deleted_with_account(ch):
    signed_in(ch["app"], "admin@corp.test", "Admin")
    alice, bob = signed_in(ch["app"], "alice@corp.test"), signed_in(ch["app"], "bob@corp.test")
    c = ch["app"].test_client()
    wa_post(c, "919800000010", f"link {link_code(alice, 'whatsapp')}")
    lid = alice.get("/api/channels").json["channels"][0]["links"][0]["id"]
    assert bob.delete(f"/api/channels/links/{lid}").status_code == 404
    assert bob.get("/api/channels").json["channels"][0]["links"] == []
    # a number already linked to Alice can't be taken over by Bob's code
    wa_post(c, "919800000010", f"link {link_code(bob, 'whatsapp')}")
    assert "already linked to another Nova user" in wa_texts(ch)[-1]
    r = alice.delete("/api/account", {"password": "Correct-Horse-9", "confirm": "DELETE"})
    assert r.status_code == 204
    from nova import db
    with db.engine.connect() as conn:
        assert conn.execute(db.channel_links.select()).all() == []


def test_disabled_channel_endpoints(make_app):
    app = make_app()
    api = signed_in(app)
    assert api.post("/api/channels/whatsapp/link-code").status_code == 409
    assert app.test_client().post("/api/channels/whatsapp/webhook", data=b"{}").status_code == 404
    assert app.test_client().post("/api/channels/teams/messages", json={}).status_code == 404
    assert all(not x["enabled"] for x in api.get("/api/channels").json["channels"])


# ------------------------------------------------------------------ Teams
def teams_token(aud=TEAMS_APP, iss="https://api.botframework.com", serviceurl=SERVICE, key=KEY, exp=600):
    now = int(time.time())
    return jwt.encode({"aud": aud, "iss": iss, "serviceurl": serviceurl, "nbf": now - 10, "exp": now + exp},
                      key, algorithm="RS256")


def teams_activity(text, aad="AAD-USER-1", tenant=TENANT, conv_type="personal", service=SERVICE):
    return {"type": "message", "id": f"act-{time.time_ns()}", "serviceUrl": service, "text": text,
            "from": {"id": "29:abc", "name": "Rahul Kumar", "aadObjectId": aad},
            "recipient": {"id": "28:bot"}, "conversation": {"id": "a:conv1", "conversationType": conv_type,
                                                            "tenantId": tenant},
            "channelData": {"tenant": {"id": tenant}}}


def teams_post(client, activity, token=None):
    return client.post("/api/channels/teams/messages", json=activity,
                       headers={"Authorization": f"Bearer {token or teams_token()}"})


def teams_texts(out):
    return [a["text"] for a in out["teams"] if a.get("type") == "message"]


def test_teams_rejects_forged_or_wrong_tokens(ch):
    c = ch["app"].test_client()
    act = teams_activity("hi")
    assert c.post("/api/channels/teams/messages", json=act).status_code == 401
    assert teams_post(c, act, teams_token(aud="someone-else")).status_code == 401
    assert teams_post(c, act, teams_token(iss="https://evil.example")).status_code == 401
    assert teams_post(c, act, teams_token(exp=-1000)).status_code == 401
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert teams_post(c, act, teams_token(key=other)).status_code == 401
    assert teams_post(c, act, teams_token(serviceurl="https://other.trafficmanager.net/")).status_code == 401
    evil = teams_activity("hi", service="https://attacker.example/")
    assert teams_post(c, evil, teams_token(serviceurl="https://attacker.example/")).status_code == 401
    assert ch["teams"] == []


def test_teams_other_tenant_and_group_chats(ch):
    c = ch["app"].test_client()
    assert teams_post(c, teams_activity("hi", tenant="other-tenant")).status_code == 403
    r = teams_post(c, teams_activity("hi", conv_type="channel"))
    assert r.status_code == 200 and "1:1 chat" in teams_texts(ch)[-1]


def test_teams_link_and_chat(ch):
    api = signed_in(ch["app"])
    c = ch["app"].test_client()
    assert teams_post(c, teams_activity(f"<at>Nova</at> link {link_code(api, 'teams')}")).status_code == 200
    assert "Linked to the Nova account" in teams_texts(ch)[-1]
    teams_post(c, teams_activity("calc 12*12"))
    assert "144" in teams_texts(ch)[-1]
    assert any(a.get("type") == "typing" for a in ch["teams"])
    link = next(x for x in api.get("/api/channels").json["channels"] if x["id"] == "teams")["links"][0]
    assert link["display"] == "Rahul Kumar"


def test_teams_welcome_when_installed(ch):
    act = {"type": "conversationUpdate", "serviceUrl": SERVICE, "recipient": {"id": "28:bot"},
           "membersAdded": [{"id": "28:bot"}], "conversation": {"id": "a:conv2", "conversationType": "personal"},
           "channelData": {"tenant": {"id": TENANT}}}
    assert teams_post(ch["app"].test_client(), act).status_code == 200
    assert "link your Nova account" in teams_texts(ch)[-1]


# ------------------------------------------------------------------ email
def make_email(frm, body, subject="Question", authres="mx.corp.test; dkim=pass header.d=corp.test; dmarc=pass "
               "header.from=corp.test", mid=None, in_reply_to=None, extra=None):
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "nova@corp.test"
    m["Subject"] = subject
    m["Message-ID"] = mid or f"<{time.time_ns()}@corp.test>"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    if authres:
        m["Authentication-Results"] = authres
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(body)
    return m.as_bytes()


def test_email_trust_checks(ch):
    from nova.channels import email_channel as ec
    with ch["app"].app_context():
        assert ec.to_inbound(make_email("Staff <staff@corp.test>", "hi")) is not None
        # forged: no result, a result from another server, or a failing result
        assert ec.to_inbound(make_email("staff@corp.test", "hi", authres=None)) is None
        assert ec.to_inbound(make_email("staff@corp.test", "hi", authres="evil.example; dmarc=pass")) is None
        assert ec.to_inbound(make_email("staff@corp.test", "hi", authres="mx.corp.test; dkim=fail; dmarc=fail")) is None
        # DKIM pass for a different domain doesn't vouch for the From address
        assert ec.to_inbound(make_email("ceo@corp.test", "hi",
                                        authres="mx.corp.test; dkim=pass header.d=attacker.example")) is None
        # automatic mail and our own address are ignored
        assert ec.to_inbound(make_email("staff@corp.test", "away", extra={"Auto-Submitted": "auto-replied"})) is None
        assert ec.to_inbound(make_email("mailer-daemon@corp.test", "bounce")) is None
        assert ec.to_inbound(make_email("nova@corp.test", "loop")) is None


def test_email_strips_quoted_text():
    from nova.channels.email_channel import strip_quoted
    text = "Thanks, one more thing.\n\nOn Mon, 20 Oct 2026 at 10:00, Nova <nova@corp.test> wrote:\n> old reply"
    assert strip_quoted(text) == "Thanks, one more thing."


def test_email_unknown_sender_gets_no_reply(ch):
    from nova.channels import core
    from nova.channels import email_channel as ec
    with ch["app"].app_context():
        core.handle(ec.to_inbound(make_email("stranger@corp.test", "hello")))
    assert ch["email"] == [] and STATE["calls"] == 0


def test_email_link_reply_threading_and_no_sensitive_tools(ch):
    from nova.channels import core
    from nova.channels import email_channel as ec
    api = signed_in(ch["app"])
    code = link_code(api, "email")
    with ch["app"].app_context():
        core.handle(ec.to_inbound(make_email("staff@corp.test", f"link {code}")))
        assert "Linked" in ch["email"][-1]["body"]
        core.handle(ec.to_inbound(make_email("staff@corp.test", "What is the **plan**?", subject="Plan", mid="<q1@corp.test>")))
        first = ch["email"][-1]
        assert first["subject"] == "Re: Plan" and first["headers"]["In-Reply-To"] == "<q1@corp.test>"
        assert first["headers"]["Auto-Submitted"] == "auto-replied" and "**" not in first["body"]
        tools_offered = {t["function"]["name"] for t in STATE["last_request"].get("tools") or []}
        assert "send_email" not in tools_offered
        # replying to Nova's email continues the same conversation
        core.handle(ec.to_inbound(make_email("staff@corp.test", "And the budget?", subject="Re: Plan",
                                             in_reply_to=first["headers"]["Message-ID"])))
    convs = [cv for cv in api.get("/api/conversations").json if cv["title"].startswith("Email")]
    assert len(convs) == 1
    msgs = api.get(f"/api/conversations/{convs[0]['id']}").json["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]


def test_email_poller_lease_is_exclusive(ch):
    from nova.channels import email_channel as ec
    with ch["app"].app_context():
        assert ec._lease("worker-a") is True
        assert ec._lease("worker-b") is False
        assert ec._lease("worker-a") is True


def test_admin_channel_status(ch):
    admin = signed_in(ch["app"], "admin@corp.test", "Admin")
    r = admin.get("/api/admin/channels")
    assert r.status_code == 200
    by = {x["id"]: x for x in r.json["channels"]}
    assert by["whatsapp"]["enabled"] and by["whatsapp"]["endpoint"].endswith("/api/channels/whatsapp/webhook")
    assert by["email"]["endpoint"] == "nova@corp.test"
    assert signed_in(ch["app"], "u2@corp.test").get("/api/admin/channels").status_code == 403


def test_config_requires_complete_channel_settings(make_app):
    from nova.config import ConfigError
    with pytest.raises(ConfigError):
        make_app(APP_ENV="production", SECRET_KEY="x" * 40, APP_URL="https://nova.example", ALLOWED_ORIGINS="https://nova.example",
                 WHATSAPP_ACCESS_TOKEN="only-this")
    with pytest.raises(ConfigError):
        make_app(EMAIL_CHANNEL_IMAP_HOST="imap.x", EMAIL_CHANNEL_USER="a@x", EMAIL_CHANNEL_PASSWORD="p")  # no SMTP/authserv
