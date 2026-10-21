"""Email channel: Nova reads a dedicated mailbox over IMAP and answers by SMTP in the same thread.

Trust model: the From header of an email is easy to forge, so a message is only processed when YOUR receiving mail
server recorded that it passed DMARC (or DKIM for the From domain). That result is read from the top-most
Authentication-Results header written by the server named in EMAIL_CHANNEL_AUTHSERV_ID (for Gmail: mx.google.com;
for Microsoft 365, which writes no server name, use "first"). Unknown senders never get a reply, automatic mail
(bounces, out-of-office, lists) is ignored, and sensitive tools such as send_email are never offered by email.

One process at a time polls the mailbox (a lease in the database), so several web workers can run safely.
"""
import email
import hashlib
import html
import imaplib
import logging
import re
import ssl
import threading
import time
import uuid
from email import policy
from email.utils import make_msgid, parseaddr

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from .. import db
from ..config import settings
from ..mail import smtp_send
from . import core

log = logging.getLogger("nova.channels.email")
MAX_RAW = 2 * 1024 * 1024
MAX_PER_POLL = 25
AUTO_LOCALPARTS = ("mailer-daemon", "postmaster", "noreply", "no-reply", "do-not-reply", "donotreply", "bounce")
QUOTE_START = re.compile(r"^(On .{5,200} wrote:|-----\s*Original Message\s*-----|From: .+|_{10,}|Sent from my .+)$",
                         re.I | re.M)
_started = threading.Event()


# ------------------------------------------------------------------ checks
def is_automatic(msg, addr):
    local = addr.split("@", 1)[0]
    if any(local.startswith(p) for p in AUTO_LOCALPARTS):
        return True
    auto = str(msg.get("Auto-Submitted", "no")).strip().lower()
    if auto and auto != "no":
        return True
    if str(msg.get("Precedence", "")).strip().lower() in ("bulk", "junk", "list", "auto_reply"):
        return True
    return any(msg.get(h) for h in ("List-Id", "List-Unsubscribe", "X-Autoreply", "X-Autorespond"))


def authenticated(msg, from_domain):
    """True when the receiving server recorded DMARC pass, or DKIM pass for the From domain."""
    want = settings.EMAIL_CHANNEL_AUTHSERV_ID
    for header in msg.get_all("Authentication-Results") or []:
        value = str(header)
        head, _, rest = value.partition(";")
        server = (head.strip().split() or [""])[0].lower()
        if want != "first" and server != want:
            continue
        results = (rest if want != "first" or "=" not in head else value).lower()
        if re.search(r"\bdmarc=pass\b", results):
            return True
        for m in re.finditer(r"\bdkim=pass\b[^;]*?header\.(?:d|i)=@?([a-z0-9.-]+)", results):
            d = m.group(1).strip(".")
            if from_domain == d or from_domain.endswith("." + d):
                return True
        return False  # only the top-most header from our own server counts; anything below could be forged
    return False


# ------------------------------------------------------------------ parsing
def html_to_text(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def strip_quoted(text):
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith(">") or line.strip() == "--":
            break
        lines.append(line)
    body = "\n".join(lines)
    m = QUOTE_START.search(body)
    if m and m.start() > 0:
        body = body[:m.start()]
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def body_text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        text = part.get_payload(decode=True).decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        text = html_to_text(text)
    return strip_quoted(text)


def to_inbound(raw):
    """Parse one raw email. Returns an Inbound to handle, or None (with the reason logged) to skip it."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    _, addr = parseaddr(str(msg.get("From", "")))
    addr = addr.strip().lower()
    if "@" not in addr:
        return None
    if addr == settings.EMAIL_CHANNEL_ADDRESS or is_automatic(msg, addr):
        log.info("email channel: skipped automatic or own message")
        return None
    if not authenticated(msg, addr.rsplit("@", 1)[1]):
        log.warning("email channel: rejected a message that did not pass DMARC/DKIM (claimed sender domain %s)",
                    addr.rsplit("@", 1)[1])
        return None
    mid = str(msg.get("Message-ID", "")).strip().strip("<>")[:200] or hashlib.sha256(raw).hexdigest()
    refs = re.findall(r"<([^>]{1,200})>", f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}")
    subject = re.sub(r"\s+", " ", str(msg.get("Subject", ""))).strip()[:150]
    inbound = core.Inbound(channel="email", sender=addr, text=body_text(msg)[:8000], event_id=mid, display=addr,
                           subject=subject, thread_ids=list(dict.fromkeys(reversed(refs))), allow_approvals=False,
                           reply=lambda t: None)
    inbound.reply = lambda t: send_reply(inbound, mid, refs, t)
    return inbound


def send_reply(inbound, mid, refs, text):
    subject = inbound.subject or "Your question"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    domain = settings.EMAIL_CHANNEL_ADDRESS.rsplit("@", 1)[-1] or None
    new_id = make_msgid(domain=domain)
    chain = " ".join(f"<{r}>" for r in [*refs[-10:], mid])
    smtp_send(inbound.sender, subject, text, from_addr=f"{settings.EMAIL_CHANNEL_NAME} <{settings.EMAIL_CHANNEL_ADDRESS}>",
              headers={"Message-ID": new_id, "In-Reply-To": f"<{mid}>", "References": chain,
                       "Auto-Submitted": "auto-replied", "X-Auto-Response-Suppress": "All"})
    link = core.find_link("email", inbound.sender)
    if link and inbound.conversation_id:
        core.remember_thread(link.user_id, new_id.strip("<>"), inbound.conversation_id)


# ------------------------------------------------------------------ polling
def poll_once():
    """Read up to MAX_PER_POLL unread messages, mark them read and answer them. Returns how many were handled."""
    if not settings.EMAIL_CHANNEL_ENABLED:
        return 0
    handled = 0
    with imaplib.IMAP4_SSL(settings.EMAIL_CHANNEL_IMAP_HOST, settings.EMAIL_CHANNEL_IMAP_PORT,
                           ssl_context=ssl.create_default_context(), timeout=30) as box:
        box.login(settings.EMAIL_CHANNEL_USER, settings.EMAIL_CHANNEL_PASSWORD)
        box.select(settings.EMAIL_CHANNEL_FOLDER)
        _, data = box.uid("search", None, "UNSEEN")
        for uid in (data[0].split() if data and data[0] else [])[:MAX_PER_POLL]:
            _, parts = box.uid("fetch", uid, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            box.uid("store", uid, "+FLAGS", "(\\Seen)")  # mark first: a crash must never cause a second reply
            if not raw or len(raw) > MAX_RAW:
                continue
            try:
                inbound = to_inbound(raw)
                if inbound:
                    core.handle(inbound)
                    handled += 1
            except Exception:  # noqa: BLE001
                log.exception("email channel: could not handle a message")
    return handled


def _lease(worker):
    """Only one process polls at a time; a lease older than three intervals is taken over."""
    now = db.now_us()
    stale = now - 3 * settings.EMAIL_CHANNEL_POLL_SECONDS * 10**6
    with db.engine.begin() as c:
        row = c.execute(select(db.channel_state).where(db.channel_state.c.key == "email_poller")).first()
        if row is None:
            try:
                c.execute(insert(db.channel_state).values(key="email_poller", value=worker, updated_at=now))
                return True
            except IntegrityError:
                return False
        if row.value != worker and row.updated_at >= stale:
            return False
        res = c.execute(update(db.channel_state).where(db.channel_state.c.key == "email_poller",
                                                       db.channel_state.c.updated_at == row.updated_at)
                        .values(value=worker, updated_at=now))
        return res.rowcount == 1


def start_poller(app):
    """Start the background poller once per process (called on the first web request)."""
    if not settings.EMAIL_CHANNEL_ENABLED or not settings.EMAIL_CHANNEL_POLLER or settings.IS_TEST or _started.is_set():
        return
    _started.set()
    worker = uuid.uuid4().hex

    def loop():
        with app.app_context():
            while True:
                try:
                    if _lease(worker):
                        n = poll_once()
                        if n:
                            log.info("email channel: handled %s message(s)", n)
                except Exception:  # noqa: BLE001
                    log.exception("email channel: polling failed")
                time.sleep(settings.EMAIL_CHANNEL_POLL_SECONDS)

    threading.Thread(target=loop, daemon=True, name="email-poller").start()
    log.info("email channel: polling %s every %ss", settings.EMAIL_CHANNEL_ADDRESS, settings.EMAIL_CHANNEL_POLL_SECONDS)
