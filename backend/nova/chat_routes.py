"""Chat endpoints: streaming (SSE), non-streaming, agent approvals, tools list and feedback."""
import json
import queue
import threading
import uuid

from flask import Blueprint, Response, g, jsonify, request, stream_with_context
from openai import APIStatusError
from sqlalchemy import delete, insert, select, update

from . import agent as ag
from . import db, llm
from .chat import build_messages, cited_sources, doc_ids_from, maybe_summarize, prepare_turn, save_message
from .config import settings
from .errors import ApiError
from .monitor import log_error
from .security import audit, auth, enforce_rate
from .validation import Bool, Int, Str, parse

bp = Blueprint("chat", __name__, url_prefix="/api")
HEARTBEAT_SECONDS = 15


def sse(payload):
    return f"data: {json.dumps(payload)}\n\n"


def agent_ctx(cid, user=None):
    u = user or g.user
    return {"uid": u.id, "role": u.role, "name": u.name, "email": u.email, "cid": cid,
            "memory": bool(u.memory_enabled), "cancel": threading.Event()}


def plain_events(msgs, ctx):
    """Plain streaming reply as agent-style events."""
    out = []
    gen = llm.stream(msgs, ctx, kind="chat")
    try:
        for piece in gen:
            if ctx["cancel"].is_set():
                break
            out.append(piece)
            yield {"type": "delta", "text": piece}
    finally:
        gen.close()  # stops reading from the provider when the user cancels
    return {"paused": False, "text": "".join(out)}


def pump(gen, ctx, acc):
    """Run an event generator in a worker thread so the response can send heartbeats during long tool or model
    calls (keeps proxies from closing the connection). Returns the generator's return value."""
    q = queue.Queue()
    cancel = ctx["cancel"]

    def worker():
        try:
            while True:
                if cancel.is_set():
                    gen.close()
                    q.put(("end", {"paused": False, "text": "".join(acc), "cancelled": True}))
                    return
                q.put(("item", next(gen)))
        except StopIteration as stop:
            q.put(("end", stop.value))
        except BaseException as e:  # noqa: BLE001
            q.put(("exc", e))

    threading.Thread(target=worker, daemon=True, name="chat-pump").start()
    while True:
        try:
            kind, val = q.get(timeout=HEARTBEAT_SECONDS)
        except queue.Empty:
            yield ": ping\n\n"
            continue
        if kind == "item":
            if val.get("type") == "delta":
                acc.append(val["text"])
            yield sse(val)
        elif kind == "end":
            return val
        else:
            raise val


def stream_response(ctx, gen, steps, sources, msgs=None, agent=None, release=None, user_msg=None):
    """SSE response: streams steps/text, then saves the reply, a pending approval, or a marked partial reply."""
    uid, cid = ctx["uid"], ctx["cid"]
    acc = []

    def generate():
        try:
            if user_msg:
                yield sse({"type": "user", "message": user_msg})  # the stored question's real id
            result = yield from pump(gen, ctx, acc)
            if result.get("paused"):
                msg = save_message(cid, "assistant", "Waiting for your approval to continue.", None, steps)
                pid = str(uuid.uuid4())
                now = db.now_us()
                with db.engine.begin() as c:
                    c.execute(insert(db.pending_actions).values(
                        id=pid, user_id=uid, conversation_id=cid, message_id=msg["id"], status="open", created_at=now,
                        expires_at=now + settings.APPROVAL_TTL_MINUTES * 60 * 10**6,
                        state=json.dumps({"msgs": msgs, "steps": steps, "queue": result["queue"], "sources": sources,
                                          "agent": agent}, default=str)))
                    for st in steps:
                        if st["status"] == "awaiting":
                            st["pending_id"] = pid
                            st["expires_at"] = now + settings.APPROVAL_TTL_MINUTES * 60 * 10**6
                    c.execute(update(db.messages).where(db.messages.c.id == msg["id"]).values(steps=json.dumps(steps)))
                msg["steps"] = steps
                yield sse({"type": "done", "message": msg})
                return
            text = result.get("text") or ""
            if not text.strip():
                if not steps:
                    yield sse({"type": "error", "message": "The AI returned an empty reply. Try again."})
                    return
                text = next((s["summary"] for s in reversed(steps) if s.get("summary")), "") or \
                    "I couldn't produce a reply. Please try again."
                yield sse({"type": "delta", "text": text})
            yield sse({"type": "done", "message": save_message(cid, "assistant", text, cited_sources(text, sources), steps)})
        except GeneratorExit:  # the user pressed Stop or the connection dropped: keep what was generated
            ctx["cancel"].set()
            text = "".join(acc)
            if text.strip() or steps:
                save_message(cid, "assistant", text or "Stopped.", cited_sources(text, sources), steps)
            raise
        except Exception as e:  # noqa: BLE001
            msg = llm.friendly_error(e)
            if agent is not None and isinstance(e, APIStatusError) and e.status_code == 400:
                msg += " If this keeps happening, turn off Agent tools: this model may not support them."
            log_error(uid, msg)
            text = "".join(acc)
            saved = None
            if text.strip() or steps:
                note = f"\n\n*(This reply was interrupted: {msg})*" if text.strip() else f"*(Stopped: {msg})*"
                saved = save_message(cid, "assistant", text + note, cited_sources(text, sources), steps)
            yield sse({"type": "error", "message": msg, "saved": saved})
        finally:
            if release:
                release()  # idempotent; call_on_close covers a response that never started

    resp = Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})
    if release:
        resp.call_on_close(release)
    return resp


def _slot(uid):
    """Take a concurrency slot now; the returned function releases it exactly once when the response closes."""
    from .security import limiter
    key = f"streams:{uid}"
    if not limiter.acquire(key, settings.MAX_CONCURRENT_STREAMS, settings.AGENT_TIMEOUT + 60):
        raise ApiError(429, "You already have replies in progress. Wait for them to finish or stop one.", "too_many_streams")
    done = threading.Event()

    def release():
        if not done.is_set():
            done.set()
            limiter.release(key)
    return release


CHAT_FIELDS = {"conversation_id": Str(max=64, required=True), "message": Str(max=200000, strip=False),
               "regenerate": Bool(), "edit_message_id": Str(max=64), "tools": Bool(), "agent": Str(max=20),
               "doc_ids": None, "attachment_ids": None}


def _validated_body():
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        raise ApiError(400, "Invalid request body.", "bad_request")
    parse(raw, {k: v for k, v in CHAT_FIELDS.items() if v is not None})
    return raw


@bp.post("/chat/stream")
@auth()
def chat_stream():
    """Server-Sent Events: {type: delta|step|done|error}; ': ping' comments keep the connection alive."""
    enforce_rate("chat-user", settings.RATE_LIMIT, key=g.user.id)
    data = _validated_body()
    llm.client()  # fail fast with a clear JSON error when no provider is configured
    llm.check_quota(g.user.id)
    release = _slot(g.user.id)
    try:
        cid, history, conv, user_msg = prepare_turn(data)
        ctx = agent_ctx(cid)
        agent = ag.resolve_agent(ctx, data.get("agent"))
        summary = maybe_summarize(conv, history, ctx)
        llm_messages, sources = build_messages(history, g.user.id, doc_ids_from(data, g.user.id),
                                               ag.AGENTS[agent]["prompt"] if agent else "", summary)
        if data.get("tools"):
            defs = ag.tool_defs(ctx, agent)
            if defs:
                msgs = ag.agent_system(llm_messages, ctx, defs)
                steps = []
                return stream_response(ctx, ag.agent_events(ctx, msgs, steps, defs), steps, sources, msgs, agent or "",
                                       release, user_msg)
        return stream_response(ctx, plain_events(llm_messages, ctx), [], sources, release=release, user_msg=user_msg)
    except Exception:
        release()
        raise


@bp.post("/chat")
@auth()
def chat():
    """Non-streaming endpoint: returns the full assistant reply as JSON."""
    enforce_rate("chat-user", settings.RATE_LIMIT, key=g.user.id)
    data = _validated_body()
    llm.client()
    llm.check_quota(g.user.id)
    release = _slot(g.user.id)
    try:
        cid, history, conv, _ = prepare_turn(data)
        ctx = agent_ctx(cid)
        summary = maybe_summarize(conv, history, ctx)
        llm_messages, sources = build_messages(history, g.user.id, doc_ids_from(data, g.user.id), "", summary)
        try:
            text = llm.complete(llm_messages, ctx).content or ""
        except Exception as e:  # noqa: BLE001
            msg = llm.friendly_error(e)
            log_error(g.user.id, msg)
            raise ApiError(502, msg, "provider_error")
        if not text.strip():
            raise ApiError(502, "The AI returned an empty reply. Try again.", "empty_reply")
        return jsonify(message=save_message(cid, "assistant", text, cited_sources(text, sources)))
    finally:
        release()


@bp.post("/chat/resume")
@auth()
def chat_resume():
    """Continue an agent run after the user approved or declined a sensitive action (single use, owner only)."""
    d = parse(request, {"pending_id": Str(max=64, required=True), "approve": Bool(default=False)})
    llm.client()
    llm.check_quota(g.user.id)
    now = db.now_us()
    with db.engine.begin() as c:
        p = c.execute(select(db.pending_actions).where(
            db.pending_actions.c.id == d["pending_id"], db.pending_actions.c.user_id == g.user.id,
            db.pending_actions.c.status == "open")).first()
        if not p:
            raise ApiError(404, "This approval request is no longer available.", "approval_gone")
        if p.expires_at and p.expires_at < now:
            c.execute(update(db.pending_actions).where(db.pending_actions.c.id == p.id).values(status="expired"))
            raise ApiError(410, "This approval request expired. Ask the assistant again.", "approval_expired")
        claimed = c.execute(update(db.pending_actions).where(db.pending_actions.c.id == p.id,
                                                             db.pending_actions.c.status == "open").values(status="done"))
        if claimed.rowcount != 1:  # someone (another tab) already used it
            raise ApiError(404, "This approval request is no longer available.", "approval_gone")
        c.execute(delete(db.messages).where(db.messages.c.id == p.message_id))
    state = json.loads(p.state)
    release = _slot(g.user.id)
    try:
        ctx = agent_ctx(p.conversation_id)
        queue_ = state["queue"]
        queue_[0]["decision"] = "approved" if d["approve"] else "declined"
        step = queue_[0]
        audit("agent.action_" + ("approved" if d["approve"] else "declined"), target_type="tool", target_id=step["name"])
        for st in state["steps"]:
            st.pop("pending_id", None)
            st.pop("expires_at", None)
        agent = state.get("agent") or None
        defs = ag.tool_defs(ctx, agent)  # permissions are re-checked now, not taken from the stored state
        return stream_response(ctx, ag.agent_events(ctx, state["msgs"], state["steps"], defs, queue_), state["steps"],
                               state.get("sources", []), state["msgs"], agent or "", release)
    except Exception:
        release()
        raise


@bp.get("/tools")
@auth()
def list_tools():
    defs = ag.tool_defs(agent_ctx(None))
    return jsonify(
        tools=[{"name": n, "label": t["label"], "sensitive": bool(t.get("sensitive"))} for n, t in defs.items()],
        agents=[{"id": i, "label": ag.AGENTS[i]["label"], "description": ag.AGENTS[i]["description"]}
                for i in ag.available_agents(defs)])


@bp.post("/messages/<mid>/feedback")
@auth()
def set_feedback(mid):
    d = parse(request, {"rating": Int(choices=(1, -1, 0), required=True, label="rating")})
    with db.engine.begin() as c:
        owned = c.execute(
            select(db.messages.c.id).select_from(db.messages.join(db.conversations,
                                                                  db.messages.c.conversation_id == db.conversations.c.id))
            .where(db.messages.c.id == mid, db.messages.c.role == "assistant", db.conversations.c.user_id == g.user.id)).first()
        if not owned:
            raise ApiError(404, "Message not found.", "not_found")
        c.execute(delete(db.feedback).where(db.feedback.c.message_id == mid, db.feedback.c.user_id == g.user.id))
        if d["rating"]:
            c.execute(insert(db.feedback).values(message_id=mid, user_id=g.user.id, rating=d["rating"], created_at=db.now_us()))
    return jsonify(rating=d["rating"])
