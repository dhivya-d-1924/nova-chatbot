"""Shared engine for chatting with Nova from WhatsApp, Microsoft Teams and email.

Every connector verifies the provider first (WhatsApp HMAC signature, Teams Bot Framework JWT, email DMARC/DKIM
result from your own mail server) and then calls handle(Inbound(...)). From there the flow is the same:

1. Duplicate deliveries are dropped (providers retry webhooks).
2. "link 123456" binds the sender to the Nova account that created that code in Account -> Connected apps.
3. Unlinked senders only ever get instructions (email gets no reply at all, to avoid answering forged mail).
   Disabled accounts are refused.
4. Linked users get the same assistant as on the web: memory, tools and (with "docs ...") their documents. Sensitive
   tools pause and ask for YES/NO on WhatsApp and Teams; the email channel never offers sensitive tools.
5. Each channel conversation is a normal Nova chat, so it also appears in the web app's sidebar.
"""
import hashlib
import logging
import re
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from flask import current_app, g
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from .. import agent as ag
from .. import db, llm
from ..chat import build_messages, cited_sources, claim_pending, maybe_summarize, prepare_turn, save_message, save_pending
from ..config import settings
from ..errors import ApiError
from ..monitor import log_error
from ..security import audit, limiter
from ..users import user_settings

log = logging.getLogger("nova.channels")
CHANNELS = ("whatsapp", "teams", "email")
LABELS = {"whatsapp": "WhatsApp", "teams": "Microsoft Teams", "email": "Email"}
STYLE = {
    "whatsapp": "The user is chatting with you on WhatsApp on a phone. Keep replies short (under 200 words) with plain "
                "sentences or short lists. Do not use tables or headings.",
    "teams": "The user is chatting with you in Microsoft Teams. Keep replies concise. Markdown lists and code blocks are "
             "fine; avoid wide tables.",
    "email": "The user emailed you. Write the reply as a clear, polite email body in plain text (no Markdown), without a "
             "subject line or signature.",
}
YES = {"yes", "y", "approve", "approved", "ok", "okay", "confirm", "send", "send it", "go ahead"}
NO = {"no", "n", "decline", "cancel", "stop", "don't", "dont", "do not send"}
DOCS_PREFIX = re.compile(r"^\s*(?:/docs?|docs?\s*:|docs?\s+)\s*", re.I)
LINK_CMD = re.compile(r"^(?:link|connect)\s*(?:nova[-\s]?)?(\d{6})$")
US = 10**6
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="channel")


@dataclass
class Inbound:
    channel: str
    sender: str                       # stable id on that channel (wa_id, Entra object id, lower-case email)
    text: str
    reply: callable                   # reply(text) sends a message back to the sender on the same channel
    event_id: str = ""                # provider message id, for de-duplication
    display: str = ""                 # what the user sees in Account -> Connected apps
    subject: str = ""                 # email only
    thread_ids: list = field(default_factory=list)  # email: Message-IDs this message replies to
    meta: str | None = None           # JSON stored on the link (e.g. Teams conversation reference)
    allow_approvals: bool = True      # False for email: no sensitive tools there
    typing: callable = None           # optional "typing..." indicator
    conversation_id: str | None = None  # set by the engine before reply() is called


def enabled(channel):
    return {"whatsapp": settings.WHATSAPP_ENABLED, "teams": settings.TEAMS_ENABLED,
            "email": settings.EMAIL_CHANNEL_ENABLED}.get(channel, False)


def address_hint(channel):
    """Where the user should send their message, shown in Account -> Connected apps."""
    if channel == "whatsapp":
        return settings.WHATSAPP_DISPLAY_NUMBER or "your company's Nova WhatsApp number"
    if channel == "teams":
        return f"the {settings.TEAMS_BOT_NAME} app in Microsoft Teams (1:1 chat)"
    return settings.EMAIL_CHANNEL_ADDRESS


# ------------------------------------------------------------------ dispatching
def submit(inbound):
    """Handle a message in the background so the webhook can answer the provider at once. Tests run it inline."""
    app = current_app._get_current_object()
    if settings.IS_TEST:
        _run(app, inbound)
    else:
        _pool.submit(_run, app, inbound)


def submit_call(fn):
    """Run a simple outbound call (e.g. a welcome message) in the background."""
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("channel send failed")
    if settings.IS_TEST:
        run()
    else:
        _pool.submit(run)


def _run(app, inbound):
    with app.app_context():
        try:
            handle(inbound)
        except Exception:  # noqa: BLE001
            log.exception("channel message failed (%s)", inbound.channel)


def _first_time(event_key):
    try:
        with db.engine.begin() as c:
            c.execute(insert(db.channel_events).values(id=event_key[:255], created_at=db.now_us()))
        return True
    except IntegrityError:
        return False


def handle(m):
    if m.event_id and not _first_time(f"{m.channel}:{m.event_id}"):
        return
    text = (m.text or "").strip()
    cmd = re.sub(r"\s+", " ", text.lower()).strip(" .!")
    code = LINK_CMD.match(cmd.replace(" ", "")) or LINK_CMD.match(cmd)
    if code:
        return _link(m, code.group(1))
    link = find_link(m.channel, m.sender)
    if not link:
        return _unknown(m)
    with db.engine.connect() as c:
        user = c.execute(select(db.users).where(db.users.c.id == link.user_id)).first()
    if not user or user.disabled:
        m.reply("Your Nova account is disabled. Contact your administrator.")
        return
    values = {"last_used_at": db.now_us()}
    if m.meta:
        values["meta"] = m.meta
    with db.engine.begin() as c:
        c.execute(update(db.channel_links).where(db.channel_links.c.id == link.id).values(**values))

    if cmd in ("help", "menu", "?", "/help", "commands"):
        m.reply(help_text(m.channel))
        return
    if cmd in ("unlink", "/unlink", "disconnect"):
        with db.engine.begin() as c:
            c.execute(delete(db.channel_links).where(db.channel_links.c.id == link.id))
        audit("channel.unlinked", actor_id=user.id, target_type="channel", target_id=m.channel, via="channel")
        m.reply("This account is no longer linked to Nova. You can link it again from Account > Connected apps.")
        return
    if cmd in ("new", "/new", "reset", "new chat", "/reset") and m.channel != "email":
        cid = _new_conversation(user.id, f"{LABELS[m.channel]} chat")
        with db.engine.begin() as c:
            c.execute(update(db.channel_links).where(db.channel_links.c.id == link.id).values(conversation_id=cid))
        m.reply("Started a new chat. What can I help with?")
        return

    pending = _open_pending(user.id, link.conversation_id) if m.allow_approvals and link.conversation_id else None
    if pending and (cmd in YES or cmd in NO):
        return _guarded(m, user, lambda: _resume(m, user, pending, cmd in YES))
    if pending:  # a new request replaces an unanswered approval
        with db.engine.begin() as c:
            c.execute(update(db.pending_actions).where(db.pending_actions.c.id == pending.id,
                                                       db.pending_actions.c.status == "open").values(status="cancelled"))
    if not text:
        m.reply("I can read text messages only. Please type your question.")
        return
    return _guarded(m, user, lambda: _turn(m, user, link))


def help_text(channel):
    lines = ["I'm Nova, your company assistant. Just ask a question.", "",
             "• docs <question>: answer from your uploaded documents",
             "• new: start a new chat" if channel != "email" else "• Reply to my email to continue the same chat; "
                                                                  "a new email starts a new chat",
             "• unlink: disconnect this account from Nova",
             "• help: show this message"]
    if channel != "email":
        lines.insert(4, "• When I ask for approval, reply YES or NO")
    return "\n".join(lines)


# ------------------------------------------------------------------ linking
def _code_id(channel, code):
    return hashlib.sha256(f"{channel}:{code}".encode()).hexdigest()


def new_link_code(uid, channel):
    """Create a one-time 6-digit code for this user and channel. Returns (code, expires_at)."""
    for _ in range(5):
        code = f"{secrets.randbelow(10**6):06d}"
        now = db.now_us()
        expires = now + settings.CHANNEL_LINK_CODE_MINUTES * 60 * US
        try:
            with db.engine.begin() as c:
                c.execute(delete(db.channel_link_codes).where(db.channel_link_codes.c.user_id == uid,
                                                              db.channel_link_codes.c.channel == channel))
                c.execute(delete(db.channel_link_codes).where(db.channel_link_codes.c.expires_at < now))
                c.execute(insert(db.channel_link_codes).values(id=_code_id(channel, code), user_id=uid, channel=channel,
                                                               expires_at=expires, created_at=now))
            return code, expires
        except IntegrityError:  # another user holds the same code right now; pick another
            continue
    raise ApiError(503, "Could not create a code. Try again.", "code_failed")


def find_link(channel, sender):
    with db.engine.connect() as c:
        return c.execute(select(db.channel_links).where(db.channel_links.c.channel == channel,
                                                        db.channel_links.c.external_id == sender)).first()


def _link(m, code):
    # brute-force protection: 5 tries per sender per 15 minutes, and a ceiling per channel
    if not limiter.hit(f"link:{m.channel}:{m.sender}", 5, 900) or not limiter.hit(f"link-all:{m.channel}", 120, 60):
        if m.channel != "email":
            m.reply("Too many linking attempts. Wait 15 minutes and try again.")
        return
    now = db.now_us()
    with db.engine.begin() as c:
        row = c.execute(select(db.channel_link_codes).where(db.channel_link_codes.c.id == _code_id(m.channel, code),
                                                            db.channel_link_codes.c.channel == m.channel)).first()
        if not row or row.expires_at < now:
            m.reply("That code is wrong or has expired. Create a new one in Nova: Account > Connected apps.")
            return
        c.execute(delete(db.channel_link_codes).where(db.channel_link_codes.c.id == row.id))  # single use
        user = c.execute(select(db.users).where(db.users.c.id == row.user_id)).first()
        existing = c.execute(select(db.channel_links).where(db.channel_links.c.channel == m.channel,
                                                            db.channel_links.c.external_id == m.sender)).first()
        if not user or user.disabled:
            m.reply("That Nova account can't be linked. Contact your administrator.")
            return
        if existing and existing.user_id != user.id:
            m.reply(f"This {LABELS[m.channel]} account is already linked to another Nova user. "
                    "They need to unlink it first (send: unlink).")
            return
        if existing:
            c.execute(update(db.channel_links).where(db.channel_links.c.id == existing.id).values(
                display=m.display or existing.display, meta=m.meta or existing.meta, last_used_at=now))
        else:
            c.execute(insert(db.channel_links).values(id=str(uuid.uuid4()), user_id=user.id, channel=m.channel,
                                                      external_id=m.sender, display=(m.display or m.sender)[:255],
                                                      meta=m.meta, created_at=now, last_used_at=now))
    audit("channel.linked", actor_id=user.id, target_type="channel", target_id=m.channel)
    m.reply(f"✅ Linked to the Nova account {mask_email(user.email)}. Ask me anything, or send HELP to see what I can do.")


def _unknown(m):
    if m.channel == "email":
        log.info("email channel: ignored message from an unlinked address")
        return  # never answer unknown senders by email (backscatter, forged From addresses)
    if limiter.hit(f"unknown:{m.channel}:{m.sender}", 1, 600):
        m.reply("Hi! I'm Nova, your company's AI assistant. To use me here, link your account first: open Nova, go to "
                f"Account > Connected apps > {LABELS[m.channel]} > Link, then send me the code like this: link 123456")


def mask_email(email):
    name, _, domain = (email or "").partition("@")
    return f"{name[:1]}•••@{domain}" if domain else "•••"


def mask_phone(digits):
    d = re.sub(r"\D", "", digits or "")
    return f"+{d[:2]} ••••• {d[-4:]}" if len(d) > 6 else "•••"


# ------------------------------------------------------------------ running a turn
def _guarded(m, user, work):
    """Rate limit, concurrency slot and quota checks shared by new questions and approvals."""
    if not limiter.hit(f"channel-user:{user.id}", settings.CHANNEL_RATE_LIMIT, 60):
        m.reply("You're sending messages very quickly. Wait a minute and try again.")
        return
    key = f"streams:{user.id}"
    if not limiter.acquire(key, settings.MAX_CONCURRENT_STREAMS, settings.AGENT_TIMEOUT + 60):
        m.reply("I'm still working on your previous message. I'll answer that first.")
        return
    try:
        llm.client()
        llm.check_quota(user.id)
        g.user = user  # the chat services read the current user from g, exactly as for a web request
        if m.typing:
            try:
                m.typing()
            except Exception:  # noqa: BLE001
                log.debug("typing indicator failed", exc_info=True)
        work()
    except ApiError as e:
        m.reply(e.message)
    except Exception as e:  # noqa: BLE001
        msg = llm.friendly_error(e)
        log_error(user.id, f"{LABELS[m.channel]}: {msg}")
        log.warning("channel turn failed: %s", msg, exc_info=True)
        m.reply(f"Sorry, I couldn't answer that: {msg}")
    finally:
        limiter.release(key)


def _ctx(cid, user):
    return {"uid": user.id, "role": user.role, "name": user.name, "email": user.email, "cid": cid,
            "memory": bool(user.memory_enabled), "cancel": threading.Event()}


def _defs(ctx, m, agent=None):
    defs = ag.tool_defs(ctx, agent)
    if not m.allow_approvals:  # email: never offer actions that need approval
        defs = {n: t for n, t in defs.items() if not t.get("sensitive")}
    return defs


def _turn(m, user, link):
    text = m.text.strip()
    use_docs = settings.CHANNEL_DOCUMENTS == "always"
    if DOCS_PREFIX.match(text) and settings.CHANNEL_DOCUMENTS != "off":
        text, use_docs = DOCS_PREFIX.sub("", text, count=1).strip() or text, True
    cid = _conversation_for(m, user, link)
    m.conversation_id = cid
    cid, history, conv, _ = prepare_turn({"conversation_id": cid, "message": text})
    ctx = _ctx(cid, user)
    summary = maybe_summarize(conv, history, ctx)
    doc_ids = _doc_ids(user.id) if use_docs else []
    if use_docs and not doc_ids:
        save_message(cid, "assistant", "You haven't uploaded any documents yet.")
        m.reply("You haven't uploaded any documents yet. Upload them in the Nova web app, then ask again with: docs <question>")
        return
    llm_messages, sources = build_messages(history, user.id, doc_ids, STYLE[m.channel], summary)
    if user_settings(user)["agent_tools"]:
        defs = _defs(ctx, m)
        if defs:
            msgs = ag.agent_system(llm_messages, ctx, defs)
            steps = []
            result = ag.run_to_end(ag.agent_events(ctx, msgs, steps, defs))
            return _finish(m, user, cid, result, steps, msgs, sources)
    reply = llm.complete(llm_messages, ctx).content or ""
    _finish(m, user, cid, {"text": reply}, [], None, sources)


def _resume(m, user, pending, approve):
    p, state = claim_pending(user.id, pending.id, approve)
    m.conversation_id = p.conversation_id
    audit("agent.action_" + ("approved" if approve else "declined"), actor_id=user.id, target_type="tool",
          target_id=state["queue"][0]["name"], via=m.channel)
    ctx = _ctx(p.conversation_id, user)
    defs = _defs(ctx, m, state.get("agent") or None)  # permissions re-checked now, never taken from stored state
    result = ag.run_to_end(ag.agent_events(ctx, state["msgs"], state["steps"], defs, state["queue"]))
    _finish(m, user, p.conversation_id, result, state["steps"], state["msgs"], state.get("sources", []))


def _finish(m, user, cid, result, steps, msgs, sources):
    if result.get("paused"):
        save_pending(user.id, cid, steps, msgs, result["queue"], sources, "")
        step = next((s for s in steps if s["status"] == "awaiting"), {})
        m.reply(approval_text(step))
        return
    text = (result.get("text") or "").strip()
    if not text:
        text = next((s["summary"] for s in reversed(steps) if s.get("summary")), "") or \
            "I couldn't produce a reply. Please try again."
    saved = save_message(cid, "assistant", text, cited_sources(text, sources), steps)
    m.reply(format_reply(m.channel, text, saved["sources"]))


def approval_text(step):
    args = step.get("args") or {}
    lines = ["I need your approval before I continue:", "", step.get("label") or "Run an action"]
    if args.get("subject"):
        lines.append(f"Subject: {args['subject']}")
    if args.get("body"):
        body = args["body"]
        lines += ["", body[:600] + ("…" if len(body) > 600 else "")]
    lines += ["", f"Reply YES to approve or NO to cancel. This request expires in {settings.APPROVAL_TTL_MINUTES} minutes."]
    return "\n".join(lines)


def _open_pending(uid, cid):
    with db.engine.connect() as c:
        return c.execute(select(db.pending_actions).where(
            db.pending_actions.c.user_id == uid, db.pending_actions.c.conversation_id == cid,
            db.pending_actions.c.status == "open", db.pending_actions.c.expires_at > db.now_us())
            .order_by(db.pending_actions.c.created_at.desc())).first()


def _doc_ids(uid):
    with db.engine.connect() as c:
        return [r.id for r in c.execute(select(db.documents.c.id).where(db.documents.c.user_id == uid)
                                        .order_by(db.documents.c.created_at.desc()).limit(50))]


# ------------------------------------------------------------------ conversations
def _new_conversation(uid, title):
    cid, now = str(uuid.uuid4()), db.now_us()
    with db.engine.begin() as c:
        c.execute(insert(db.conversations).values(id=cid, title=title[:200], user_id=uid, created_at=now, updated_at=now))
    return cid


def _owned(c, cid, uid):
    return cid and c.execute(select(db.conversations.c.id).where(db.conversations.c.id == cid,
                                                                 db.conversations.c.user_id == uid)).first()


def _conversation_for(m, user, link):
    """WhatsApp/Teams: one rolling chat per linked account ("new" starts another). Email: one chat per thread."""
    if m.channel == "email":
        with db.engine.connect() as c:
            for ref in m.thread_ids:
                row = c.execute(select(db.channel_threads).where(db.channel_threads.c.id == f"email:{ref}"[:255],
                                                                 db.channel_threads.c.user_id == user.id)).first()
                if row and _owned(c, row.conversation_id, user.id):
                    cid = row.conversation_id
                    break
            else:
                cid = None
        cid = cid or _new_conversation(user.id, f"Email: {m.subject or 'no subject'}")
        if m.event_id:
            remember_thread(user.id, m.event_id, cid)
        return cid
    with db.engine.connect() as c:
        if _owned(c, link.conversation_id, user.id):
            return link.conversation_id
    cid = _new_conversation(user.id, f"{LABELS[m.channel]} chat")
    with db.engine.begin() as c:
        c.execute(update(db.channel_links).where(db.channel_links.c.id == link.id).values(conversation_id=cid))
    return cid


def remember_thread(uid, message_id, cid):
    try:
        with db.engine.begin() as c:
            c.execute(insert(db.channel_threads).values(id=f"email:{message_id}"[:255], user_id=uid, conversation_id=cid,
                                                        created_at=db.now_us()))
    except IntegrityError:
        pass


# ------------------------------------------------------------------ formatting
def _source_footer(sources):
    if not sources:
        return ""
    rows = [f"[{s['n']}] {s['filename']}, {'page' if s['kind'] in ('pdf', 'image') else 'section'} {s['page']}"
            for s in sources]
    return "\n\nSources:\n" + "\n".join(rows)


def to_whatsapp(text):
    """Markdown -> WhatsApp formatting (*bold*, _italic_, ```code```)."""
    t = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t)
    t = re.sub(r"__(.+?)__", r"_\1_", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", t)
    return t


def to_plain(text):
    t = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda mm: mm.group(1) or mm.group(2), t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", t)
    t = re.sub(r"^```\w*\s*$", "", t, flags=re.M)
    return t


def format_reply(channel, text, sources=None):
    body = to_whatsapp(text) if channel == "whatsapp" else to_plain(text) if channel == "email" else text
    return body + _source_footer(sources)


def chunks(text, size):
    """Split a long reply on paragraph or line boundaries so each part fits the provider's limit."""
    out, rest = [], text
    while len(rest) > size:
        cut = rest.rfind("\n\n", 0, size)
        if cut < size // 3:
            cut = rest.rfind("\n", 0, size)
        if cut < size // 3:
            cut = rest.rfind(" ", 0, size)
        if cut <= 0:
            cut = size
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return out
