"""HTTP endpoints for channels: account linking (signed-in users), admin status, and provider webhooks.

Webhooks are not cookie-authenticated; each one verifies the provider's own signature before reading the body.
"""
import json
import logging

from flask import Blueprint, Response, g, jsonify, request
from sqlalchemy import delete, func, select

from .. import db
from ..config import settings
from ..errors import ApiError
from ..security import audit, auth, enforce_rate
from . import core, teams, whatsapp

log = logging.getLogger("nova.channels")
bp = Blueprint("channels", __name__, url_prefix="/api")
WEBHOOK_PATHS = {"whatsapp": "/api/channels/whatsapp/webhook", "teams": "/api/channels/teams/messages"}


def _link_dict(r):
    return {"id": r.id, "channel": r.channel, "display": r.display, "created_at": r.created_at,
            "last_used_at": r.last_used_at}


@bp.get("/channels")
@auth()
def list_channels():
    with db.engine.connect() as c:
        links = c.execute(select(db.channel_links).where(db.channel_links.c.user_id == g.user.id)
                          .order_by(db.channel_links.c.created_at)).all()
    return jsonify(channels=[{
        "id": ch, "label": core.LABELS[ch], "enabled": core.enabled(ch), "send_to": core.address_hint(ch),
        "approvals": ch != "email", "links": [_link_dict(r) for r in links if r.channel == ch]} for ch in core.CHANNELS])


@bp.post("/channels/<channel>/link-code")
@auth()
def create_link_code(channel):
    if channel not in core.CHANNELS:
        raise ApiError(404, "Unknown app.", "not_found")
    if not core.enabled(channel):
        raise ApiError(409, f"{core.LABELS[channel]} isn't set up on this server yet. Ask your administrator.",
                       "channel_disabled")
    enforce_rate("link-code", 10, 3600, key=g.user.id)
    code, expires = core.new_link_code(g.user.id, channel)
    audit("channel.link_code_created", target_type="channel", target_id=channel)
    return jsonify(code=code, expires_at=expires, send_to=core.address_hint(channel),
                   message=f"link {code}", minutes=settings.CHANNEL_LINK_CODE_MINUTES)


@bp.delete("/channels/links/<lid>")
@auth()
def remove_link(lid):
    with db.engine.begin() as c:
        row = c.execute(select(db.channel_links).where(db.channel_links.c.id == lid,
                                                       db.channel_links.c.user_id == g.user.id)).first()
        if not row:
            raise ApiError(404, "Connection not found.", "not_found")
        c.execute(delete(db.channel_links).where(db.channel_links.c.id == lid))
    audit("channel.unlinked", target_type="channel", target_id=row.channel)
    return jsonify(ok=True)


@bp.get("/admin/channels")
@auth(admin=True)
def admin_channels():
    with db.engine.connect() as c:
        counts = dict(c.execute(select(db.channel_links.c.channel, func.count()).group_by(db.channel_links.c.channel)).all())
        users = dict(c.execute(select(db.channel_links.c.channel, func.count(func.distinct(db.channel_links.c.user_id)))
                               .group_by(db.channel_links.c.channel)).all())
    base = settings.APP_URL or request.host_url.rstrip("/")
    return jsonify(channels=[{
        "id": ch, "label": core.LABELS[ch], "enabled": core.enabled(ch), "links": counts.get(ch, 0),
        "users": users.get(ch, 0),
        "endpoint": f"{base}{WEBHOOK_PATHS[ch]}" if ch in WEBHOOK_PATHS else settings.EMAIL_CHANNEL_ADDRESS or None}
        for ch in core.CHANNELS])


# ------------------------------------------------------------------ WhatsApp Cloud API
@bp.get("/channels/whatsapp/webhook")
def whatsapp_verify():
    if not settings.WHATSAPP_ENABLED:
        raise ApiError(404, "Not found", "not_found")
    challenge = whatsapp.verify_subscription(request.args)
    if challenge is None:
        log.warning("WhatsApp: webhook verification failed (wrong verify token)")
        return Response("Forbidden", status=403, mimetype="text/plain")
    return Response(challenge, mimetype="text/plain")


@bp.post("/channels/whatsapp/webhook")
def whatsapp_receive():
    if not settings.WHATSAPP_ENABLED:
        raise ApiError(404, "Not found", "not_found")
    raw = request.get_data(cache=True)
    if not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        log.warning("WhatsApp: rejected a webhook with a bad signature")
        return Response("Bad signature", status=401, mimetype="text/plain")
    try:
        payload = json.loads(raw)
    except ValueError:
        return Response("Bad JSON", status=400, mimetype="text/plain")
    whatsapp.handle_payload(payload if isinstance(payload, dict) else {})
    return jsonify(ok=True)


# ------------------------------------------------------------------ Microsoft Teams (Azure Bot)
@bp.post("/channels/teams/messages")
def teams_receive():
    if not settings.TEAMS_ENABLED:
        raise ApiError(404, "Not found", "not_found")
    activity = request.get_json(silent=True)
    if not isinstance(activity, dict):
        return Response("Bad request", status=400, mimetype="text/plain")
    try:
        teams.verify_request(request.headers.get("Authorization", ""), activity)
    except teams.AuthError as e:
        log.warning("Teams: rejected request (%s)", e)
        return Response("Unauthorized", status=401, mimetype="text/plain")
    status = teams.handle_activity(activity)
    return jsonify({}), status
