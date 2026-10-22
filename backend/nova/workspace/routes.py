"""Workspace API: accounts, preferences, today's schedule, slot finder, unified search, approvals, activity, leave,
out-of-office, action items, reminders, notifications and scheduled jobs. Every route acts only on the signed-in
user's own data."""
import json
import urllib.parse
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, redirect, request
from sqlalchemy import delete, insert, select, update

from .. import db, security, tools
from ..config import settings
from ..errors import ApiError
from ..integrations import google
from ..security import audit, auth, enforce_rate
from ..validation import Str, parse
from . import actions, approvals, briefing, calendar, common, gmail, inbox, jobs, leave, microsoft, notify, oauth, ooo, teams_graph

bp = Blueprint("workspace", __name__, url_prefix="/api/workspace")


def _tool(fn, *a, **kw):
    """Run a provider call and turn its friendly ToolError into a JSON error."""
    try:
        return fn(*a, **kw)
    except common.InputError as e:
        raise ApiError(400, str(e), "invalid_field")
    except tools.ToolError as e:
        raise ApiError(502, str(e), "provider_error")


def _ev(e, tz):
    return {**{k: v for k, v in e.items() if k not in ("start", "end")}, "start": e["start"].isoformat(), "end": e["end"].isoformat(),
            "local": calendar.describe(e, tz).split(" · ")[0]}


# ------------------------------------------------------------------ accounts and preferences
def _account(provider, row, required):
    if not row:
        return {"connected": False}
    granted = set((row.scopes or "").split())
    missing = sorted(s for s in required if s not in granted and not s.startswith(("openid", "email", "profile", "offline_access")))
    return {"connected": True, "account": row.account, "since": row.created_at, "missing_scopes": missing,
            "token_ok": bool(row.refresh_token), "updated_at": row.updated_at}


@bp.get("/status")
@auth()
def status():
    uid = g.user.id
    jobs.ensure(uid)
    with db.engine.connect() as c:
        pending = c.execute(select(db.workspace_drafts.c.id).where(db.workspace_drafts.c.user_id == uid,
                                                                    db.workspace_drafts.c.status == "pending")).all()
        failed = c.execute(select(db.workspace_drafts.c.id).where(db.workspace_drafts.c.user_id == uid,
                                                                   db.workspace_drafts.c.status == "failed")).all()
    gscopes = [s.rsplit("/", 1)[-1] for s in google.SCOPES]
    grow = google.connection(uid) if settings.GOOGLE_ENABLED else None
    g_acc = _account("google", grow, [])
    if grow:
        granted = {s.rsplit("/", 1)[-1] for s in (grow.scopes or "").split()}
        g_acc["missing_scopes"] = [s for s in gscopes if s not in granted and s not in ("openid", "email")]
    mrow = microsoft.connection(uid) if settings.MS_ENABLED else None
    m_acc = _account("microsoft", mrow, microsoft.scopes())
    if mrow:
        granted = {s.lower() for s in (mrow.scopes or "").split()}
        m_acc["missing_scopes"] = [s for s in microsoft.scopes() if s.lower() not in granted
                                   and s not in ("openid", "profile", "email", "offline_access")]
    return jsonify(
        google={"configured": settings.GOOGLE_ENABLED, **g_acc},
        microsoft={"configured": settings.MS_ENABLED, **m_acc, "admin_scopes": microsoft.ADMIN_SCOPES,
                   "extra_scopes": settings.MS_EXTRA_SCOPES},
        prefs=common.prefs(uid), auto_actions=common.AUTO_ACTIONS, pending_approvals=len(pending), failed_approvals=len(failed),
        notifications=notify.listing(uid, limit=1)["unread"], jobs_running=settings.WORKSPACE_JOBS, demo=settings.WORKSPACE_DEMO)


@bp.get("/prefs")
@auth()
def get_prefs():
    return jsonify(common.prefs(g.user.id))


@bp.put("/prefs")
@auth()
def put_prefs():
    body = request.get_json(silent=True) or {}
    before = common.prefs(g.user.id)
    after = common.save_prefs(g.user.id, body)
    for k in ("mode", "paused", "auto_actions", "policies"):
        if before[k] != after[k]:  # automation changes are always recorded
            audit("workspace.automation_changed", target_type="setting", target_id=k,
                  value=after[k] if k != "policies" else {"ooo_on_own_absence": after[k]["ooo_on_own_absence"]})
    for kind in jobs.KINDS:  # working days / time zone may move scheduled times
        jobs.configure(g.user.id, kind)
    return jsonify(after)


# ------------------------------------------------------------------ Microsoft 365 connect
def _back(status_, reason=None):
    q = {"integration": "microsoft", "status": status_}
    if reason:
        q["reason"] = reason[:200]
    return redirect(f"{settings.APP_URL.rstrip('/')}/?{urllib.parse.urlencode(q)}")


@bp.get("/microsoft/connect")
@auth()
def ms_connect():
    if not settings.MS_ENABLED:
        raise ApiError(409, "Microsoft 365 isn't set up on this server yet. Ask your administrator.", "not_configured")
    enforce_rate("ms-connect", 10, 3600, key=g.user.id)
    return jsonify(url=microsoft.auth_url(g.user.id))


@bp.get("/microsoft/callback")
def ms_callback():
    if not settings.MS_ENABLED:
        raise ApiError(404, "Not found", "not_found")
    _, user = security.load_session()
    if not user:
        return _back("error", "Sign in to Nova first, then connect Microsoft 365 again.")
    g.user = user
    if request.args.get("error"):
        return _back("cancelled", request.args.get("error_description") or request.args.get("error"))
    try:
        verifier = oauth.finish(request.args.get("state"), user.id, "microsoft")
        code = request.args.get("code") or ""
        if not code or len(code) > 4000:
            return _back("error", "Missing authorization code.")
        email = microsoft.finish(user.id, code, verifier)
    except oauth.StateError as e:
        return _back("error", str(e))
    except microsoft.GraphError as e:
        return _back("error", e.message)
    audit("integration.microsoft_connected", target_type="provider", target_id="microsoft")
    return _back("connected", email)


@bp.delete("/microsoft")
@auth()
def ms_disconnect():
    if not microsoft.disconnect(g.user.id):
        raise ApiError(404, "Microsoft 365 isn't connected.", "not_found")
    audit("integration.microsoft_disconnected", target_type="provider", target_id="microsoft")
    return jsonify(ok=True)


# ------------------------------------------------------------------ calendar
@bp.get("/calendar")
@auth()
def get_calendar():
    p = common.prefs(g.user.id)
    tz = common.tz_of(p)
    day = request.args.get("date")
    try:
        d = datetime.fromisoformat(day).date() if day else common.now_utc().astimezone(tz).date()
        days = max(1, min(14, int(request.args.get("days", 1))))
    except ValueError:
        raise ApiError(400, "date must look like 2026-10-23.", "invalid_field")
    start, end = common.day_bounds(d, tz)[0], common.day_bounds(d + timedelta(days=days - 1), tz)[1]
    if not any(common.connected(g.user.id).values()):
        return jsonify(timezone=tz.key, events=[], conflicts=[], away=[], errors=["Connect Google or Microsoft 365 to see your calendar."])
    evts, errors = _tool(calendar.events, g.user.id, start, end)
    clash = calendar.conflicts(evts)
    away = leave.listing(g.user.id, d.isoformat(), (d + timedelta(days=days - 1)).isoformat(), "confirmed")
    return jsonify(timezone=tz.key, date=d.isoformat(), events=[_ev(e, tz) for e in evts], away=away, errors=errors,
                   conflicts=[{"a": a["id"], "b": b["id"], "text": f"“{a['title']}” overlaps “{b['title']}”"} for a, b in clash])


@bp.post("/slots")
@auth()
def find_slots():
    d = request.get_json(silent=True) or {}
    p = common.prefs(g.user.id)
    tz = common.tz_of(p)
    try:
        minutes = int(d.get("minutes") or 30)
    except ValueError:
        raise ApiError(400, "minutes must be a number.", "invalid_field")
    slots, unknown, tz = _tool(calendar.find_slots, g.user.id, d.get("attendees") or "", minutes,
                               _tool(common.parse_dt, d.get("start"), tz), _tool(common.parse_dt, d.get("end"), tz), 8)
    return jsonify(timezone=tz.key, unknown=unknown,
                   slots=[{"start": s.isoformat(), "end": e.isoformat(), "label": common.fmt(s, tz)} for s, e in slots])


@bp.post("/meetings")
@auth()
def propose_meeting():
    """Create a meeting draft in the approvals center (the UI's "schedule" form). Nothing is sent until approved."""
    d = request.get_json(silent=True) or {}
    title = str(d.get("title") or "").strip()[:300]
    if not title or not d.get("start") or not d.get("end"):
        raise ApiError(400, "Give a title, start and end.", "invalid_field")
    payload = {k: d.get(k) for k in ("title", "start", "end", "attendees", "description", "location", "online", "provider") if d.get(k) not in (None, "")}
    did = approvals.create(g.user.id, "calendar_create", f"Meeting: {title}", payload, origin="user")
    return jsonify(approvals.as_dict(g.user.id, approvals._row(g.user.id, did))), 201


# ------------------------------------------------------------------ search and briefing
@bp.get("/search")
@auth()
def search():
    """One query across Gmail and Teams. Filters: source=all|gmail|teams, from=<name or email>, since/until=YYYY-MM-DD,
    page=<Gmail page token> for more email results. Every result names its source and links back to it."""
    q = (request.args.get("q") or "").strip()[:200]
    if len(q) < 2:
        raise ApiError(400, "Type at least 2 characters.", "invalid_field")
    src = request.args.get("source") or "all"
    if src not in ("all", "gmail", "teams"):
        raise ApiError(400, "source must be all, gmail or teams.", "invalid_field")
    who = (request.args.get("from") or "").strip()[:100]
    since, until = (request.args.get(k) or "" for k in ("since", "until"))
    for v in (since, until):
        if v and not common.DATE.fullmatch(v):
            raise ApiError(400, "Dates must look like 2026-10-22.", "invalid_field")
    enforce_rate("workspace-search", 30, 60, key=g.user.id)
    have = common.connected(g.user.id)
    results, errors, next_page = [], [], None
    if have["google"] and src in ("all", "gmail"):
        gq = q + (f' from:"{who.replace(chr(34), "")}"' if who else "") + (f" after:{since.replace('-', '/')}" if since else "")
        if until:
            gq += f" before:{(datetime.fromisoformat(until) + timedelta(days=1)).strftime('%Y/%m/%d')}"
        try:
            mails, next_page = gmail.search_page(g.user.id, gq, 10, request.args.get("page") or None)
            results += [{"source": "gmail", "title": m["subject"] or "(no subject)", "from": m["from"], "at": m["date"],
                         "snippet": m["snippet"], "link": m["link"], "id": m["id"], "unread": m["unread"]} for m in mails]
        except tools.ToolError as e:
            errors.append(f"Gmail: {e}")
    if have["microsoft"] and src in ("all", "teams") and not request.args.get("page"):
        try:
            results += [{"source": "teams", "title": h["text"][:80], "from": h["from"], "at": h["at"], "snippet": h["text"],
                         "link": h["link"], "id": h["id"], "chat_id": h["chat_id"], "channel_id": h["channel_id"]}
                        for h in teams_graph.search(g.user.id, q, 10, who or None, since or None, until or None)]
        except tools.ToolError as e:
            errors.append(f"Teams: {e}")
    if not have["google"] and not have["microsoft"]:
        errors.append("Connect Google or Microsoft 365 to search email and Teams.")
    return jsonify(results=results, errors=errors, next_page=next_page)


# ------------------------------------------------------------------ inbox across Gmail and Teams
@bp.get("/inbox/needs-response")
@auth()
def needs_response():
    enforce_rate("workspace-inbox", 20, 60, key=g.user.id)
    days = request.args.get("days")
    return jsonify(inbox.needs_response(g.user.id, int(days) if days and days.isdigit() else None))


@bp.post("/inbox/triage")
@auth()
def triage():
    enforce_rate("workspace-ai", 10, 60, key=g.user.id)
    if not common.connected(g.user.id)["google"]:
        raise ApiError(400, "Connect Google to sort your email.", "not_connected")
    return jsonify(_tool(inbox.triage, g.user.id))


@bp.post("/inbox/action-items")
@auth()
def extract_items():
    enforce_rate("workspace-ai", 10, 60, key=g.user.id)
    d = request.get_json(silent=True) or {}
    out = _tool(inbox.extract_action_items, g.user.id, d.get("days") if isinstance(d.get("days"), int) else 3)
    audit("workspace.items_extracted", added=len(out["added"]))
    return jsonify(out)


@bp.post("/inbox/convert")
@auth()
def convert():
    """Draft a Teams update from an email, an email from a Teams discussion, or a reply. The draft waits in Approvals."""
    enforce_rate("workspace-ai", 10, 60, key=g.user.id)
    d = parse(request, {"source": Str(choices=("gmail", "teams"), required=True), "ref": Str(min=1, max=300, required=True),
                        "target": Str(choices=tuple(inbox.TARGETS), required=True), "instructions": Str(max=500)})
    dest = (request.get_json(silent=True) or {}).get("dest") or {}
    if not isinstance(dest, dict):
        raise ApiError(400, "dest must be an object.", "invalid_field")
    return jsonify(_tool(inbox.convert, g.user.id, d["source"], d["ref"], d["target"], dest, d.get("instructions") or "")), 201


@bp.get("/teams/channels")
@auth()
def teams_channels():
    if not common.connected(g.user.id)["microsoft"]:
        return jsonify([])
    return jsonify(_tool(teams_graph.teams_and_channels, g.user.id))


@bp.get("/email/<mid>")
@auth()
def email_detail(mid):
    return jsonify(_tool(gmail.detail, g.user.id, mid))


@bp.post("/email/<mid>/read")
@auth()
def email_read(mid):
    """Mark read/unread: the user's own click on their own mailbox, so it runs directly (and is logged)."""
    read = (request.get_json(silent=True) or {}).get("read") is not False
    msg, _ = _tool(actions.execute, g.user.id, "email_mark_read", {"message_id": mid, "read": read}, "user")
    return jsonify(ok=True, message=msg)


DRAFTABLE = ("email_send", "email_reply", "email_forward", "teams_chat", "teams_channel")


@bp.post("/approvals")
@auth()
def new_draft():
    """Prepare an outgoing message the user wrote or edited (e.g. forward with attachments). It waits for approval."""
    d = parse(request, {"kind": Str(choices=DRAFTABLE, required=True), "title": Str(min=1, max=300, required=True, label="Title")})
    payload = (request.get_json(silent=True) or {}).get("payload")
    if not isinstance(payload, dict) or len(json.dumps(payload)) > 60000:
        raise ApiError(400, "payload must be an object.", "invalid_field")
    _tool(actions.validate, g.user.id, d["kind"], payload)   # check ids/recipients now, not at send time
    did = approvals.create(g.user.id, d["kind"], d["title"], payload, origin="user")
    return jsonify(approvals.as_dict(g.user.id, approvals._row(g.user.id, did))), 201


@bp.post("/briefing")
@auth()
def make_briefing():
    period = (request.get_json(silent=True) or {}).get("period") or "today"
    if period not in briefing.PERIODS:
        raise ApiError(400, "Unknown period.", "invalid_field")
    enforce_rate("workspace-briefing", 10, 3600, key=g.user.id)
    data = briefing.gather(g.user.id, period)
    return jsonify(summary=briefing.summarize(g.user.id, data), facts=data["text"], errors=data["errors"], counts=data["counts"])


# ------------------------------------------------------------------ approvals and activity
@bp.get("/approvals")
@auth()
def list_approvals():
    status_ = request.args.get("status", "pending")
    if status_ not in ("pending", "sent", "failed", "discarded", "all"):
        raise ApiError(400, "Unknown status.", "invalid_field")
    return jsonify(approvals.listing(g.user.id, status_))


@bp.patch("/approvals/<did>")
@auth()
def edit_approval(did):
    return jsonify(approvals.edit(g.user.id, did, request.get_json(silent=True) or {}))


@bp.post("/approvals/<did>/approve")
@auth()
def approve(did):
    enforce_rate("workspace-approve", 30, 60, key=g.user.id)
    out = approvals.decide(g.user.id, did, True)
    audit("workspace.approved", target_type="draft", target_id=did, kind=out["kind"], status=out["status"])
    return jsonify(out)


@bp.post("/approvals/<did>/retry")
@auth()
def retry(did):
    enforce_rate("workspace-approve", 30, 60, key=g.user.id)
    out = approvals.decide(g.user.id, did, True, retry=True)
    audit("workspace.retried", target_type="draft", target_id=did, kind=out["kind"], status=out["status"])
    return jsonify(out)


@bp.post("/approvals/<did>/discard")
@auth()
def discard(did):
    """Reject a pending item, or cancel a failed one so it is not retried."""
    failed = approvals._row(g.user.id, did).status == "failed"
    return jsonify(approvals.decide(g.user.id, did, False, retry=failed))


@bp.get("/activity")
@auth()
def activity():
    with db.engine.connect() as c:
        rows = c.execute(select(db.workspace_activity).where(db.workspace_activity.c.user_id == g.user.id)
                         .order_by(db.workspace_activity.c.created_at.desc()).limit(200)).all()
    return jsonify([{"id": r.id, "kind": r.kind, "summary": r.summary, "provider": r.provider, "ref": r.ref, "status": r.status,
                     "created_at": r.created_at} for r in rows])


# ------------------------------------------------------------------ leave
@bp.get("/absences")
@auth()
def absences():
    return jsonify(leave.listing(g.user.id, request.args.get("start"), request.args.get("end"), request.args.get("status") or None))


@bp.post("/absences")
@auth()
def add_absence():
    d = request.get_json(silent=True) or {}
    rec, created = leave.record(g.user.id, d.get("person_email"), d.get("start_date"), d.get("end_date"), d.get("kind") or "announcement",
                                d.get("person_name"), "manual", None, "confirmed")
    if created:
        leave.maybe_auto_ooo(g.user.id, rec)
    return jsonify(rec), 201 if created else 200


@bp.patch("/absences/<aid>")
@auth()
def update_absence(aid):
    d = request.get_json(silent=True) or {}
    return jsonify(leave.set_status(g.user.id, aid, d.get("status"), d.get("kind")))


@bp.delete("/absences/<aid>")
@auth()
def delete_absence(aid):
    with db.engine.begin() as c:
        n = c.execute(delete(db.absences).where(db.absences.c.id == aid, db.absences.c.user_id == g.user.id)).rowcount
    if not n:
        raise ApiError(404, "Absence not found.", "not_found")
    return jsonify(ok=True)


@bp.post("/leave/scan")
@auth()
def leave_scan():
    enforce_rate("leave-scan", 6, 3600, key=g.user.id)
    days = (request.get_json(silent=True) or {}).get("days") or 7
    return jsonify(found=_tool(leave.scan, g.user.id, max(1, min(30, int(days)))))


@bp.get("/absences/<aid>/affected")
@auth()
def affected(aid):
    tz = common.tz_of(common.prefs(g.user.id))
    return jsonify([_ev(e, tz) for e in _tool(leave.affected_meetings, g.user.id, leave.get(g.user.id, aid))])


@bp.post("/absences/<aid>/notices")
@auth()
def notices(aid):
    ids = _tool(leave.prepare_notices, g.user.id, aid)
    return jsonify(created=len(ids), ids=ids)


# ------------------------------------------------------------------ out of office
@bp.get("/ooo")
@auth()
def get_ooo():
    return jsonify(ooo.get(g.user.id))


@bp.put("/ooo")
@auth()
def put_ooo():
    return jsonify(ooo.save(g.user.id, request.get_json(silent=True) or {}))


@bp.post("/ooo/preview")
@auth()
def preview_ooo():
    return jsonify(ooo.preview(g.user.id))


@bp.post("/ooo/test")
@auth()
def test_ooo():
    enforce_rate("ooo-test", 5, 3600, key=g.user.id)
    return jsonify(sent_to=_tool(ooo.send_test, g.user.id))


@bp.post("/ooo/enable")
@auth()
def enable_ooo():
    """The user pressed "Turn on" after seeing the preview: this is their approval."""
    msg = _tool(ooo.apply, g.user.id, True)
    common.log_activity(g.user.id, "ooo_enable", msg + " [user]")
    audit("workspace.ooo_enabled", target_type="setting", target_id="ooo")
    return jsonify(message=msg, ooo=ooo.get(g.user.id))


@bp.post("/ooo/disable")
@auth()
def disable_ooo():
    msg = _tool(ooo.apply, g.user.id, False)
    common.log_activity(g.user.id, "ooo_disable", msg + " [user]")
    return jsonify(message=msg, ooo=ooo.get(g.user.id))


# ------------------------------------------------------------------ action items and reminders
def _item(r):
    return {"id": r.id, "title": r.title, "owner": r.owner, "due_at": r.due_at, "source": r.source, "source_ref": r.source_ref,
            "status": r.status, "created_at": r.created_at, "done_at": r.done_at}


@bp.get("/action-items")
@auth()
def list_items():
    st = request.args.get("status", "open")
    q = select(db.action_items).where(db.action_items.c.user_id == g.user.id)
    if st in ("open", "done"):
        q = q.where(db.action_items.c.status == st)
    with db.engine.connect() as c:
        rows = c.execute(q.order_by(db.action_items.c.due_at.is_(None), db.action_items.c.due_at, db.action_items.c.created_at.desc())
                         .limit(200)).all()
    return jsonify([_item(r) for r in rows])


@bp.post("/action-items")
@auth()
def add_item():
    d = parse(request, {"title": Str(min=1, max=500, required=True, label="Title"), "owner": Str(max=255), "due": Str(max=40)})
    due = _tool(common.parse_dt, d.get("due"), common.tz_of(common.prefs(g.user.id)))
    iid = str(uuid.uuid4())
    with db.engine.begin() as c:
        c.execute(insert(db.action_items).values(id=iid, user_id=g.user.id, title=d["title"], owner=d.get("owner") or None,
                                                 due_at=int(due.timestamp() * 1e6) if due else None, source="manual", status="open",
                                                 created_at=db.now_us()))
        return jsonify(_item(c.execute(select(db.action_items).where(db.action_items.c.id == iid)).first())), 201


@bp.patch("/action-items/<iid>")
@auth()
def update_item(iid):
    d = request.get_json(silent=True) or {}
    vals = {}
    if d.get("status") in ("open", "done"):
        vals.update(status=d["status"], done_at=db.now_us() if d["status"] == "done" else None)
    if isinstance(d.get("title"), str) and d["title"].strip():
        vals["title"] = d["title"].strip()[:500]
    with db.engine.begin() as c:
        n = c.execute(update(db.action_items).where(db.action_items.c.id == iid, db.action_items.c.user_id == g.user.id).values(**vals)).rowcount \
            if vals else 1
        row = c.execute(select(db.action_items).where(db.action_items.c.id == iid, db.action_items.c.user_id == g.user.id)).first()
    if not n or not row:
        raise ApiError(404, "Action item not found.", "not_found")
    return jsonify(_item(row))


@bp.delete("/action-items/<iid>")
@auth()
def delete_item(iid):
    with db.engine.begin() as c:
        n = c.execute(delete(db.action_items).where(db.action_items.c.id == iid, db.action_items.c.user_id == g.user.id)).rowcount
    if not n:
        raise ApiError(404, "Action item not found.", "not_found")
    return jsonify(ok=True)


@bp.get("/reminders")
@auth()
def list_reminders():
    with db.engine.connect() as c:
        rows = c.execute(select(db.reminders).where(db.reminders.c.user_id == g.user.id, db.reminders.c.status == "pending")
                         .order_by(db.reminders.c.remind_at)).all()
    return jsonify([{"id": r.id, "text": r.text, "remind_at": r.remind_at, "kind": r.kind,
                     "ref": json.loads(r.ref) if r.ref else None} for r in rows])


@bp.post("/reminders")
@auth()
def add_reminder():
    d = parse(request, {"text": Str(min=1, max=1000, required=True, label="Reminder"), "at": Str(min=1, max=40, required=True, label="Time")})
    at = _tool(common.parse_dt, d["at"], common.tz_of(common.prefs(g.user.id)))
    if at < common.now_utc() - timedelta(minutes=1):
        raise ApiError(400, "That time is in the past.", "invalid_field")
    rid = str(uuid.uuid4())
    with db.engine.begin() as c:
        c.execute(insert(db.reminders).values(id=rid, user_id=g.user.id, text=d["text"], remind_at=int(at.timestamp() * 1e6),
                                              kind="reminder", status="pending", created_at=db.now_us()))
    return jsonify(id=rid), 201


@bp.delete("/reminders/<rid>")
@auth()
def cancel_reminder(rid):
    with db.engine.begin() as c:
        n = c.execute(update(db.reminders).where(db.reminders.c.id == rid, db.reminders.c.user_id == g.user.id,
                                                 db.reminders.c.status == "pending").values(status="cancelled", done_at=db.now_us())).rowcount
    if not n:
        raise ApiError(404, "Reminder not found.", "not_found")
    return jsonify(ok=True)


# ------------------------------------------------------------------ notifications and jobs
@bp.get("/notifications")
@auth()
def list_notifications():
    return jsonify(notify.listing(g.user.id, request.args.get("unread") == "1"))


@bp.post("/notifications/read")
@auth()
def read_notifications():
    nid = (request.get_json(silent=True) or {}).get("id")
    return jsonify(updated=notify.mark_read(g.user.id, nid))


@bp.get("/jobs")
@auth()
def list_jobs():
    return jsonify(jobs.listing(g.user.id))


@bp.put("/jobs/<kind>")
@auth()
def put_job(kind):
    d = request.get_json(silent=True) or {}
    out = _tool(jobs.configure, g.user.id, kind, d.get("enabled"), d.get("time"), d.get("days"))
    audit("workspace.job_changed", target_type="job", target_id=kind, enabled=out["enabled"])
    return jsonify(out)


@bp.post("/jobs/<kind>/run")
@auth()
def run_job(kind):
    enforce_rate("job-run", 10, 600, key=g.user.id)
    if kind not in jobs.KINDS:
        raise ApiError(404, "Unknown job.", "not_found")
    return jsonify(outcome=_tool(jobs.run_now, g.user.id, kind))
