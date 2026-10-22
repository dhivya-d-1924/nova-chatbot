"""Gmail: search, read, unanswered threads, drafts, replies in the same thread, new emails, forwards, labels and the
vacation (out-of-office) responder. Callers decide about approval; this module only talks to Gmail and returns what
Gmail confirmed."""
import base64
import re
import urllib.parse
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from .. import tools
from ..integrations import google
from . import common

ID = re.compile(r"[A-Za-z0-9_\-]{5,200}")


def _q(v):
    return urllib.parse.quote(v, safe="")


def _id(v, what="message_id"):
    v = str(v or "").strip()
    if not ID.fullmatch(v):
        raise tools.ToolError(f"'{what}' must be an id returned by an email search.")
    return v


def _hdr(msg, name):
    return next((h["value"] for h in (msg.get("payload") or {}).get("headers", []) if h.get("name", "").lower() == name.lower()), "")


def my_address(uid):
    row = google.connection(uid)
    return (row.account if row else "") or google._api(uid, "GET", "/gmail/v1/users/me/profile").get("emailAddress", "")


def get(uid, message_id, fmt="full"):
    params = {"format": fmt}
    if fmt == "metadata":
        params["metadataHeaders"] = ["From", "To", "Cc", "Subject", "Date", "Message-ID", "References", "In-Reply-To"]
    return google._api(uid, "GET", f"/gmail/v1/users/me/messages/{_q(_id(message_id))}", params)


def summary(msg):
    return {"id": msg.get("id"), "thread_id": msg.get("threadId"), "from": _hdr(msg, "From"), "to": _hdr(msg, "To"),
            "subject": _hdr(msg, "Subject"), "date": _hdr(msg, "Date"), "snippet": msg.get("snippet", ""),
            "unread": "UNREAD" in (msg.get("labelIds") or []), "important": "IMPORTANT" in (msg.get("labelIds") or []),
            "link": f"https://mail.google.com/mail/u/0/#all/{msg.get('threadId')}"}


def search_page(uid, query, limit=10, page_token=None):
    """One page of matching messages and the token for the next page (None when there are no more)."""
    params = {"q": query[:300], "maxResults": min(int(limit), 25)}
    if page_token:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,200}", str(page_token)):
            raise tools.ToolError("Invalid page token.")
        params["pageToken"] = page_token
    res = google._api(uid, "GET", "/gmail/v1/users/me/messages", params)
    return [summary(get(uid, m["id"], "metadata")) for m in res.get("messages", [])], res.get("nextPageToken")


def search(uid, query, limit=10):
    return search_page(uid, query, limit)[0]


def attachments_of(msg):
    """Attachment metadata (no content) from a full message."""
    out = []

    def walk(part):
        body = part.get("body") or {}
        if part.get("filename") and body.get("attachmentId"):
            out.append({"id": body["attachmentId"], "filename": part["filename"][:200], "mime": part.get("mimeType", ""),
                        "size": int(body.get("size") or 0)})
        for p in part.get("parts") or []:
            walk(p)
    walk(msg.get("payload") or {})
    return out


def body_text(msg):
    from ..integrations.google import _plain_body
    return (_plain_body(msg.get("payload", {})) or msg.get("snippet", "")).strip()


def detail(uid, message_id):
    """The message with its text and attachment list (names and sizes only)."""
    m = get(uid, message_id, "full")
    return {**summary(m), "cc": _hdr(m, "Cc"), "body": body_text(m)[:20000], "attachments": attachments_of(m)}


def mark_read(uid, message_id, read=True):
    change = {"removeLabelIds": ["UNREAD"]} if read else {"addLabelIds": ["UNREAD"]}
    return google._api(uid, "POST", f"/gmail/v1/users/me/messages/{_q(_id(message_id))}/modify", body=change)


def unanswered(uid, days=3, limit=10):
    """Inbox threads where the latest message is from someone else and is older than `days` days."""
    me = my_address(uid).lower()
    res = google._api(uid, "GET", "/gmail/v1/users/me/threads", {
        "q": f"in:inbox -category:promotions -category:social -category:updates older_than:{int(days)}d newer_than:30d",
        "maxResults": 25}).get("threads", [])
    out = []
    for t in res:
        th = google._api(uid, "GET", f"/gmail/v1/users/me/threads/{_q(_id(t['id'], 'thread_id'))}",
                         {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        msgs = th.get("messages") or []
        if not msgs:
            continue
        last = msgs[-1]
        sender = parseaddr(_hdr(last, "From"))[1].lower()
        if sender and sender != me and not sender.startswith(("no-reply", "noreply", "mailer-daemon")):
            out.append(summary(last))
        if len(out) >= limit:
            break
    return out


def _raw(to, subject, body, cc=(), in_reply_to=None, references=None, sender=None, attachments=()):
    m = EmailMessage()
    m["To"] = ", ".join(to)
    if cc:
        m["Cc"] = ", ".join(cc)
    if sender:
        m["From"] = sender
    m["Subject"] = subject.replace("\n", " ").replace("\r", " ")[:300]
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = (references + " " if references else "") + in_reply_to
    m.set_content(body)
    for a in attachments:
        maintype, _, subtype = (a.get("mime") or "application/octet-stream").partition("/")
        m.add_attachment(a["data"], maintype=maintype or "application", subtype=subtype or "octet-stream", filename=a["filename"])
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


def _recipients(value, what="recipient"):
    people = common.emails_in(value)
    if not people:
        raise tools.ToolError(f"Give at least one valid {what} email address.")
    return people


def reply_context(uid, message_id, reply_all=False):
    """Who a reply goes to, its subject and threading headers, from the original message."""
    me = my_address(uid).lower()
    m = get(uid, message_id, "metadata")
    frm = parseaddr(_hdr(m, "From"))[1].lower()
    to = [frm] if frm and frm != me else [a for _, a in getaddresses([_hdr(m, "To")]) if a and a.lower() != me]
    cc = []
    if reply_all:
        cc = [a.lower() for _, a in getaddresses([_hdr(m, "To"), _hdr(m, "Cc")]) if a and a.lower() not in (me, *to)]
    subject = _hdr(m, "Subject")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    return {"to": common.emails_in(to), "cc": common.emails_in(cc), "subject": subject, "thread_id": m.get("threadId"),
            "in_reply_to": _hdr(m, "Message-ID"), "references": _hdr(m, "References"), "original_from": _hdr(m, "From"),
            "original_subject": _hdr(m, "Subject")}


def send(uid, to, subject, body, cc=(), thread=None, attachments=()):
    """Send now. thread = reply_context(...) to reply inside the conversation. Returns Gmail's message summary."""
    raw = _raw(_recipients(to), subject, body, common.emails_in(cc), (thread or {}).get("in_reply_to"),
               (thread or {}).get("references"), attachments=attachments)
    payload = {"raw": raw}
    if thread and thread.get("thread_id"):
        payload["threadId"] = thread["thread_id"]
    sent = google._api(uid, "POST", "/gmail/v1/users/me/messages/send", body=payload)
    if not sent.get("id"):
        raise tools.ToolError("Gmail didn't confirm the email; it was not sent.")
    return sent


def draft(uid, to, subject, body, cc=(), thread=None):
    raw = _raw(common.emails_in(to), subject, body, common.emails_in(cc), (thread or {}).get("in_reply_to"),
               (thread or {}).get("references"))
    msg = {"raw": raw}
    if thread and thread.get("thread_id"):
        msg["threadId"] = thread["thread_id"]
    d = google._api(uid, "POST", "/gmail/v1/users/me/drafts", body={"message": msg})
    if not d.get("id"):
        raise tools.ToolError("Gmail didn't confirm the draft.")
    return d


MAX_FORWARD_BYTES = 20 * 1024 * 1024   # Gmail's limit is 25 MB including encoding overhead


def forward_plan(uid, message_id, include_attachments=True):
    """What a forward would include: subject and the attachments that fit (shown in the approval preview)."""
    m = get(uid, message_id, "full")
    atts, skipped, total = [], [], 0
    for a in attachments_of(m) if include_attachments else []:
        if total + a["size"] > MAX_FORWARD_BYTES:
            skipped.append(a)
            continue
        total += a["size"]
        atts.append(a)
    return m, atts, skipped


def forward(uid, message_id, to, note="", include_attachments=True):
    """Forward a message with its text and (by default) its attachments up to 20 MB in total."""
    m, atts, skipped = forward_plan(uid, message_id, include_attachments)
    files = []
    for a in atts:
        data = google._api(uid, "GET", f"/gmail/v1/users/me/messages/{_q(_id(message_id))}/attachments/{_q(a['id'])}").get("data", "")
        files.append({**a, "data": base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))})
    body = (note.strip() + "\n\n" if note else "") + "---------- Forwarded message ---------\n" + \
        f"From: {_hdr(m, 'From')}\nDate: {_hdr(m, 'Date')}\nSubject: {_hdr(m, 'Subject')}\nTo: {_hdr(m, 'To')}\n\n{body_text(m)}"
    if skipped:
        body += "\n\n(Not attached, too large: " + ", ".join(a["filename"] for a in skipped) + ")"
    subject = _hdr(m, "Subject")
    sent = send(uid, to, subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}", body[:50000], attachments=files)
    sent["attached"] = [a["filename"] for a in files]
    return sent


def add_label(uid, message_id, label):
    label = str(label or "").strip()[:100]
    if not label:
        raise tools.ToolError("Give a label name.")
    labels = google._api(uid, "GET", "/gmail/v1/users/me/labels").get("labels", [])
    found = next((x for x in labels if x.get("name", "").lower() == label.lower()), None)
    if not found:
        found = google._api(uid, "POST", "/gmail/v1/users/me/labels", body={
            "name": label, "labelListVisibility": "labelShow", "messageListVisibility": "show"})
    google._api(uid, "POST", f"/gmail/v1/users/me/messages/{_q(_id(message_id))}/modify", body={"addLabelIds": [found["id"]]})
    return found


def thread_has_reply_after(uid, thread_id, after_us, me=None):
    """True when someone other than the user wrote in the thread after the given time (for follow-up checks)."""
    me = (me or my_address(uid)).lower()
    th = google._api(uid, "GET", f"/gmail/v1/users/me/threads/{_q(_id(thread_id, 'thread_id'))}",
                     {"format": "metadata", "metadataHeaders": ["From"]})
    for m in th.get("messages") or []:
        if int(m.get("internalDate", 0)) * 1000 > after_us and parseaddr(_hdr(m, "From"))[1].lower() != me:
            return True
    return False


def set_vacation(uid, enable, subject="", body="", start_us=None, end_us=None, internal_only=False):
    payload = {"enableAutoReply": bool(enable)}
    if enable:
        payload.update(responseSubject=subject[:200], responseBodyPlainText=body[:10000], restrictToDomain=bool(internal_only),
                       restrictToContacts=False)
        if start_us:
            payload["startTime"] = str(start_us // 1000)
        if end_us:
            payload["endTime"] = str(end_us // 1000)
    return google._api(uid, "PUT", "/gmail/v1/users/me/settings/vacation", body=payload)
