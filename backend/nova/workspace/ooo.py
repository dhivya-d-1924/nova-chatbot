"""Out-of-office replies for the signed-in user, applied to Gmail (vacation responder) and/or Outlook (automatic
replies). Both providers schedule the start and end themselves, so no reply is ever sent by Nova directly.

Nothing is invented: the messages are exactly what the user wrote; an alternate contact is only mentioned when the
user gave one. Preview and a test email (to the user themself) are available before turning it on."""
import json
from datetime import datetime, timezone

from sqlalchemy import insert, select, update

from .. import db, tools
from ..errors import ApiError
from . import common, gmail, teams_graph

PROVIDERS = ("gmail", "outlook")


def get(uid):
    with db.engine.connect() as c:
        r = c.execute(select(db.ooo_settings).where(db.ooo_settings.c.user_id == uid)).first()
    if not r:
        return None
    return {"start_at": r.start_at, "end_at": r.end_at, "internal_message": r.internal_message,
            "external_message": r.external_message, "alternate_contact": r.alternate_contact,
            "providers": json.loads(r.providers), "status": r.status, "applied": json.loads(r.applied or "{}"),
            "updated_at": r.updated_at}


def validate(uid, d):
    tz = common.tz_of(common.prefs(uid))
    start, end = common.parse_dt(d.get("start"), tz), common.parse_dt(d.get("end"), tz)
    if not start or not end or end <= start:
        raise ApiError(400, "Give a start and an end (end after start).", "invalid_field")
    internal = str(d.get("internal_message") or d.get("internal") or "").strip()
    if not internal:
        raise ApiError(400, "Write the reply people will receive.", "invalid_field")
    external = str(d.get("external_message") or d.get("external") or "").strip()
    contact = str(d.get("alternate_contact") or d.get("contact") or "").strip()
    if contact and not common.EMAIL.fullmatch(contact):
        raise ApiError(400, "The alternate contact must be an email address.", "invalid_field")
    providers = [p for p in (d.get("providers") or []) if p in PROVIDERS]
    have = common.connected(uid)
    providers = [p for p in providers if have["google" if p == "gmail" else "microsoft"]]
    if not providers:
        raise ApiError(400, "Choose Gmail and/or Outlook (the account must be connected).", "invalid_field")
    return {"start_at": int(start.timestamp() * 1e6), "end_at": int(end.timestamp() * 1e6), "internal_message": internal[:4000],
            "external_message": external[:4000] or None, "alternate_contact": contact or None, "providers": providers}


def save(uid, d):
    v = validate(uid, d)
    now = db.now_us()
    vals = {**v, "providers": json.dumps(v["providers"]), "updated_at": now}
    with db.engine.begin() as c:
        if c.execute(select(db.ooo_settings.c.user_id).where(db.ooo_settings.c.user_id == uid)).first():
            c.execute(update(db.ooo_settings).where(db.ooo_settings.c.user_id == uid).values(**vals))
        else:
            c.execute(insert(db.ooo_settings).values(user_id=uid, status="draft", **vals))
    return get(uid)


def render(s):
    def add_contact(text):
        c = s.get("alternate_contact")
        return f"{text}\n\nFor anything urgent, please contact {c}." if c and c.lower() not in text.lower() else text
    internal = add_contact(s["internal_message"])
    return internal, add_contact(s["external_message"]) if s.get("external_message") else None


def preview(uid):
    s = get(uid)
    if not s:
        raise ApiError(404, "Save your out-of-office first.", "not_found")
    tz = common.tz_of(common.prefs(uid))
    internal, external = render(s)
    start = datetime.fromtimestamp(s["start_at"] / 1e6, timezone.utc)
    end = datetime.fromtimestamp(s["end_at"] / 1e6, timezone.utc)
    return {"internal": internal, "external": external or "(no reply to people outside your organisation)",
            "from": common.fmt(start, tz), "until": common.fmt(end, tz), "providers": s["providers"]}


def send_test(uid):
    if not common.connected(uid)["google"]:
        raise ApiError(400, "Connect Google to send a test email to yourself.", "not_connected")
    p = preview(uid)
    me = gmail.my_address(uid)
    gmail.send(uid, [me], "[Test] Your out-of-office reply",
               f"This is how your automatic reply will look.\n\nTo colleagues:\n{p['internal']}\n\nTo people outside:\n{p['external']}\n\n"
               f"Active from {p['from']} until {p['until']}.")
    common.log_activity(uid, "ooo_test", f"Out-of-office test email sent to {me}", "done", "google")
    return me


def apply(uid, enable=True, draft=None):
    """Turn the saved (or given) out-of-office on or off at each provider. Returns a summary of confirmed results."""
    if draft and enable and draft.get("start"):
        save(uid, draft)
    s = get(uid)
    if enable and not s:
        raise tools.ToolError("Save an out-of-office first.")
    results, providers = {}, (s or {}).get("providers") or list(PROVIDERS)
    have = common.connected(uid)
    for p in providers:
        try:
            if p == "gmail" and have["google"]:
                internal, external = render(s) if enable else ("", None)
                gmail.set_vacation(uid, enable, "Out of office", external or internal, s["start_at"] if s else None,
                                   s["end_at"] if s else None, internal_only=enable and not external)
                results[p] = "on" if enable else "off"
            elif p == "outlook" and have["microsoft"]:
                internal, external = render(s) if enable else ("", None)
                start = datetime.fromtimestamp(s["start_at"] / 1e6, timezone.utc) if enable else None
                end = datetime.fromtimestamp(s["end_at"] / 1e6, timezone.utc) if enable else None
                got = teams_graph.set_auto_replies(uid, enable, internal, external or "", start, end,
                                                   "all" if external else "none")
                results[p] = got.get("status", "updated")
        except tools.ToolError as e:
            results[p] = f"failed: {e}"
    if s:
        with db.engine.begin() as c:
            c.execute(update(db.ooo_settings).where(db.ooo_settings.c.user_id == uid).values(
                status="on" if enable and any(not str(v).startswith("failed") for v in results.values()) else "off",
                applied=json.dumps(results), updated_at=db.now_us()))
    failed = {k: v for k, v in results.items() if str(v).startswith("failed")}
    if failed and len(failed) == len(results):
        raise tools.ToolError("; ".join(f"{k}: {v}" for k, v in failed.items()))
    done = ", ".join(f"{'Gmail' if k == 'gmail' else 'Outlook'} {v}" for k, v in results.items())
    return f"Out-of-office {'turned on' if enable else 'turned off'} ({done})."
