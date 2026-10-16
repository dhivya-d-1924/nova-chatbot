"""Self-service account management: profile, password, sessions, MFA, synced settings, export and deletion."""
import json
from datetime import datetime, timezone

from flask import Blueprint, Response, g, jsonify, make_response, request
from sqlalchemy import delete, select, update

from . import db, security
from .chat import delete_messages
from .errors import ApiError
from .security import audit, auth, enforce_rate
from .users import DEFAULT_SETTINGS, LANG_PATTERN, admin_count, user_dict, user_settings
from .validation import Bool, Str, parse

bp = Blueprint("account", __name__, url_prefix="/api")


def _reload_user():
    with db.engine.connect() as c:
        return c.execute(select(db.users).where(db.users.c.id == g.user.id)).first()


def _require_password(pw):
    if not security.verify_password(g.user.password_hash, pw or ""):
        until = security.register_failed_login(g.user)
        audit("reauth.failed", target_type="user", target_id=g.user.id)
        if until:
            security.revoke_sessions(g.user.id)
            raise ApiError(429, security.lock_message(until), "locked")
        raise ApiError(403, "Your current password is incorrect.", "bad_password")


# ------------------------------------------------------------------ profile & settings
@bp.patch("/account/profile")
@auth()
def update_profile():
    d = parse(request, {"name": Str(min=1, max=100, required=True, label="Name")})
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == g.user.id).values(name=d["name"]))
    return jsonify(user=user_dict(_reload_user()))


@bp.get("/settings")
@auth()
def get_settings():
    return jsonify(user_settings(g.user))


@bp.put("/settings")
@auth()
def put_settings():
    d = parse(request, {"theme": Str(choices=("dark", "light", "system")), "voice_lang": Str(max=12, pattern=LANG_PATTERN),
                        "auto_read": Bool(), "agent_tools": Bool(), "agent_mode": Str(choices=("", "research", "coding", "data"))})
    merged = {**user_settings(g.user), **d}
    merged = {k: merged[k] for k in DEFAULT_SETTINGS}
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == g.user.id).values(settings=json.dumps(merged)))
    return jsonify(merged)


# ------------------------------------------------------------------ password & sessions
@bp.post("/account/password")
@auth()
def change_password():
    d = parse(request, {"current_password": Str(max=200, strip=False, required=True, label="Current password"),
                        "new_password": Str(max=200, strip=False, required=True, label="New password")})
    _require_password(d["current_password"])
    security.check_password_policy(d["new_password"], g.user.email)
    if d["new_password"] == d["current_password"]:
        raise ApiError(400, "Choose a password you haven't used here before.", "same_password")
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == g.user.id).values(
            password_hash=security.hash_password(d["new_password"]), password_changed_at=db.now_us(), failed_logins=0))
        n = security.revoke_sessions(g.user.id, except_id=g.session.id, conn=c)
    audit("password.changed", target_type="user", target_id=g.user.id, other_sessions_revoked=n)
    return jsonify(ok=True, message="Password changed. You've been signed out on your other devices.")


@bp.get("/account/sessions")
@auth()
def list_sessions():
    with db.engine.connect() as c:
        rows = c.execute(select(db.sessions).where(db.sessions.c.user_id == g.user.id)
                         .order_by(db.sessions.c.last_seen.desc())).all()
    return jsonify([{"id": r.id[:16], "created_at": r.created_at, "last_seen": r.last_seen, "ip": r.ip,
                     "user_agent": r.user_agent, "current": r.id == g.session.id} for r in rows])


@bp.delete("/account/sessions/<sid>")
@auth()
def revoke_session(sid):
    if len(sid) != 16:
        raise ApiError(404, "Session not found.", "not_found")
    with db.engine.begin() as c:
        rows = [r.id for r in c.execute(select(db.sessions.c.id).where(db.sessions.c.user_id == g.user.id))
                if r.id.startswith(sid)]
        if not rows:
            raise ApiError(404, "Session not found.", "not_found")
        c.execute(delete(db.sessions).where(db.sessions.c.id.in_(rows), db.sessions.c.user_id == g.user.id))
    audit("session.revoked", target_type="session", target_id=sid)
    resp = make_response("", 204)
    if g.session.id in rows:
        security.clear_session_cookie(resp)
    return resp


@bp.post("/account/verify-email")
@auth()
def send_my_verification():
    from .auth_routes import send_verification
    if g.user.email_verified:
        return jsonify(ok=True, message="Your email is already verified.")
    enforce_rate("verify-email", 3, 3600, key=g.user.email)
    if not send_verification(g.user):
        raise ApiError(503, "We couldn't send the email right now. Try again later.", "mail_failed")
    return jsonify(ok=True, message="Verification link sent. Check your inbox.")


# ------------------------------------------------------------------ MFA (TOTP)
@bp.post("/account/mfa/setup")
@auth()
def mfa_setup():
    d = parse(request, {"password": Str(max=200, strip=False, required=True, label="Password")})
    _require_password(d["password"])
    if g.user.mfa_enabled:
        raise ApiError(409, "Two-step verification is already on.", "mfa_on")
    secret = security.new_totp_secret()
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == g.user.id).values(mfa_secret=security.encrypt(secret)))
    return jsonify(secret=secret, uri=security.totp_uri(secret, g.user.email))


@bp.post("/account/mfa/enable")
@auth()
def mfa_enable():
    d = parse(request, {"code": Str(max=10, required=True, label="Code")})
    u = _reload_user()
    if u.mfa_enabled or not u.mfa_secret:
        raise ApiError(400, "Start the setup again.", "mfa_setup_needed")
    step = security.verify_totp(security.decrypt(u.mfa_secret), d["code"])
    if step is None:
        raise ApiError(400, "That code isn't right. Check the time on your phone and try again.", "bad_code")
    codes, hashes = security.new_recovery_codes()
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == u.id).values(
            mfa_enabled=1, mfa_last_step=step, mfa_recovery=json.dumps(hashes)))
        security.revoke_sessions(u.id, except_id=g.session.id, conn=c)
    audit("mfa.enabled", target_type="user", target_id=u.id)
    return jsonify(ok=True, recovery_codes=codes, user=user_dict(_reload_user()))


@bp.post("/account/mfa/disable")
@auth()
def mfa_disable():
    d = parse(request, {"password": Str(max=200, strip=False, required=True, label="Password"),
                        "code": Str(max=40, required=True, label="Code")})
    _require_password(d["password"])
    u = _reload_user()
    if not u.mfa_enabled:
        raise ApiError(400, "Two-step verification is already off.", "mfa_off")
    ok = security.verify_totp(security.decrypt(u.mfa_secret), d["code"], u.mfa_last_step) is not None \
        or security.use_recovery_code(u.mfa_recovery, d["code"]) is not None
    if not ok:
        raise ApiError(400, "That code isn't right.", "bad_code")
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == u.id).values(
            mfa_enabled=0, mfa_secret=None, mfa_recovery=None, mfa_last_step=None))
    audit("mfa.disabled", target_type="user", target_id=u.id)
    return jsonify(ok=True, user=user_dict(_reload_user()))


# ------------------------------------------------------------------ privacy: export & delete
@bp.get("/account/export")
@auth()
def export_account():
    enforce_rate("export", 5, 3600, key=g.user.id)
    uid = g.user.id
    with db.engine.connect() as c:
        convs = c.execute(select(db.conversations).where(db.conversations.c.user_id == uid)
                          .order_by(db.conversations.c.created_at)).all()
        cids = [r.id for r in convs]
        msgs = c.execute(select(db.messages).where(db.messages.c.conversation_id.in_(cids))
                         .order_by(db.messages.c.created_at)).all() if cids else []
        docs = c.execute(select(db.documents).where(db.documents.c.user_id == uid)).all()
        mems = c.execute(select(db.memories).where(db.memories.c.user_id == uid)).all()
        reps = c.execute(select(db.reports).where(db.reports.c.user_id == uid)).all()
        fb = c.execute(select(db.feedback).where(db.feedback.c.user_id == uid)).all()
        atts = c.execute(select(db.attachments.c.id, db.attachments.c.message_id, db.attachments.c.mime, db.attachments.c.size,
                                db.attachments.c.created_at).where(db.attachments.c.user_id == uid)).all()
    by_conv = {}
    for m in msgs:
        by_conv.setdefault(m.conversation_id, []).append({"role": m.role, "content": m.content, "created_at": iso(m.created_at)})
    out = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {k: v for k, v in user_dict(g.user).items() if k != "id"},
        "conversations": [{"title": cv.title, "created_at": iso(cv.created_at), "pinned": bool(cv.pinned),
                           "archived": bool(cv.archived), "messages": by_conv.get(cv.id, [])} for cv in convs],
        "documents": [{"filename": d.filename, "kind": d.kind, "pages": d.pages, "size": d.size, "version": d.version,
                       "uploaded_at": iso(d.created_at)} for d in docs],
        "memories": [{"text": m.text, "created_at": iso(m.created_at)} for m in mems],
        "reports": [{"title": r.title, "content": r.content, "created_at": iso(r.created_at)} for r in reps],
        "feedback": [{"message_id": f.message_id, "rating": f.rating} for f in fb],
        "image_attachments": [{"id": a.id, "mime": a.mime, "size": a.size, "created_at": iso(a.created_at)} for a in atts],
    }
    audit("account.exported")
    return Response(json.dumps(out, indent=2, ensure_ascii=False), mimetype="application/json",
                    headers={"Content-Disposition": 'attachment; filename="nova-account-export.json"'})


def iso(us):
    return datetime.fromtimestamp(us / 1e6, timezone.utc).isoformat() if us else None


def delete_user_data(c, uid):
    """Remove everything that belongs to a user. Audit entries keep only the user id."""
    cids = [r.id for r in c.execute(select(db.conversations.c.id).where(db.conversations.c.user_id == uid))]
    if cids:
        mids = [r.id for r in c.execute(select(db.messages.c.id).where(db.messages.c.conversation_id.in_(cids)))]
        for i in range(0, len(mids), 500):
            delete_messages(c, mids[i:i + 500])
        c.execute(delete(db.pending_actions).where(db.pending_actions.c.conversation_id.in_(cids)))
        c.execute(delete(db.conversations).where(db.conversations.c.id.in_(cids)))
    for t in (db.doc_chunks, db.documents, db.memories, db.reports, db.attachments, db.feedback, db.pending_actions,
              db.handoffs):
        c.execute(delete(t).where(t.c.user_id == uid))
    c.execute(update(db.tool_log).where(db.tool_log.c.user_id == uid).values(user_id=None, args=None))
    c.execute(update(db.errors).where(db.errors.c.user_id == uid).values(user_id=None))
    c.execute(update(db.usage).where(db.usage.c.user_id == uid).values(user_id=None, conversation_id=None))
    c.execute(delete(db.sessions).where(db.sessions.c.user_id == uid))
    c.execute(delete(db.users).where(db.users.c.id == uid))


@bp.delete("/account")
@auth()
def delete_account():
    d = parse(request, {"password": Str(max=200, strip=False, required=True, label="Password"),
                        "confirm": Str(max=20, required=True, label="Confirmation")})
    if d["confirm"] != "DELETE":
        raise ApiError(400, "Type DELETE to confirm.", "confirm")
    _require_password(d["password"])
    with db.engine.begin() as c:
        if g.user.role == "admin" and admin_count(c) <= 1:
            raise ApiError(400, "You're the only administrator. Make someone else an admin first.", "last_admin")
        delete_user_data(c, g.user.id)
    audit("account.deleted", actor_id=g.user.id, target_type="user", target_id=g.user.id)
    return security.clear_session_cookie(make_response("", 204))


