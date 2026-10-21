"""Conversations: list/search, create, rename/pin/archive, export, clear, delete, and message search."""
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from flask import Blueprint, Response, g, jsonify, request
from sqlalchemy import and_, delete, exists, func, insert, or_, select, update

from . import db
from .chat import conv_dict, delete_messages, get_conv, msg_dict
from .errors import ApiError
from .security import audit, auth
from .validation import Bool, Str, parse, query_str

bp = Blueprint("conversations", __name__, url_prefix="/api")


def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@bp.get("/conversations")
@auth()
def list_conversations():
    q = query_str("q")
    archived = request.args.get("archived") == "1"
    try:
        limit = max(1, min(500, int(request.args.get("limit", 200))))
    except ValueError:
        raise ApiError(400, "limit must be a number.", "bad_request")
    stmt = select(db.conversations).where(db.conversations.c.user_id == g.user.id,
                                          db.conversations.c.archived == (1 if archived else 0))
    if q:
        pat = _like(q.lower())
        stmt = stmt.where(or_(func.lower(db.conversations.c.title).like(pat, escape="\\"),
                              exists().where(and_(db.messages.c.conversation_id == db.conversations.c.id,
                                                  func.lower(db.messages.c.content).like(pat, escape="\\")))))
    stmt = stmt.order_by(db.conversations.c.pinned.desc(), db.conversations.c.updated_at.desc()).limit(limit)
    with db.engine.connect() as c:
        rows = c.execute(stmt).all()
    return jsonify([conv_dict(r) for r in rows])


@bp.post("/conversations")
@auth()
def create_conversation():
    ts = db.now_us()
    row = {"id": str(uuid.uuid4()), "title": "New chat", "user_id": g.user.id, "created_at": ts, "updated_at": ts,
           "pinned": 0, "archived": 0}
    with db.engine.begin() as c:
        c.execute(insert(db.conversations).values(**row))
        conv = get_conv(c, row["id"])
    return jsonify(conv_dict(conv)), 201


def _conversation_payload(c, conv):
    rows = c.execute(select(db.messages).where(db.messages.c.conversation_id == conv.id)
                     .order_by(db.messages.c.created_at)).all()
    ids = [r.id for r in rows]
    ratings, images = {}, defaultdict(list)
    if ids:
        ratings = {r.message_id: r.rating for r in c.execute(
            select(db.feedback).where(db.feedback.c.user_id == g.user.id, db.feedback.c.message_id.in_(ids)))}
        for r in c.execute(select(db.attachments.c.id, db.attachments.c.mime, db.attachments.c.message_id)
                           .where(db.attachments.c.message_id.in_(ids), db.attachments.c.user_id == g.user.id)
                           .order_by(db.attachments.c.created_at)):
            images[r.message_id].append({"id": r.id, "mime": r.mime})
    subject = None
    if conv.subject_id:
        srow = c.execute(select(db.subjects.c.id, db.subjects.c.name).where(db.subjects.c.id == conv.subject_id,
                                                                            db.subjects.c.user_id == g.user.id)).first()
        subject = {"id": srow.id, "name": srow.name} if srow else None
    return {**conv_dict(conv), "subject": subject, "messages": [
        {**msg_dict(r), "rating": ratings.get(r.id, 0), "images": images.get(r.id, [])} for r in rows]}


@bp.get("/conversations/<cid>")
@auth()
def get_conversation(cid):
    with db.engine.connect() as c:
        return jsonify(_conversation_payload(c, get_conv(c, cid)))


@bp.patch("/conversations/<cid>")
@auth()
def update_conversation(cid):
    d = parse(request, {"title": Str(min=1, max=200, label="Title"), "pinned": Bool(), "archived": Bool()})
    if not d:
        raise ApiError(400, "Nothing to update.", "bad_request")
    values = {}
    if "title" in d:
        values["title"] = re.sub(r"\s+", " ", d["title"])
    if "pinned" in d:
        values["pinned"] = 1 if d["pinned"] else 0
    if "archived" in d:
        values["archived"] = 1 if d["archived"] else 0
        if d["archived"]:
            values["pinned"] = 0
    with db.engine.begin() as c:
        get_conv(c, cid)
        c.execute(update(db.conversations).where(db.conversations.c.id == cid,
                                                 db.conversations.c.user_id == g.user.id).values(**values))
        conv = get_conv(c, cid)
    return jsonify(conv_dict(conv))


def _purge_messages(c, cid):
    mids = [r.id for r in c.execute(select(db.messages.c.id).where(db.messages.c.conversation_id == cid))]
    for i in range(0, len(mids), 500):
        delete_messages(c, mids[i:i + 500])
    c.execute(delete(db.pending_actions).where(db.pending_actions.c.conversation_id == cid))


@bp.delete("/conversations/<cid>")
@auth()
def delete_conversation(cid):
    with db.engine.begin() as c:
        get_conv(c, cid)
        _purge_messages(c, cid)
        c.execute(update(db.handoffs).where(db.handoffs.c.conversation_id == cid).values(conversation_id=None))
        c.execute(delete(db.conversations).where(db.conversations.c.id == cid, db.conversations.c.user_id == g.user.id))
    return "", 204


@bp.delete("/conversations/<cid>/messages")
@auth()
def clear_conversation(cid):
    with db.engine.begin() as c:
        get_conv(c, cid)
        _purge_messages(c, cid)
        c.execute(update(db.conversations).where(db.conversations.c.id == cid).values(
            title="New chat", summary=None, summary_upto=None))
    return "", 204


def _iso(us):
    return datetime.fromtimestamp(us / 1e6, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@bp.get("/conversations/<cid>/export")
@auth()
def export_conversation(cid):
    fmt = request.args.get("format", "md")
    if fmt not in ("md", "json"):
        raise ApiError(400, "format must be md or json.", "bad_request")
    with db.engine.connect() as c:
        data = _conversation_payload(c, get_conv(c, cid))
    name = re.sub(r"[^\w\- ]+", "", data["title"]).strip().replace(" ", "-")[:60] or "conversation"
    audit("conversation.exported", target_type="conversation", target_id=cid, format=fmt)
    if fmt == "json":
        body = json.dumps({"title": data["title"], "created_at": _iso(data["created_at"]),
                           "messages": [{"role": m["role"], "content": m["content"], "created_at": _iso(m["created_at"]),
                                         "sources": m["sources"]} for m in data["messages"]]}, indent=2, ensure_ascii=False)
        return Response(body, mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    lines = [f"# {data['title']}", "", f"_Exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_", ""]
    for m in data["messages"]:
        who = "You" if m["role"] == "user" else "Nova"
        lines += [f"### {who} · {_iso(m['created_at'])}", "", m["content"], ""]
        if m.get("sources"):
            lines += ["Sources: " + "; ".join(f"[{s['n']}] {s['filename']} p.{s['page']}" for s in m["sources"]), ""]
    return Response("\n".join(lines), mimetype="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@bp.get("/search")
@auth()
def search_messages():
    q = query_str("q")
    if len(q) < 2:
        return jsonify([])
    pat = _like(q.lower())
    with db.engine.connect() as c:
        rows = c.execute(
            select(db.messages.c.id, db.messages.c.content, db.messages.c.role, db.messages.c.created_at,
                   db.conversations.c.id.label("cid"), db.conversations.c.title)
            .select_from(db.messages.join(db.conversations, db.messages.c.conversation_id == db.conversations.c.id))
            .where(db.conversations.c.user_id == g.user.id, func.lower(db.messages.c.content).like(pat, escape="\\"))
            .order_by(db.messages.c.created_at.desc()).limit(30)).all()
    out = []
    for r in rows:
        i = r.content.lower().find(q.lower())
        start = max(0, i - 60)
        snippet = ("…" if start else "") + r.content[start:start + 180].replace("\n", " ") + ("…" if len(r.content) > start + 180 else "")
        out.append({"conversation_id": r.cid, "title": r.title, "message_id": r.id, "role": r.role,
                    "snippet": snippet, "created_at": r.created_at})
    return jsonify(out)
