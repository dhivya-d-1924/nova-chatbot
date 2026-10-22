"""The single place where consequential workspace actions are executed.

Callers (approved chat steps, the approvals center, policy automation) pass a kind and a payload. Each action:
- re-validates its input (dates, recipients, ids),
- refuses an identical send within 24 hours (duplicate protection),
- reports only what the provider confirmed (ids/links from the API response),
- is written to the activity history (success or failure, never message bodies or tokens).
"""
import json

from .. import tools
from . import calendar, common, gmail, teams_graph

KINDS = {
    "email_send": "Send an email", "email_reply": "Reply to an email", "email_forward": "Forward an email",
    "email_draft": "Create a Gmail draft", "email_label": "Label an email",
    "email_mark_read": "Mark an email read/unread", "teams_chat": "Send a Teams message",
    "teams_channel": "Post in a Teams channel", "calendar_create": "Create a meeting", "calendar_update": "Change a meeting",
    "calendar_cancel": "Cancel a meeting", "ooo_enable": "Turn on out-of-office", "ooo_disable": "Turn off out-of-office",
}


def _tz(uid):
    return common.tz_of(common.prefs(uid))


def preview(uid, kind, p):
    """Plain-text description of exactly what will happen, shown before approval."""
    tz = _tz(uid)
    if kind in ("email_send", "email_draft"):
        lines = [f"To: {', '.join(common.emails_in(p.get('to')))}"]
        if p.get("cc"):
            lines.append(f"Cc: {', '.join(common.emails_in(p.get('cc')))}")
        return "\n".join(lines + [f"Subject: {p.get('subject', '')}", "", p.get("body", "")])
    if kind == "email_reply":
        ctx = gmail.reply_context(uid, p["message_id"], bool(p.get("reply_all")))
        return "\n".join([f"Reply to: {', '.join(ctx['to'])}" + (f" (cc {', '.join(ctx['cc'])})" if ctx["cc"] else ""),
                          f"Subject: {ctx['subject']}", "", p.get("body", "")])
    if kind == "email_forward":
        m, atts, skipped = gmail.forward_plan(uid, p.get("message_id"), p.get("include_attachments", True) is not False)
        lines = [f"Forward “{gmail._hdr(m, 'Subject')}” (from {gmail._hdr(m, 'From')})",
                 f"To: {', '.join(common.emails_in(p.get('to')))}",
                 "Attachments: " + (", ".join(f"{a['filename']} ({_size(a['size'])})" for a in atts) or "none")]
        if skipped:
            lines.append("Too large to attach: " + ", ".join(a["filename"] for a in skipped))
        return "\n".join(lines + ["", p.get("note", "")]).strip()
    if kind == "email_mark_read":
        return f"Mark message {p.get('message_id')} as {'unread' if p.get('read') is False else 'read'}"
    if kind == "email_label":
        return f"Add label “{p.get('label')}” to message {p.get('message_id')}"
    if kind == "teams_chat":
        return f"Teams message to {p.get('to_email') or 'chat ' + str(p.get('chat_id'))}:\n\n{p.get('text', '')}"
    if kind == "teams_channel":
        return f"Teams channel post ({p.get('team_id')}/{p.get('channel_id')}):\n\n{p.get('text', '')}"
    if kind == "calendar_create":
        s, e = common.parse_dt(p["start"], tz), common.parse_dt(p["end"], tz)
        return "\n".join([f"Create “{p.get('title')}”", f"When: {common.fmt(s, tz)} – {e.astimezone(tz).strftime('%H:%M')}",
                          f"Attendees: {', '.join(common.emails_in(p.get('attendees'))) or 'just you'}",
                          f"Calendar: {calendar.pick_provider(uid, p.get('provider') or None)}",
                          "Online meeting link: yes" if p.get("online", True) else "", p.get("description", "")]).strip()
    if kind == "calendar_update":
        parts = [f"Change event {p.get('event_id')} ({p.get('provider')})"]
        if p.get("start"):
            parts.append(f"New time: {common.fmt(common.parse_dt(p['start'], tz), tz)}")
        if p.get("title"):
            parts.append(f"New title: {p['title']}")
        if p.get("add_attendees"):
            parts.append(f"Add: {', '.join(common.emails_in(p['add_attendees']))}")
        return "\n".join(parts + ["Attendees are notified."])
    if kind == "calendar_cancel":
        return f"Cancel (or decline) event {p.get('event_id')} ({p.get('provider')}); attendees are notified.\n\n{p.get('message', '')}"
    if kind == "ooo_enable":
        return (f"Out-of-office from {p.get('start')} to {p.get('end')} on {', '.join(p.get('providers') or [])}\n\n"
                f"Internal:\n{p.get('internal')}\n\nExternal:\n{p.get('external') or '(none)'}")
    if kind == "ooo_disable":
        return "Turn off out-of-office replies."
    return json.dumps(p)[:2000]


def validate(uid, kind, p):
    """Check a prepared message has what it needs before it is queued for approval."""
    if kind in ("email_send", "email_forward") and not common.emails_in(p.get("to")):
        raise common.InputError("Add at least one valid recipient email address.")
    if kind == "email_send" and not str(p.get("body") or "").strip():
        raise common.InputError("The email has no text.")
    if kind in ("email_reply", "email_forward") and not gmail.ID.fullmatch(str(p.get("message_id") or "")):
        raise common.InputError("Pick the email this is about.")
    if kind == "email_reply" and not str(p.get("body") or "").strip():
        raise common.InputError("The reply has no text.")
    if kind in ("teams_chat", "teams_channel") and not str(p.get("text") or "").strip():
        raise common.InputError("The message has no text.")
    if kind == "teams_chat" and not (p.get("chat_id") or len(common.emails_in(p.get("to_email"))) == 1):
        raise common.InputError("Give one colleague's email or a chat.")
    if kind == "teams_channel" and not (teams_graph.GUID.fullmatch(str(p.get("team_id") or "")) and
                                        teams_graph.CHAT_ID.fullmatch(str(p.get("channel_id") or ""))):
        raise common.InputError("Pick a team and channel.")
    preview(uid, kind, p)


def _size(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


def _dup_check(uid, key, what):
    prev = common.already_done(uid, key)
    if prev:
        raise tools.ToolError(f"An identical {what} was already sent in the last 24 hours, so it was not sent again.")


def execute(uid, kind, p, origin="assistant"):
    """Run an action. Returns (message for the user, provider reference). Raises ToolError on failure."""
    if kind not in KINDS:
        raise tools.ToolError("Unknown action.")
    tz = _tz(uid)
    key = None
    try:
        if kind == "email_send":
            key = common.idem_key(kind, sorted(common.emails_in(p.get("to"))), p.get("subject"), p.get("body"))
            _dup_check(uid, key, "email")
            sent = gmail.send(uid, p.get("to"), str(p.get("subject") or "")[:300], str(p.get("body") or ""), p.get("cc") or ())
            msg, ref, provider = f"Email sent to {', '.join(common.emails_in(p.get('to')))} (Gmail id {sent['id']}).", sent["id"], "google"
        elif kind == "email_reply":
            key = common.idem_key(kind, p.get("message_id"), p.get("body"))
            _dup_check(uid, key, "reply")
            ctx = gmail.reply_context(uid, p["message_id"], bool(p.get("reply_all")))
            sent = gmail.send(uid, ctx["to"], ctx["subject"], str(p.get("body") or ""), ctx["cc"], ctx)
            msg, ref, provider = f"Reply sent to {', '.join(ctx['to'])} in the same thread (Gmail id {sent['id']}).", sent["id"], "google"
        elif kind == "email_forward":
            key = common.idem_key(kind, p.get("message_id"), sorted(common.emails_in(p.get("to"))))
            _dup_check(uid, key, "forward")
            sent = gmail.forward(uid, p["message_id"], p.get("to"), str(p.get("note") or ""), p.get("include_attachments", True) is not False)
            att = f" with {len(sent['attached'])} attachment(s)" if sent.get("attached") else ""
            msg, ref, provider = f"Forwarded to {', '.join(common.emails_in(p.get('to')))}{att} (Gmail id {sent['id']}).", sent["id"], "google"
        elif kind == "email_draft":
            thread = gmail.reply_context(uid, p["reply_to_message_id"]) if p.get("reply_to_message_id") else None
            to = p.get("to") or (thread or {}).get("to")
            subject = p.get("subject") or (thread or {}).get("subject", "")
            d = gmail.draft(uid, to, str(subject)[:300], str(p.get("body") or ""), p.get("cc") or (), thread)
            msg, ref, provider = "Draft saved in Gmail (not sent). Open Gmail > Drafts to review and send it.", d["id"], "google"
        elif kind == "email_label":
            lab = gmail.add_label(uid, p["message_id"], p.get("label"))
            msg, ref, provider = f"Label “{lab.get('name')}” added.", p["message_id"], "google"
        elif kind == "email_mark_read":
            gmail.mark_read(uid, p["message_id"], p.get("read") is not False)
            msg, ref, provider = f"Marked as {'unread' if p.get('read') is False else 'read'}.", p["message_id"], "google"
        elif kind == "teams_chat":
            chat_id = p.get("chat_id") or teams_graph.chat_with(uid, p.get("to_email"))
            key = common.idem_key(kind, chat_id, p.get("text"))
            _dup_check(uid, key, "Teams message")
            m = teams_graph.send_chat(uid, chat_id, str(p.get("text") or ""))
            msg, ref, provider = "Teams message sent.", m.get("webUrl") or m["id"], "microsoft"
        elif kind == "teams_channel":
            key = common.idem_key(kind, p.get("team_id"), p.get("channel_id"), p.get("text"))
            _dup_check(uid, key, "channel post")
            m = teams_graph.post_channel(uid, p.get("team_id"), p.get("channel_id"), str(p.get("text") or ""))
            msg, ref, provider = "Posted in the Teams channel.", m.get("webUrl") or m["id"], "microsoft"
        elif kind == "calendar_create":
            start, end = common.parse_dt(p["start"], tz), common.parse_dt(p["end"], tz)
            key = common.idem_key(kind, p.get("title"), start.isoformat(), sorted(common.emails_in(p.get("attendees"))))
            _dup_check(uid, key, "meeting")
            ev = calendar.create_event(uid, str(p.get("title") or "Meeting"), start, end, p.get("attendees") or (),
                                       str(p.get("description") or ""), str(p.get("location") or ""), bool(p.get("online", True)),
                                       p.get("provider") or None)
            msg = f"Meeting created and confirmed by {ev['provider']}: {calendar.describe(ev, tz)}"
            ref, provider = ev.get("web_link") or ev["id"], ev["provider"]
        elif kind == "calendar_update":
            ev = calendar.update_event(uid, p.get("provider"), p["event_id"], common.parse_dt(p.get("start"), tz),
                                       common.parse_dt(p.get("end"), tz), p.get("title"), p.get("add_attendees") or (), p.get("note"))
            msg, ref, provider = f"Meeting updated: {calendar.describe(ev, tz)}", ev.get("web_link") or ev["id"], ev["provider"]
        elif kind == "calendar_cancel":
            ev, how = calendar.cancel_event(uid, p.get("provider"), p["event_id"], str(p.get("message") or ""))
            msg, ref, provider = f"Meeting “{ev['title']}” {how}; attendees were notified.", ev["id"], ev["provider"]
        elif kind in ("ooo_enable", "ooo_disable"):
            from . import ooo
            result = ooo.apply(uid, enable=kind == "ooo_enable", draft=p)
            msg, ref, provider = result, None, None
    except tools.ToolError as e:
        common.log_activity(uid, kind, f"{KINDS[kind]} failed: {e}"[:500], "failed", key=None)
        raise
    common.log_activity(uid, kind, f"{msg} [{origin}]", "done", provider, ref, key)
    return msg, ref
