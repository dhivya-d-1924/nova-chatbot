"""Scheduled workspace jobs (briefings, reminders, follow-ups, summaries) with a database-backed runner.

Why not Celery: the app already has a reliable database and an optional Redis; a small runner keeps deployment simple
(one container) while giving the same guarantees that matter here:
- each job row is leased before it runs, so several web workers or servers never run the same job twice;
- every alert has a dedupe key (see notify.py), so a retry never sends a duplicate;
- failures retry with backoff (1, 2, 4 … up to 60 minutes) and then fall back to the next scheduled time;
- times follow each user's time zone, working days and quiet hours; "Pause all automation" stops delivery.
Run it inside the web app (default) or from cron:  flask --app app workspace-jobs --run-due
"""
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from .. import db, tools
from ..config import settings
from . import briefing, calendar, common, gmail, notify

log = logging.getLogger("nova.workspace.jobs")
LEASE_MINUTES = 5
MAX_ATTEMPTS = 5
KINDS = {
    "reminders": {"label": "Your reminders and follow-up checks", "every": 1, "enabled": True},
    "meeting_reminders": {"label": "Reminder before each meeting", "every": 5, "enabled": True},
    "morning_briefing": {"label": "Morning briefing", "time": "08:30", "days": "work", "enabled": True},
    "email_digest": {"label": "Important email summary", "time": "16:30", "days": "work", "enabled": False},
    "followups": {"label": "Unanswered email follow-ups", "time": "10:00", "days": "work", "enabled": False},
    "action_items": {"label": "Action items due", "time": "09:00", "days": "work", "enabled": False},
    "conflict_alerts": {"label": "Meeting conflict alerts", "time": "08:00", "days": "work", "enabled": False},
    "eod_summary": {"label": "End-of-day summary", "time": "18:00", "days": "work", "enabled": False},
    "weekly_summary": {"label": "Weekly summary", "time": "17:00", "days": "fri", "enabled": False},
}
_started = threading.Event()


# ------------------------------------------------------------------ schedule
def _config(kind, row_config):
    base = {k: v for k, v in KINDS[kind].items() if k in ("time", "days", "every")}
    base.update(json.loads(row_config or "{}"))
    return base


def next_run(uid, kind, config, after=None):
    after = after or common.now_utc()
    if config.get("every"):
        return int((after + timedelta(minutes=int(config["every"]))).timestamp() * 1e6)
    p = common.prefs(uid)
    tz = common.tz_of(p)
    days = p["working_days"] if config.get("days", "work") == "work" else \
        [d.strip() for d in str(config.get("days")).split(",") if d.strip() in common.DAYS] or common.DAYS
    hh, mm = (int(x) for x in config.get("time", "08:30").split(":"))
    local = after.astimezone(tz)
    for i in range(0, 15):
        day = local.date() + timedelta(days=i)
        cand = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        if cand > local and common.DAYS[day.weekday()] in days and day.isoformat() not in p["holidays"]:
            return int(cand.timestamp() * 1e6)
    return int((after + timedelta(days=1)).timestamp() * 1e6)


def ensure(uid):
    """Create the user's job rows with defaults (idempotent)."""
    with db.engine.connect() as c:
        have = {r.kind for r in c.execute(select(db.jobs.c.kind).where(db.jobs.c.user_id == uid))}
    for kind, spec in KINDS.items():
        if kind in have:
            continue
        try:
            with db.engine.begin() as c:
                c.execute(insert(db.jobs).values(id=str(uuid.uuid4()), user_id=uid, kind=kind, enabled=1 if spec["enabled"] else 0,
                                                 config="{}", next_run_at=next_run(uid, kind, _config(kind, "{}")), attempts=0,
                                                 created_at=db.now_us()))
        except IntegrityError:
            pass


def listing(uid):
    ensure(uid)
    with db.engine.connect() as c:
        rows = {r.kind: r for r in c.execute(select(db.jobs).where(db.jobs.c.user_id == uid))}
    return [{"kind": k, "label": spec["label"], "enabled": bool(rows[k].enabled), "config": _config(k, rows[k].config),
             "next_run_at": rows[k].next_run_at if rows[k].enabled else None, "last_run_at": rows[k].last_run_at,
             "last_status": rows[k].last_status, "last_error": rows[k].last_error}
            for k, spec in KINDS.items() if k in rows]


def configure(uid, kind, enabled=None, time_=None, days=None):
    if kind not in KINDS:
        raise tools.ToolError("Unknown job.")
    ensure(uid)
    with db.engine.connect() as c:
        row = c.execute(select(db.jobs).where(db.jobs.c.user_id == uid, db.jobs.c.kind == kind)).first()
    cfg = json.loads(row.config or "{}")
    if time_ is not None and "time" in KINDS[kind]:
        if not common.HHMM.fullmatch(time_):
            raise tools.ToolError("Time must look like 08:30.")
        cfg["time"] = time_
    if days is not None and "days" in KINDS[kind]:
        if days != "work" and any(d.strip() not in common.DAYS for d in days.split(",")):
            raise tools.ToolError("Days must be 'work' or a list like mon,wed,fri.")
        cfg["days"] = days
    vals = {"config": json.dumps(cfg)}
    if enabled is not None:
        vals["enabled"] = 1 if enabled else 0
    vals["next_run_at"] = next_run(uid, kind, _config(kind, json.dumps(cfg)))
    vals["attempts"] = 0
    with db.engine.begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == row.id).values(**vals))
    return next(j for j in listing(uid) if j["kind"] == kind)


# ------------------------------------------------------------------ handlers
def _today_key(uid):
    return common.now_utc().astimezone(common.tz_of(common.prefs(uid))).date().isoformat()


def job_reminders(uid):
    now = db.now_us()
    with db.engine.connect() as c:
        due = c.execute(select(db.reminders).where(db.reminders.c.user_id == uid, db.reminders.c.status == "pending",
                                                   db.reminders.c.remind_at <= now).limit(50)).all()
    sent = 0
    for r in due:
        status = "sent"
        if r.kind == "followup":
            ref = json.loads(r.ref or "{}")
            replied = False
            if ref.get("provider") == "gmail" and ref.get("thread_id") and common.connected(uid)["google"]:
                try:
                    replied = gmail.thread_has_reply_after(uid, ref["thread_id"], r.created_at)
                except tools.ToolError as e:
                    log.info("follow-up check failed: %s", e)
            if replied:
                status = "resolved"
                notify.create(uid, "followup", "They replied", f"You got a reply about: {r.text}", f"followup-ok:{r.id}")
            else:
                notify.create(uid, "followup", "No reply yet — follow up?", r.text, f"followup:{r.id}")
        else:
            notify.create(uid, "reminder", "Reminder", r.text, f"reminder:{r.id}", urgent=True)
        with db.engine.begin() as c:
            c.execute(update(db.reminders).where(db.reminders.c.id == r.id).values(status=status, done_at=db.now_us()))
        sent += 1
    notify.deliver_pending(uid)
    return f"{sent} reminder(s)"


def job_meeting_reminders(uid):
    if not any(common.connected(uid).values()):
        return "no calendar"
    p = common.prefs(uid)
    tz = common.tz_of(p)
    now = common.now_utc()
    ahead = timedelta(minutes=p["reminder_minutes"])
    evts, _ = calendar.events(uid, now, now + ahead + timedelta(minutes=6))
    n = 0
    for e in evts:
        if e["all_day"] or e["start"] < now or e["start"] > now + ahead + timedelta(minutes=5):
            continue
        mins = max(0, int((e["start"] - now).total_seconds() // 60))
        body = calendar.describe(e, tz)
        if notify.create(uid, "meeting", f"In {mins} min: {e['title']}", body, f"meet:{e['provider']}:{e['id']}:{e['start'].isoformat()}",
                         urgent=True):
            n += 1
    return f"{n} meeting reminder(s)"


def _summary_job(uid, period, focus, kind, title):
    data = briefing.gather(uid, period)
    if not any(data["counts"].get(k) for k in ("meetings", "emails", "teams", "action_items", "approvals")) and period != "last_week":
        return "nothing to report"
    text = briefing.summarize(uid, data, focus)
    notify.create(uid, kind, title, text, f"{kind}:{_today_key(uid)}")
    return "sent"


def job_morning_briefing(uid):
    return _summary_job(uid, "today", "morning briefing", "briefing", "Your day")


def job_email_digest(uid):
    if not common.connected(uid)["google"]:
        return "Gmail not connected"
    return _summary_job(uid, "since_yesterday", "important-email digest", "email_digest", "Important email")


def job_eod_summary(uid):
    return _summary_job(uid, "today", "end-of-day summary (done, still open, tomorrow's first meetings)", "eod", "End of day")


def job_weekly_summary(uid):
    return _summary_job(uid, "last_week", "weekly summary", "weekly", "Your week")


def job_followups(uid):
    if not common.connected(uid)["google"]:
        return "Gmail not connected"
    p = common.prefs(uid)
    threads = gmail.unanswered(uid, p["followup_days"], 10)
    if not threads:
        return "nothing waiting"
    body = "\n".join(f"- {t['from']}: {t['subject']} ({t['date']})" for t in threads)
    notify.create(uid, "followups", f"{len(threads)} email(s) waiting for your reply", body, f"followups:{_today_key(uid)}")
    return f"{len(threads)} waiting"


def job_action_items(uid):
    tz = common.tz_of(common.prefs(uid))
    end_of_day = int(common.day_bounds(common.now_utc().astimezone(tz).date(), tz)[1].timestamp() * 1e6)
    with db.engine.connect() as c:
        items = c.execute(select(db.action_items).where(db.action_items.c.user_id == uid, db.action_items.c.status == "open",
                                                        db.action_items.c.due_at.isnot(None), db.action_items.c.due_at < end_of_day)).all()
    if not items:
        return "nothing due"
    body = "\n".join(f"- {i.title}" + (f" (owner {i.owner})" if i.owner else "") for i in items)
    notify.create(uid, "action_items", f"{len(items)} action item(s) due", body, f"items:{_today_key(uid)}")
    return f"{len(items)} due"


def job_conflict_alerts(uid):
    if not any(common.connected(uid).values()):
        return "no calendar"
    tz = common.tz_of(common.prefs(uid))
    now = common.now_utc()
    evts, _ = calendar.events(uid, now, now + timedelta(days=2))
    n = 0
    for a, b in calendar.conflicts(evts):
        if notify.create(uid, "conflict", "Meeting conflict", f"“{a['title']}” and “{b['title']}” overlap on "
                         f"{b['start'].astimezone(tz).strftime('%a %d %b %H:%M')}.", f"conflict:{a['id']}:{b['id']}"):
            n += 1
    return f"{n} conflict(s)"


HANDLERS = {"reminders": job_reminders, "meeting_reminders": job_meeting_reminders, "morning_briefing": job_morning_briefing,
            "email_digest": job_email_digest, "followups": job_followups, "action_items": job_action_items,
            "conflict_alerts": job_conflict_alerts, "eod_summary": job_eod_summary, "weekly_summary": job_weekly_summary}


# ------------------------------------------------------------------ runner
def _claim(job_id, worker):
    now = db.now_us()
    with db.engine.begin() as c:
        return c.execute(update(db.jobs).where(db.jobs.c.id == job_id, or_(db.jobs.c.lease_until.is_(None), db.jobs.c.lease_until < now))
                         .values(lease_owner=worker, lease_until=now + LEASE_MINUTES * 60 * 10**6)).rowcount == 1


def run_job(row, worker="manual", force=False):
    """Run one job row (already claimed, or force=True for "run now"). Records the outcome and the next run time."""
    with db.engine.connect() as c:
        user = c.execute(select(db.users.c.disabled).where(db.users.c.id == row.user_id)).first()
    p = common.prefs(row.user_id)
    cfg = _config(row.kind, row.config)
    vals = {"lease_owner": None, "lease_until": None, "last_run_at": db.now_us()}
    try:
        if not user or user.disabled:
            outcome = "skipped: account disabled"
        elif p["paused"] and not force:
            outcome = "skipped: automation paused"
        else:
            outcome = HANDLERS[row.kind](row.user_id)
        vals.update(last_status="ok", last_error=outcome[:300], attempts=0, next_run_at=next_run(row.user_id, row.kind, cfg))
    except Exception as e:  # noqa: BLE001
        attempts = (row.attempts or 0) + 1
        log.warning("job %s for %s failed (attempt %s): %s", row.kind, row.user_id, attempts, e)
        retry = common.now_utc() + timedelta(minutes=min(2 ** (attempts - 1), 60))
        vals.update(last_status="error", last_error=str(e)[:500], attempts=0 if attempts >= MAX_ATTEMPTS else attempts,
                    next_run_at=next_run(row.user_id, row.kind, cfg) if attempts >= MAX_ATTEMPTS else int(retry.timestamp() * 1e6))
        outcome = f"error: {e}"
    with db.engine.begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == row.id).values(**vals))
    return outcome


def run_due(worker=None, limit=50):
    worker = worker or uuid.uuid4().hex
    now = db.now_us()
    with db.engine.connect() as c:
        due = c.execute(select(db.jobs).where(db.jobs.c.enabled == 1, db.jobs.c.next_run_at <= now,
                                              or_(db.jobs.c.lease_until.is_(None), db.jobs.c.lease_until < now))
                        .order_by(db.jobs.c.next_run_at).limit(limit)).all()
    ran = 0
    for row in due:
        if _claim(row.id, worker):
            run_job(row, worker)
            ran += 1
    return ran


def run_now(uid, kind):
    ensure(uid)
    with db.engine.connect() as c:
        row = c.execute(select(db.jobs).where(db.jobs.c.user_id == uid, db.jobs.c.kind == kind)).first()
    if not row:
        raise tools.ToolError("Unknown job.")
    if not _claim(row.id, "manual"):
        raise tools.ToolError("This job is running right now. Try again in a moment.")
    return run_job(row, "manual", force=True)


def start(app):
    if not settings.WORKSPACE_JOBS or settings.IS_TEST or _started.is_set():
        return
    _started.set()
    worker = uuid.uuid4().hex

    def loop():
        with app.app_context():
            while True:
                try:
                    run_due(worker)
                except Exception:  # noqa: BLE001
                    log.exception("workspace job runner failed")
                time.sleep(settings.WORKSPACE_JOB_INTERVAL)

    threading.Thread(target=loop, daemon=True, name="workspace-jobs").start()
    log.info("workspace jobs: runner started (every %ss)", settings.WORKSPACE_JOB_INTERVAL)
