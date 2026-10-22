"""Shared helpers for the workspace assistant: preferences, time zones, activity history, duplicate protection."""
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import insert, select, update

from .. import db, tools
from ..errors import ApiError


class InputError(tools.ToolError):
    """A problem with what the user asked for (shown as a 400), as opposed to a provider failure."""


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MODES = ("assisted", "approval", "policy")
CHANNELS = ("app", "email", "whatsapp", "teams")
# Low-risk actions a user may let run without asking (only in "approval" or "policy" mode).
AUTO_ACTIONS = {"email_draft": "Create Gmail drafts (never sends)", "email_label": "Add labels to emails",
                "email_mark_read": "Mark emails read or unread"}
DEFAULT_PREFS = {
    "timezone": "", "working_hours": {"start": "09:00", "end": "18:00"}, "working_days": DAYS[:5], "holidays": [],
    "quiet_hours": {"start": "21:00", "end": "07:00"}, "mode": "assisted", "paused": False, "auto_actions": [],
    "policies": {"ooo_on_own_absence": False, "ooo_internal": "", "ooo_external": "", "ooo_contact": ""},
    "notify_channels": ["app"], "default_calendar": "", "followup_days": 3, "reminder_minutes": 15,
}
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
DATE = re.compile(r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


# ------------------------------------------------------------------ preferences
def prefs(uid):
    with db.engine.connect() as c:
        row = c.execute(select(db.workspace_prefs.c.prefs).where(db.workspace_prefs.c.user_id == uid)).first()
    saved = json.loads(row.prefs) if row else {}
    out = json.loads(json.dumps(DEFAULT_PREFS))
    for k, v in saved.items():
        if k in out:
            out[k] = {**out[k], **v} if isinstance(out[k], dict) and isinstance(v, dict) else v
    return out


def validate_prefs(changes):
    """Check a partial update. Raises ApiError with a clear message."""
    if not isinstance(changes, dict):
        raise ApiError(400, "Settings must be an object.", "invalid_field")
    out = {}
    for k, v in changes.items():
        if k not in DEFAULT_PREFS:
            continue
        if k == "timezone":
            if v:
                try:
                    ZoneInfo(str(v))
                except (ZoneInfoNotFoundError, ValueError):
                    raise ApiError(400, f"Unknown time zone '{v}'.", "invalid_field")
            out[k] = str(v or "")
        elif k in ("working_hours", "quiet_hours"):
            if not isinstance(v, dict) or not HHMM.fullmatch(str(v.get("start", ""))) or not HHMM.fullmatch(str(v.get("end", ""))):
                raise ApiError(400, "Times must look like 09:00.", "invalid_field")
            out[k] = {"start": v["start"], "end": v["end"]}
        elif k == "working_days":
            if not isinstance(v, list) or not v or any(d not in DAYS for d in v):
                raise ApiError(400, "Working days must be a list like mon, tue …", "invalid_field")
            out[k] = [d for d in DAYS if d in v]
        elif k == "holidays":
            if not isinstance(v, list) or len(v) > 100:
                raise ApiError(400, "Holidays must be a list of dates.", "invalid_field")
            try:
                out[k] = sorted({date.fromisoformat(str(d)).isoformat() for d in v})
            except ValueError:
                raise ApiError(400, "Holidays must look like 2026-12-25.", "invalid_field")
        elif k == "mode":
            if v not in MODES:
                raise ApiError(400, "Mode must be assisted, approval or policy.", "invalid_field")
            out[k] = v
        elif k == "paused":
            out[k] = bool(v)
        elif k == "auto_actions":
            if not isinstance(v, list) or any(a not in AUTO_ACTIONS for a in v):
                raise ApiError(400, "Unknown automatic action.", "invalid_field")
            out[k] = sorted(set(v))
        elif k == "policies":
            if not isinstance(v, dict):
                raise ApiError(400, "Policies must be an object.", "invalid_field")
            contact = str(v.get("ooo_contact") or "").strip()
            if contact and not EMAIL.fullmatch(contact):
                raise ApiError(400, "The alternate contact must be an email address.", "invalid_field")
            out[k] = {"ooo_on_own_absence": bool(v.get("ooo_on_own_absence")), "ooo_internal": str(v.get("ooo_internal") or "")[:2000],
                      "ooo_external": str(v.get("ooo_external") or "")[:2000], "ooo_contact": contact}
        elif k == "notify_channels":
            if not isinstance(v, list) or any(ch not in CHANNELS for ch in v):
                raise ApiError(400, "Unknown notification channel.", "invalid_field")
            out[k] = sorted(set(v) | {"app"})
        elif k == "default_calendar":
            if v not in ("", "google", "microsoft"):
                raise ApiError(400, "Default calendar must be google or microsoft.", "invalid_field")
            out[k] = v
        elif k in ("followup_days", "reminder_minutes"):
            if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= (60 if k == "followup_days" else 240)):
                raise ApiError(400, f"{k} is out of range.", "invalid_field")
            out[k] = v
    return out


def save_prefs(uid, changes):
    current = prefs(uid)
    current.update(validate_prefs(changes))
    now = db.now_us()
    with db.engine.begin() as c:
        if c.execute(select(db.workspace_prefs.c.user_id).where(db.workspace_prefs.c.user_id == uid)).first():
            c.execute(update(db.workspace_prefs).where(db.workspace_prefs.c.user_id == uid)
                      .values(prefs=json.dumps(current), updated_at=now))
        else:
            c.execute(insert(db.workspace_prefs).values(user_id=uid, prefs=json.dumps(current), updated_at=now))
    return current


# ------------------------------------------------------------------ time
def tz_of(p):
    try:
        return ZoneInfo(p.get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def parse_dt(value, tz, default=None):
    """ISO date-time from the model or the UI. A time without an offset is taken in the user's time zone."""
    if value in (None, ""):
        return default
    s = str(value).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.combine(date.fromisoformat(s), datetime.min.time())
        except ValueError:
            raise tools.ToolError("Dates must look like 2026-10-23T15:00 (or 2026-10-23).")
    return d.replace(tzinfo=tz) if d.tzinfo is None else d


def fmt(dt, tz):
    local = dt.astimezone(tz)
    return local.strftime("%a %d %b %Y, %H:%M") + f" ({tz.key})"


def now_utc():
    return datetime.now(timezone.utc)


def day_bounds(day, tz):
    start = datetime.combine(day, datetime.min.time(), tz)
    return start, start + timedelta(days=1)


def emails_in(value):
    if isinstance(value, list):
        value = ",".join(str(x) for x in value)
    return list(dict.fromkeys(e.lower() for e in EMAIL.findall(str(value or ""))))[:50]


# ------------------------------------------------------------------ activity & duplicates
def idem_key(*parts):
    norm = "|".join(re.sub(r"\s+", " ", str(p or "")).strip().lower() for p in parts)
    return hashlib.sha256(norm.encode()).hexdigest()


def already_done(uid, key, hours=24):
    since = db.now_us() - hours * 3600 * 10**6
    with db.engine.connect() as c:
        return c.execute(select(db.workspace_activity).where(
            db.workspace_activity.c.user_id == uid, db.workspace_activity.c.idem_key == key,
            db.workspace_activity.c.status == "done", db.workspace_activity.c.created_at > since)).first()


def log_activity(uid, kind, summary, status="done", provider=None, ref=None, key=None):
    with db.engine.begin() as c:
        c.execute(insert(db.workspace_activity).values(
            id=str(uuid.uuid4()), user_id=uid, kind=kind, summary=summary[:2000], provider=provider,
            ref=(ref or "")[:500] or None, status=status, idem_key=key, created_at=db.now_us()))


def connected(uid):
    from ..config import settings
    from ..integrations import google
    from . import microsoft
    return {"google": bool(settings.GOOGLE_ENABLED and google.connection(uid)),
            "microsoft": bool(settings.MS_ENABLED and microsoft.connection(uid))}
