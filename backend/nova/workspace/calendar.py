"""One calendar view over Google Calendar and Outlook (Microsoft Graph).

Everything is normalised to UTC internally and shown in the user's time zone. Writes return the provider's own
record of the event, so the assistant only reports what the provider confirmed.
"""
import re
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

from .. import tools
from ..integrations import google
from . import common, microsoft

EVENT_ID = re.compile(r"[A-Za-z0-9=_\-+/]{1,500}")
STEP = timedelta(minutes=15)


def _q(v):
    return urllib.parse.quote(v, safe="")


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ms_time(v):
    """Graph returns naive times in the zone we asked for (UTC)."""
    s = (v or {}).get("dateTime", "")
    if not s:
        return None
    d = datetime.fromisoformat(s[:26].rstrip("Z"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def _g_time(v):
    if not v:
        return None, False
    if v.get("dateTime"):
        return datetime.fromisoformat(v["dateTime"].replace("Z", "+00:00")), False
    d = datetime.fromisoformat(v["date"])
    return d.replace(tzinfo=timezone.utc), True


def _norm_google(e):
    start, all_day = _g_time(e.get("start"))
    end, _ = _g_time(e.get("end"))
    link = e.get("hangoutLink") or next((p.get("uri") for p in (e.get("conferenceData") or {}).get("entryPoints", [])
                                         if p.get("entryPointType") == "video"), None)
    return {"provider": "google", "id": e.get("id"), "title": e.get("summary") or "(no title)", "start": start, "end": end,
            "all_day": all_day, "location": e.get("location") or "", "join_url": link, "web_link": e.get("htmlLink"),
            "organizer": (e.get("organizer") or {}).get("email", ""),
            "attendees": [{"email": (a.get("email") or "").lower(), "name": a.get("displayName") or "",
                           "response": a.get("responseStatus", "")} for a in e.get("attendees") or []],
            "status": e.get("status", "confirmed"), "busy": e.get("transparency") != "transparent",
            "recurring": bool(e.get("recurringEventId"))}


def _norm_ms(e):
    return {"provider": "microsoft", "id": e.get("id"), "title": e.get("subject") or "(no title)",
            "start": _ms_time(e.get("start")), "end": _ms_time(e.get("end")), "all_day": bool(e.get("isAllDay")),
            "location": (e.get("location") or {}).get("displayName", ""),
            "join_url": (e.get("onlineMeeting") or {}).get("joinUrl"), "web_link": e.get("webLink"),
            "organizer": ((e.get("organizer") or {}).get("emailAddress") or {}).get("address", "").lower(),
            "attendees": [{"email": ((a.get("emailAddress") or {}).get("address") or "").lower(),
                           "name": (a.get("emailAddress") or {}).get("name", ""),
                           "response": (a.get("status") or {}).get("response", "")} for a in e.get("attendees") or []],
            "status": "cancelled" if e.get("isCancelled") else ("tentative" if e.get("showAs") == "tentative" else "confirmed"),
            "busy": e.get("showAs") not in ("free",), "recurring": e.get("type") in ("occurrence", "exception")}


# ------------------------------------------------------------------ reading
def events(uid, start, end, provider=None):
    """All events between start and end from the connected calendars, sorted. Returns (events, errors)."""
    have = common.connected(uid)
    out, errors = [], []
    if have["google"] and provider in (None, "google"):
        try:
            res = google._api(uid, "GET", "/calendar/v3/calendars/primary/events", {
                "timeMin": _iso(start), "timeMax": _iso(end), "singleEvents": "true", "orderBy": "startTime", "maxResults": 250})
            out += [_norm_google(e) for e in res.get("items", []) if e.get("status") != "cancelled"]
        except tools.ToolError as e:
            errors.append(f"Google Calendar: {e}")
    if have["microsoft"] and provider in (None, "microsoft"):
        try:
            res = microsoft.graph(uid, "GET", "/me/calendarView", params={
                "startDateTime": _iso(start), "endDateTime": _iso(end), "$top": "250", "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,isAllDay,location,onlineMeeting,webLink,organizer,attendees,isCancelled,showAs,type"},
                headers={"Prefer": 'outlook.timezone="UTC"'})
            out += [_norm_ms(e) for e in res.get("value", []) if not e.get("isCancelled")]
        except tools.ToolError as e:
            errors.append(f"Outlook Calendar: {e}")
    if not have["google"] and not have["microsoft"]:
        raise tools.ToolError("No calendar is connected. Connect Google or Microsoft 365 in Workspace > Accounts.")
    out = [e for e in out if e["start"] and e["end"]]
    out.sort(key=lambda e: e["start"])
    return out, errors


def conflicts(evts):
    """Pairs of busy, timed events that overlap."""
    timed = [e for e in evts if not e["all_day"] and e["busy"] and e["status"] != "cancelled"]
    found = []
    for i, a in enumerate(timed):
        for b in timed[i + 1:]:
            if b["start"] >= a["end"]:
                break
            if b["start"] < a["end"] and a["start"] < b["end"]:
                found.append((a, b))
    return found


def describe(e, tz):
    when = e["start"].astimezone(tz).strftime("%a %d %b") + " (all day)" if e["all_day"] else \
        f"{e['start'].astimezone(tz).strftime('%a %d %b %H:%M')}–{e['end'].astimezone(tz).strftime('%H:%M')}"
    people = ", ".join(a["email"] for a in e["attendees"][:8]) + (" …" if len(e["attendees"]) > 8 else "")
    parts = [f"{when} · {e['title']}", f"[{e['provider']} id {e['id']}]"]
    if e["location"]:
        parts.append(f"where: {e['location']}")
    if e["join_url"]:
        parts.append(f"join: {e['join_url']}")
    if people:
        parts.append(f"with: {people}")
    if e["status"] != "confirmed":
        parts.append(f"status: {e['status']}")
    return " · ".join(parts)


def free_busy(uid, emails, start, end):
    """Busy times per person. Returns {email: {"busy": [(start, end, status)], "known": bool, "note": str}}."""
    emails = common.emails_in(emails)
    result = {e: {"busy": [], "known": False, "note": "no calendar access"} for e in emails}
    if not emails:
        return result
    have = common.connected(uid)
    if have["microsoft"]:
        try:
            res = microsoft.graph(uid, "POST", "/me/calendar/getSchedule", {
                "schedules": emails, "startTime": {"dateTime": _iso(start)[:19], "timeZone": "UTC"},
                "endTime": {"dateTime": _iso(end)[:19], "timeZone": "UTC"}, "availabilityViewInterval": 15})
            for s in res.get("value", []):
                key = (s.get("scheduleId") or "").lower()
                if key in result and not s.get("error"):
                    result[key] = {"known": True, "note": "Outlook", "busy": [
                        (_ms_time(i.get("start")), _ms_time(i.get("end")), i.get("status", "busy"))
                        for i in s.get("scheduleItems", []) if i.get("status") in ("busy", "tentative", "oof")]}
        except tools.ToolError:
            pass
    if have["google"]:
        todo = [e for e in emails if not result[e]["known"]]
        if todo:
            try:
                res = google._api(uid, "POST", "/calendar/v3/freeBusy", body={
                    "timeMin": _iso(start), "timeMax": _iso(end), "items": [{"id": e} for e in todo]})
                for e, cal in (res.get("calendars") or {}).items():
                    e = e.lower()
                    if e in result and not cal.get("errors"):
                        result[e] = {"known": True, "note": "Google", "busy": [
                            (datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                             datetime.fromisoformat(b["end"].replace("Z", "+00:00")), "busy") for b in cal.get("busy", [])]}
            except tools.ToolError:
                pass
    return result


def _working(day, p, tz):
    if common.DAYS[day.weekday()] not in p["working_days"] or day.isoformat() in p["holidays"]:
        return None
    s = datetime.combine(day, datetime.strptime(p["working_hours"]["start"], "%H:%M").time(), tz)
    e = datetime.combine(day, datetime.strptime(p["working_hours"]["end"], "%H:%M").time(), tz)
    return (s, e) if e > s else None


def find_slots(uid, attendees, minutes, start=None, end=None, count=5):
    """Free slots inside working hours when the user and every attendee with known availability are free."""
    p = common.prefs(uid)
    tz = common.tz_of(p)
    now = common.now_utc()
    start = max(start or now, now)
    start = start + (STEP - timedelta(minutes=start.minute % 15, seconds=start.second, microseconds=start.microsecond)) % STEP
    end = end or start + timedelta(days=7)
    if end <= start:
        raise tools.ToolError("The search window must end after it starts.")
    if not 5 <= minutes <= 480:
        raise tools.ToolError("Meeting length must be between 5 and 480 minutes.")
    mine, _ = events(uid, start, end)
    busy = [(e["start"], e["end"]) for e in mine if e["busy"] and not e["all_day"]]
    others = free_busy(uid, attendees, start, end)
    for info in others.values():
        busy += [(s, e) for s, e, _ in info["busy"] if s and e]
    busy.sort()
    length = timedelta(minutes=minutes)
    slots, day = [], start.astimezone(tz).date()
    while day <= end.astimezone(tz).date() and len(slots) < count:
        hours = _working(day, p, tz)
        if hours:
            t = max(hours[0], start)
            while t + length <= min(hours[1], end) and len(slots) < count:
                clash = next((b for b in busy if b[0] < t + length and t < b[1]), None)
                if clash:
                    t = max(t + STEP, clash[1])
                    t = t + (STEP - timedelta(minutes=t.minute % 15, seconds=t.second, microseconds=t.microsecond)) % STEP
                    continue
                slots.append((t, t + length))
                t += max(length, timedelta(minutes=30))
        day += timedelta(days=1)
    unknown = [e for e, info in others.items() if not info["known"]]
    return slots, unknown, tz


# ------------------------------------------------------------------ writing (callers handle approval)
def pick_provider(uid, provider=None):
    have = common.connected(uid)
    if provider:
        if not have.get(provider):
            raise tools.ToolError(f"{'Google Calendar' if provider == 'google' else 'Outlook'} isn't connected.")
        return provider
    default = common.prefs(uid)["default_calendar"]
    if default and have.get(default):
        return default
    for p in ("microsoft", "google"):
        if have[p]:
            return p
    raise tools.ToolError("No calendar is connected. Connect Google or Microsoft 365 in Workspace > Accounts.")


def _check_id(event_id):
    if not EVENT_ID.fullmatch(str(event_id or "")):
        raise tools.ToolError("Use an event id from the calendar list.")
    return str(event_id)


def create_event(uid, title, start, end, attendees=(), description="", location="", online=True, provider=None):
    provider = pick_provider(uid, provider)
    if end <= start:
        raise tools.ToolError("The meeting must end after it starts.")
    people = common.emails_in(attendees)
    if provider == "google":
        body = {"summary": title[:300], "description": description[:8000], "location": location[:300],
                "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
                "attendees": [{"email": a} for a in people]}
        params = {"sendUpdates": "all" if people else "none"}
        if online:
            body["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
            params["conferenceDataVersion"] = 1
        ev = google._api(uid, "POST", "/calendar/v3/calendars/primary/events", params, body)
        if not ev.get("id"):
            raise tools.ToolError("Google didn't confirm the event; it was not created.")
        return _norm_google(ev)
    body = {"subject": title[:300], "body": {"contentType": "text", "content": description[:8000]},
            "start": {"dateTime": _iso(start)[:19], "timeZone": "UTC"}, "end": {"dateTime": _iso(end)[:19], "timeZone": "UTC"},
            "location": {"displayName": location[:300]},
            "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in people]}
    if online:
        body.update(isOnlineMeeting=True, onlineMeetingProvider="teamsForBusiness")
    ev = microsoft.graph(uid, "POST", "/me/events", body, headers={"Prefer": 'outlook.timezone="UTC"'})
    if not ev.get("id"):
        raise tools.ToolError("Outlook didn't confirm the event; it was not created.")
    return _norm_ms(ev)


def get_event(uid, provider, event_id):
    event_id = _check_id(event_id)
    if provider == "google":
        return _norm_google(google._api(uid, "GET", f"/calendar/v3/calendars/primary/events/{_q(event_id)}"))
    return _norm_ms(microsoft.graph(uid, "GET", f"/me/events/{_q(event_id)}", headers={"Prefer": 'outlook.timezone="UTC"'}))


def update_event(uid, provider, event_id, start=None, end=None, title=None, add_attendees=(), note=None):
    provider = pick_provider(uid, provider)
    event_id = _check_id(event_id)
    if start and end and end <= start:
        raise tools.ToolError("The meeting must end after it starts.")
    current = get_event(uid, provider, event_id)
    extra = [a for a in common.emails_in(add_attendees) if a not in {x["email"] for x in current["attendees"]}]
    if provider == "google":
        body = {}
        if start:
            body["start"] = {"dateTime": start.isoformat()}
        if end:
            body["end"] = {"dateTime": end.isoformat()}
        if title:
            body["summary"] = title[:300]
        if extra:
            body["attendees"] = [{"email": a["email"]} for a in current["attendees"]] + [{"email": a} for a in extra]
        if note:
            body["description"] = note[:8000]
        ev = google._api(uid, "PATCH", f"/calendar/v3/calendars/primary/events/{_q(event_id)}", {"sendUpdates": "all"}, body)
        return _norm_google(ev)
    body = {}
    if start:
        body["start"] = {"dateTime": _iso(start)[:19], "timeZone": "UTC"}
    if end:
        body["end"] = {"dateTime": _iso(end)[:19], "timeZone": "UTC"}
    if title:
        body["subject"] = title[:300]
    if extra:
        body["attendees"] = [{"emailAddress": {"address": a["email"]}, "type": "required"} for a in current["attendees"]] + \
            [{"emailAddress": {"address": a}, "type": "required"} for a in extra]
    if note:
        body["body"] = {"contentType": "text", "content": note[:8000]}
    ev = microsoft.graph(uid, "PATCH", f"/me/events/{_q(event_id)}", body, headers={"Prefer": 'outlook.timezone="UTC"'})
    return _norm_ms(ev)


def cancel_event(uid, provider, event_id, message=""):
    """Cancel (as organiser) or decline (as attendee). Returns a short description of what happened."""
    provider = pick_provider(uid, provider)
    event_id = _check_id(event_id)
    current = get_event(uid, provider, event_id)
    me = _my_email(uid, provider)
    organiser = not current["organizer"] or current["organizer"] == me
    if provider == "google":
        if organiser:
            google._api(uid, "DELETE", f"/calendar/v3/calendars/primary/events/{_q(event_id)}", {"sendUpdates": "all"})
            return current, "cancelled"
        attendees = [{**{"email": a["email"]}, **({"responseStatus": "declined"} if a["email"] == me else {})}
                     for a in current["attendees"]]
        google._api(uid, "PATCH", f"/calendar/v3/calendars/primary/events/{_q(event_id)}", {"sendUpdates": "all"},
                    {"attendees": attendees})
        return current, "declined"
    if organiser:
        microsoft.graph(uid, "POST", f"/me/events/{_q(event_id)}/cancel", {"comment": message[:1000]})
        return current, "cancelled"
    microsoft.graph(uid, "POST", f"/me/events/{_q(event_id)}/decline", {"comment": message[:1000], "sendResponse": True})
    return current, "declined"


def _my_email(uid, provider):
    from ..integrations import google as g
    row = g.connection(uid) if provider == "google" else microsoft.connection(uid)
    return (row.account or "").lower() if row else ""
