"""Approvals center: previewed actions waiting for the user (from the leave assistant, scheduled jobs or the
assistant), plus approvals still open inside chats. Nothing here runs without the user pressing Approve."""
import json
import uuid

from sqlalchemy import select, update

from .. import db, tools
from ..errors import ApiError
from . import actions, common

EDITABLE = {"to", "cc", "subject", "body", "text", "note", "title", "start", "end", "attendees", "message", "internal", "external"}


def create(uid, kind, title, payload, origin="assistant"):
    """Store a draft; identical pending drafts are not duplicated. Returns the draft id."""
    if kind not in actions.KINDS:
        raise ValueError(kind)
    blob = json.dumps(payload, sort_keys=True, default=str)
    with db.engine.begin() as c:
        same = c.execute(select(db.workspace_drafts.c.id).where(
            db.workspace_drafts.c.user_id == uid, db.workspace_drafts.c.kind == kind,
            db.workspace_drafts.c.status == "pending", db.workspace_drafts.c.payload == blob)).first()
        if same:
            return same.id
        did = str(uuid.uuid4())
        c.execute(db.workspace_drafts.insert().values(id=did, user_id=uid, kind=kind, title=title[:300], payload=blob,
                                                      status="pending", origin=origin, created_at=db.now_us()))
    return did


def _row(uid, did):
    with db.engine.connect() as c:
        r = c.execute(select(db.workspace_drafts).where(db.workspace_drafts.c.id == did,
                                                        db.workspace_drafts.c.user_id == uid)).first()
    if not r:
        raise ApiError(404, "Approval not found.", "not_found")
    return r


def as_dict(uid, r):
    payload = json.loads(r.payload)
    try:
        text = actions.preview(uid, r.kind, payload)
    except tools.ToolError as e:
        text = f"(Preview unavailable: {e})"
    except Exception:  # noqa: BLE001
        text = json.dumps(payload, indent=1)[:2000]
    return {"id": r.id, "kind": r.kind, "action": actions.KINDS.get(r.kind, r.kind), "title": r.title, "payload": payload,
            "preview": text, "status": r.status, "origin": r.origin, "result": r.result, "error": r.error,
            "created_at": r.created_at, "decided_at": r.decided_at}


def listing(uid, status="pending"):
    q = select(db.workspace_drafts).where(db.workspace_drafts.c.user_id == uid)
    if status != "all":
        q = q.where(db.workspace_drafts.c.status == status)
    with db.engine.connect() as c:
        rows = c.execute(q.order_by(db.workspace_drafts.c.created_at.desc()).limit(100)).all()
        chats = c.execute(select(db.pending_actions.c.id, db.pending_actions.c.conversation_id, db.pending_actions.c.created_at,
                                 db.pending_actions.c.expires_at, db.conversations.c.title)
                          .select_from(db.pending_actions.join(db.conversations, db.conversations.c.id == db.pending_actions.c.conversation_id))
                          .where(db.pending_actions.c.user_id == uid, db.pending_actions.c.status == "open",
                                 db.pending_actions.c.expires_at > db.now_us())).all() if status in ("pending", "all") else []
    return {"drafts": [as_dict(uid, r) for r in rows],
            "in_chats": [{"id": r.id, "conversation_id": r.conversation_id, "title": r.title, "created_at": r.created_at,
                          "expires_at": r.expires_at} for r in chats]}


def edit(uid, did, changes):
    r = _row(uid, did)
    if r.status not in ("pending", "failed"):
        raise ApiError(409, "Only pending or failed items can be edited.", "not_pending")
    payload = json.loads(r.payload)
    for k, v in (changes or {}).items():
        if k in EDITABLE and k in payload and isinstance(v, (str, list)):
            payload[k] = v if isinstance(v, list) else str(v)[:50000]
    with db.engine.begin() as c:
        c.execute(update(db.workspace_drafts).where(db.workspace_drafts.c.id == did).values(payload=json.dumps(payload, default=str)))
    return as_dict(uid, _row(uid, did))


def decide(uid, did, approve, retry=False):
    """Approve (runs it now and records the provider's confirmation) or discard. Single use.
    retry=True runs a failed item again; duplicate protection still refuses anything that already went out."""
    r = _row(uid, did)
    was = "failed" if retry else "pending"
    with db.engine.begin() as c:
        claimed = c.execute(update(db.workspace_drafts).where(db.workspace_drafts.c.id == did, db.workspace_drafts.c.user_id == uid,
                                                              db.workspace_drafts.c.status == was)
                            .values(status="running" if approve else "discarded", decided_at=db.now_us(), error=None)).rowcount
    if claimed != 1:
        raise ApiError(409, "Only failed items can be retried." if retry else "This item was already handled.", "already_decided")
    if not approve:
        common.log_activity(uid, r.kind, f"Discarded: {r.title}", "skipped")
        return as_dict(uid, _row(uid, did))
    try:
        msg, ref = actions.execute(uid, r.kind, json.loads(r.payload), origin=f"approved:{r.origin}")
        vals = {"status": "sent", "result": msg[:2000]}
    except tools.ToolError as e:
        vals = {"status": "failed", "error": str(e)[:2000]}
    with db.engine.begin() as c:
        c.execute(update(db.workspace_drafts).where(db.workspace_drafts.c.id == did).values(**vals))
    return as_dict(uid, _row(uid, did))
