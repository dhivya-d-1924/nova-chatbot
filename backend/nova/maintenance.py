"""Housekeeping: expired sessions/approvals, unsent images, and retention limits for logs and chats."""
import logging
import threading
import time

from sqlalchemy import delete, select, update

from . import db
from .config import settings

log = logging.getLogger("nova.maintenance")
_lock = threading.Lock()
_last_run = 0.0
DAY_US = 86400 * 10**6


def run_maintenance():
    now = db.now_us()
    out = {}
    with db.engine.begin() as c:
        out["sessions"] = c.execute(delete(db.sessions).where(db.sessions.c.expires_at < now)).rowcount
        out["approvals"] = c.execute(update(db.pending_actions).where(
            db.pending_actions.c.status == "open", db.pending_actions.c.expires_at < now).values(status="expired")).rowcount
        out["unsent_images"] = c.execute(delete(db.attachments).where(
            db.attachments.c.message_id.is_(None), db.attachments.c.created_at < now - DAY_US)).rowcount
        cutoff = now - settings.LOG_RETENTION_DAYS * DAY_US
        for name, t in (("errors", db.errors), ("tool_log", db.tool_log), ("audit_log", db.audit_log), ("usage", db.usage)):
            out[name] = c.execute(delete(t).where(t.c.created_at < cutoff)).rowcount
        out["eval_runs"] = c.execute(delete(db.eval_runs).where(db.eval_runs.c.created_at < cutoff)).rowcount
        out["link_codes"] = c.execute(delete(db.channel_link_codes).where(db.channel_link_codes.c.expires_at < now)).rowcount
        out["channel_events"] = c.execute(delete(db.channel_events).where(
            db.channel_events.c.created_at < now - 7 * DAY_US)).rowcount
    if settings.CHAT_RETENTION_DAYS:
        from .chat import delete_messages
        cutoff = now - settings.CHAT_RETENTION_DAYS * DAY_US
        with db.engine.begin() as c:
            old = [r.id for r in c.execute(select(db.conversations.c.id).where(db.conversations.c.updated_at < cutoff,
                                                                               db.conversations.c.pinned == 0).limit(500))]
            for cid in old:
                mids = [r.id for r in c.execute(select(db.messages.c.id).where(db.messages.c.conversation_id == cid))]
                delete_messages(c, mids)
                c.execute(delete(db.pending_actions).where(db.pending_actions.c.conversation_id == cid))
                c.execute(update(db.handoffs).where(db.handoffs.c.conversation_id == cid).values(conversation_id=None))
                c.execute(delete(db.conversations).where(db.conversations.c.id == cid))
            out["conversations"] = len(old)
    return out


def tick():
    """Called from before_request: runs maintenance at most once an hour per process, in the background."""
    global _last_run
    if time.time() - _last_run < 3600:
        return
    with _lock:
        if time.time() - _last_run < 3600:
            return
        _last_run = time.time()

    def work():
        try:
            out = run_maintenance()
            if any(out.values()):
                log.info("maintenance: %s", out)
        except Exception:  # noqa: BLE001
            log.exception("maintenance failed")
    threading.Thread(target=work, daemon=True, name="maintenance").start()
