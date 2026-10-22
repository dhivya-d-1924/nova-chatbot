"""Notifications: always stored in the app (bell icon); also delivered by email, WhatsApp or Teams when the user
chose those channels and linked them. Each notification has a dedupe key, so retries and several workers never
send the same alert twice. Outside channels wait until quiet hours are over."""
import json
import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import db
from ..config import settings
from . import common

log = logging.getLogger("nova.workspace.notify")


def in_quiet_hours(p, now=None):
    tz = common.tz_of(p)
    t = (now or common.now_utc()).astimezone(tz).strftime("%H:%M")
    s, e = p["quiet_hours"]["start"], p["quiet_hours"]["end"]
    if s == e:
        return False
    return (s <= t < e) if s < e else (t >= s or t < e)


def create(uid, kind, title, body, dedupe_key, urgent=False):
    """Store and deliver. Returns the id, or None when this exact notification already exists."""
    p = common.prefs(uid)
    channels = [c for c in p["notify_channels"] if c != "app"]
    nid = str(uuid.uuid4())
    try:
        with db.engine.begin() as c:
            c.execute(db.notifications.insert().values(
                id=nid, user_id=uid, kind=kind, title=title[:300], body=(body or "")[:20000], dedupe_key=dedupe_key[:200],
                pending_channels=json.dumps(channels), delivered=json.dumps({"app": "ok"}), created_at=db.now_us()))
    except IntegrityError:
        return None
    if channels and (urgent or not in_quiet_hours(p)) and not p["paused"]:
        deliver(uid, nid)
    return nid


def deliver(uid, nid):
    with db.engine.connect() as c:
        n = c.execute(select(db.notifications).where(db.notifications.c.id == nid, db.notifications.c.user_id == uid)).first()
        user = c.execute(select(db.users.c.email, db.users.c.disabled).where(db.users.c.id == uid)).first()
    if not n or not user or user.disabled:
        return
    pending = json.loads(n.pending_channels or "[]")
    done = json.loads(n.delivered or "{}")
    for ch in list(pending):
        try:
            done[ch] = _send(uid, ch, user.email, n.title, n.body or "")
        except Exception as e:  # noqa: BLE001
            log.warning("notification via %s failed: %s", ch, e)
            done[ch] = f"failed: {str(e)[:120]}"
        pending.remove(ch)
    with db.engine.begin() as c:
        c.execute(update(db.notifications).where(db.notifications.c.id == nid).values(
            pending_channels=json.dumps(pending), delivered=json.dumps(done)))


def deliver_pending(uid):
    """Called by the scheduler: send what waited for quiet hours to end."""
    p = common.prefs(uid)
    if in_quiet_hours(p) or p["paused"]:
        return 0
    with db.engine.connect() as c:
        rows = c.execute(select(db.notifications.c.id).where(db.notifications.c.user_id == uid,
                                                            db.notifications.c.pending_channels.isnot(None),
                                                            db.notifications.c.pending_channels != "[]")
                         .limit(20)).all()
    for r in rows:
        deliver(uid, r.id)
    return len(rows)


def _send(uid, channel, email, title, body):
    text = f"{title}\n\n{body}".strip()
    if channel == "email":
        if not settings.SMTP_HOST:
            return "skipped: email not configured"
        from ..mail import smtp_send
        smtp_send(email, f"Nova: {title}", body or title, headers={"Auto-Submitted": "auto-generated"})
        return "ok"
    from ..channels import core
    with db.engine.connect() as c:
        link = c.execute(select(db.channel_links).where(db.channel_links.c.user_id == uid, db.channel_links.c.channel == channel)
                         .order_by(db.channel_links.c.last_used_at.desc())).first()
    if not link or not core.enabled(channel):
        return "skipped: not linked"
    if channel == "whatsapp":
        from ..channels import whatsapp
        whatsapp.send_text(link.external_id, core.to_whatsapp(text))
        return "ok"
    if channel == "teams":
        from ..channels import teams
        meta = json.loads(link.meta or "{}")
        if not meta.get("service_url") or not meta.get("conversation_id"):
            return "skipped: send Nova a Teams message first"
        teams.send_text(meta["service_url"], meta["conversation_id"], None, text)
        return "ok"
    return "skipped"


def listing(uid, unread_only=False, limit=50):
    q = select(db.notifications).where(db.notifications.c.user_id == uid)
    if unread_only:
        q = q.where(db.notifications.c.read_at.is_(None))
    with db.engine.connect() as c:
        rows = c.execute(q.order_by(db.notifications.c.created_at.desc()).limit(limit)).all()
        unread = c.execute(select(db.notifications.c.id).where(db.notifications.c.user_id == uid,
                                                               db.notifications.c.read_at.is_(None))).all()
    return {"unread": len(unread), "items": [{"id": r.id, "kind": r.kind, "title": r.title, "body": r.body,
                                             "created_at": r.created_at, "read": bool(r.read_at),
                                             "delivered": json.loads(r.delivered or "{}")} for r in rows]}


def mark_read(uid, nid=None):
    q = update(db.notifications).where(db.notifications.c.user_id == uid, db.notifications.c.read_at.is_(None))
    if nid:
        q = q.where(db.notifications.c.id == nid)
    with db.engine.begin() as c:
        return c.execute(q.values(read_at=db.now_us())).rowcount

