"""Google Workspace for each person: they connect their own Google account (OAuth), and the assistant can read their
calendar, search and read their Drive files, search and read their Gmail, and create calendar events (with approval).

- Tokens are encrypted at rest, refreshed automatically, and revoked on disconnect.
- The OAuth state is signed, short-lived and bound to the signed-in Nova user, so a callback can't attach someone
  else's Google account to your Nova account (or yours to theirs).
- GOOGLE_ALLOWED_DOMAIN limits connections to your company's Google Workspace domain.
- Everything read from Google is passed to the model as untrusted data; creating an event always needs approval.
"""
import json
import logging
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, insert, select, update

from .. import db, security, tools
from ..config import settings

log = logging.getLogger("nova.integrations.google")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
API = "https://www.googleapis.com"
SCOPES = ["openid", "email", "https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/gmail.readonly"]
STATE_MAX_AGE = 600
ID_PATTERN = re.compile(r"[A-Za-z0-9_\-]{5,200}")
EXPORTS = {"application/vnd.google-apps.document": "text/plain", "application/vnd.google-apps.presentation": "text/plain",
           "application/vnd.google-apps.spreadsheet": "text/csv"}


class GoogleError(Exception):
    pass


def redirect_uri():
    return f"{settings.APP_URL.rstrip('/')}/api/integrations/google/callback"


def _signer():
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="google-oauth")


# ------------------------------------------------------------------ HTTP
def _http(method, url, data=None, headers=None, form=False, raw=False):
    """Call a Google endpoint. Returns parsed JSON (or bytes with raw=True)."""
    body, h = None, dict(headers or {})
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)  # noqa: S310  fixed Google hosts
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            content = r.read(10 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        detail = e.read(800).decode("utf-8", errors="replace")
        raise GoogleError(f"{e.code}: {detail[:300]}") from e
    except urllib.error.URLError as e:
        raise GoogleError(f"couldn't reach Google ({e.reason})") from e
    if raw:
        return content
    return json.loads(content) if content else {}


# ------------------------------------------------------------------ connect / disconnect
def auth_url(uid):
    state = _signer().dumps({"u": uid, "n": secrets.token_urlsafe(8)})
    q = {"client_id": settings.GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri(), "response_type": "code",
         "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
         "state": state}
    if settings.GOOGLE_ALLOWED_DOMAIN:
        q["hd"] = settings.GOOGLE_ALLOWED_DOMAIN
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def check_state(state, uid):
    try:
        data = _signer().loads(state or "", max_age=STATE_MAX_AGE)
    except SignatureExpired:
        raise GoogleError("The sign-in took too long. Try connecting again.")
    except BadSignature:
        raise GoogleError("Invalid sign-in response.")
    if data.get("u") != uid:
        raise GoogleError("This Google sign-in was started by a different Nova account.")


def finish(uid, code):
    """Exchange the code, check the account and store the (encrypted) tokens. Returns the Google email."""
    tok = _http("POST", TOKEN_URL, {"code": code, "client_id": settings.GOOGLE_CLIENT_ID,
                                    "client_secret": settings.GOOGLE_CLIENT_SECRET, "redirect_uri": redirect_uri(),
                                    "grant_type": "authorization_code"}, form=True)
    access = tok.get("access_token")
    if not access:
        raise GoogleError("Google didn't return an access token.")
    info = _http("GET", USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified", True):
        raise GoogleError("Google didn't confirm the account's email address.")
    if settings.GOOGLE_ALLOWED_DOMAIN:
        domain = (info.get("hd") or email.rsplit("@", 1)[-1]).lower()
        if domain != settings.GOOGLE_ALLOWED_DOMAIN:
            _revoke(access)
            raise GoogleError(f"Only {settings.GOOGLE_ALLOWED_DOMAIN} Google accounts can be connected.")
    now = db.now_us()
    values = {"account": email, "scopes": tok.get("scope", " ".join(SCOPES)), "access_token": security.encrypt(access),
              "expires_at": now + int(tok.get("expires_in", 3600)) * 10**6, "updated_at": now}
    if tok.get("refresh_token"):
        values["refresh_token"] = security.encrypt(tok["refresh_token"])
    with db.engine.begin() as c:
        row = c.execute(select(db.user_connections.c.id).where(db.user_connections.c.user_id == uid,
                                                               db.user_connections.c.provider == "google")).first()
        if row:
            c.execute(update(db.user_connections).where(db.user_connections.c.id == row.id).values(**values))
        else:
            c.execute(insert(db.user_connections).values(id=str(uuid.uuid4()), user_id=uid, provider="google",
                                                         created_at=now, **values))
    return email


def connection(uid):
    with db.engine.connect() as c:
        return c.execute(select(db.user_connections).where(db.user_connections.c.user_id == uid,
                                                           db.user_connections.c.provider == "google")).first()


def _revoke(token):
    try:
        _http("POST", REVOKE_URL, {"token": token}, form=True)
    except GoogleError:
        log.info("Google token revoke failed (it may already be invalid)")


def disconnect(uid):
    row = connection(uid)
    if not row:
        return False
    token = row.refresh_token or row.access_token
    if token:
        try:
            _revoke(security.decrypt(token))
        except Exception:  # noqa: BLE001
            log.info("could not decrypt Google token for revoke")
    with db.engine.begin() as c:
        c.execute(delete(db.user_connections).where(db.user_connections.c.id == row.id))
    return True


def _access_token(uid):
    row = connection(uid)
    if not row:
        raise tools.ToolError("Google isn't connected. Connect it in Account > Apps.")
    if row.expires_at and row.expires_at > db.now_us() + 60 * 10**6 and row.access_token:
        return security.decrypt(row.access_token)
    if not row.refresh_token:
        raise tools.ToolError("Google access expired. Reconnect Google in Account > Apps.")
    try:
        tok = _http("POST", TOKEN_URL, {"client_id": settings.GOOGLE_CLIENT_ID, "client_secret": settings.GOOGLE_CLIENT_SECRET,
                                        "refresh_token": security.decrypt(row.refresh_token), "grant_type": "refresh_token"},
                    form=True)
    except GoogleError as e:
        if "invalid_grant" in str(e):
            with db.engine.begin() as c:
                c.execute(delete(db.user_connections).where(db.user_connections.c.id == row.id))
            raise tools.ToolError("Google access was revoked. Reconnect Google in Account > Apps.")
        raise tools.ToolError(f"Couldn't refresh Google access: {e}")
    with db.engine.begin() as c:
        c.execute(update(db.user_connections).where(db.user_connections.c.id == row.id).values(
            access_token=security.encrypt(tok["access_token"]), updated_at=db.now_us(),
            expires_at=db.now_us() + int(tok.get("expires_in", 3600)) * 10**6))
    return tok["access_token"]


def _api(uid, method, path, params=None, body=None, raw=False):
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params, doseq=True)}" if params else "")
    try:
        return _http(method, url, body, headers={"Authorization": f"Bearer {_access_token(uid)}"}, raw=raw)
    except GoogleError as e:
        if str(e).startswith("403"):
            raise tools.ToolError("Google refused this request. Reconnect Google and allow the requested access.")
        if str(e).startswith("404"):
            raise tools.ToolError("Google couldn't find that item.")
        raise tools.ToolError(f"Google error {e}")


# ------------------------------------------------------------------ tools
def _need_id(a, key):
    v = str(a.get(key) or "").strip()
    if not ID_PATTERN.fullmatch(v):
        raise tools.ToolError(f"'{key}' must be an id returned by a previous search.")
    return v


def _when(v, default):
    if not v:
        return default
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise tools.ToolError("Dates must look like 2026-10-21T15:00:00+05:30.")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def calendar_list(ctx, a):
    now = datetime.now(timezone.utc)
    start, end = _when(a.get("time_min"), now), _when(a.get("time_max"), None)
    end = end or start + timedelta(days=7)
    params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime",
              "maxResults": 25}
    if a.get("query"):
        params["q"] = str(a["query"])[:200]
    items = _api(ctx["uid"], "GET", "/calendar/v3/calendars/primary/events", params).get("items", [])
    if not items:
        return "No events in that period.", "0 events", {}
    lines = []
    for e in items:
        s, f = e.get("start", {}), e.get("end", {})
        who = len(e.get("attendees") or [])
        lines.append(f"- {s.get('dateTime') or s.get('date')} to {f.get('dateTime') or f.get('date')}: {e.get('summary', '(no title)')}"
                     + (f" @ {e['location']}" if e.get("location") else "") + (f" ({who} attendees)" if who else "")
                     + f" [id {e.get('id')}]")
    return "\n".join(lines), f"{len(items)} event(s)", {}


def calendar_create(ctx, a):
    summary = str(a.get("summary") or "").strip()[:200]
    if not summary:
        raise tools.ToolError("The event needs a title.")
    start, end = _when(a.get("start"), None), _when(a.get("end"), None)
    if not start or not end or end <= start:
        raise tools.ToolError("Give a start and an end time (end after start).")
    attendees = [x.strip() for x in str(a.get("attendees") or "").split(",") if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", x.strip())]
    body = {"summary": summary, "description": str(a.get("description") or "")[:4000],
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
            "attendees": [{"email": x} for x in attendees[:50]]}
    ev = _api(ctx["uid"], "POST", "/calendar/v3/calendars/primary/events",
              {"sendUpdates": "all" if attendees else "none"}, body)
    return f"Event created: {ev.get('summary')} on {start.isoformat()} ({ev.get('htmlLink', '')}).", "Event created", {}


def drive_search(ctx, a):
    q = str(a.get("query") or "").strip()[:200]
    if not q:
        raise tools.ToolError("Say what to search for.")
    esc = q.replace("\\", "\\\\").replace("'", "\\'")
    res = _api(ctx["uid"], "GET", "/drive/v3/files", {
        "q": f"(name contains '{esc}' or fullText contains '{esc}') and trashed = false", "pageSize": 10,
        "fields": "files(id,name,mimeType,modifiedTime,webViewLink)", "orderBy": "modifiedTime desc"})
    files = res.get("files", [])
    if not files:
        return "No matching files in Google Drive.", "0 files", {}
    return "\n".join(f"- {f['name']} ({f.get('mimeType', '').rsplit('.', 1)[-1]}, modified {f.get('modifiedTime', '')[:10]}) "
                     f"[id {f['id']}] {f.get('webViewLink', '')}" for f in files), f"{len(files)} file(s)", {}


def drive_read(ctx, a):
    fid = _need_id(a, "file_id")
    meta = _api(ctx["uid"], "GET", f"/drive/v3/files/{fid}", {"fields": "id,name,mimeType,size"})
    mime = meta.get("mimeType", "")
    if mime in EXPORTS:
        data = _api(ctx["uid"], "GET", f"/drive/v3/files/{fid}/export", {"mimeType": EXPORTS[mime]}, raw=True)
        text = data.decode("utf-8", errors="replace")
    elif int(meta.get("size") or 0) > 8 * 1024 * 1024:
        raise tools.ToolError("That file is too large to read here (over 8 MB).")
    else:
        data = _api(ctx["uid"], "GET", f"/drive/v3/files/{fid}", {"alt": "media"}, raw=True)
        if mime.startswith("text/") or mime in ("application/json", "text/csv"):
            text = data.decode("utf-8", errors="replace")
        elif mime in ("application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
            from .. import rag
            kind = "pdf" if mime == "application/pdf" else "docx"
            try:
                parts, _ = rag.extract_parts(kind, data)
            except Exception as e:  # noqa: BLE001
                raise tools.ToolError(f"Couldn't read that file: {getattr(e, 'message', e)}")
            text = "\n\n".join(t for _, t in parts)
        else:
            raise tools.ToolError(f"Files of type {mime} can't be read as text.")
    text = text.strip() or "(the file is empty)"
    return f"{meta.get('name')}:\n{text[:8000]}" + ("\n…(truncated)" if len(text) > 8000 else ""), f"Read {meta.get('name')}", {}


def _header(msg, name):
    return next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h.get("name", "").lower() == name.lower()), "")


def gmail_search(ctx, a):
    q = str(a.get("query") or "").strip()[:300] or "in:inbox"
    ids = _api(ctx["uid"], "GET", "/gmail/v1/users/me/messages", {"q": q, "maxResults": 8}).get("messages", [])
    if not ids:
        return "No matching emails.", "0 emails", {}
    out = []
    for m in ids:
        msg = _api(ctx["uid"], "GET", f"/gmail/v1/users/me/messages/{m['id']}",
                   {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        out.append(f"- {_header(msg, 'Date')} | From: {_header(msg, 'From')} | {_header(msg, 'Subject')}\n  {msg.get('snippet', '')} "
                   f"[id {m['id']}]")
    return "\n".join(out), f"{len(ids)} email(s)", {}


def _plain_body(part):
    import base64
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", errors="replace")
    for p in part.get("parts", []) or []:
        t = _plain_body(p)
        if t:
            return t
    return ""


def gmail_read(ctx, a):
    mid = _need_id(a, "message_id")
    msg = _api(ctx["uid"], "GET", f"/gmail/v1/users/me/messages/{mid}", {"format": "full"})
    body = _plain_body(msg.get("payload", {})) or msg.get("snippet", "")
    head = f"From: {_header(msg, 'From')}\nTo: {_header(msg, 'To')}\nDate: {_header(msg, 'Date')}\nSubject: {_header(msg, 'Subject')}"
    return f"{head}\n\n{body.strip()[:8000]}", f"Read “{_header(msg, 'Subject')[:60]}”", {}


def _S(**props):
    return {"type": "object", "properties": {k.rstrip("?"): {"type": "string", "description": d} for k, d in props.items()},
            "required": [k for k in props if not k.endswith("?")]}


TOOLS = {
    "google_calendar_events": dict(
        label="Google Calendar: your events", desc="List events in the user's Google Calendar between two times (default: the next 7 days).",
        params=_S(**{"time_min?": "Start, ISO 8601 with timezone", "time_max?": "End, ISO 8601 with timezone",
                     "query?": "Words to filter events by"}),
        describe=lambda a: "Checking your Google Calendar", run=calendar_list),
    "google_calendar_create_event": dict(
        label="Google Calendar: create an event", sensitive=True,
        desc="Create an event in the user's Google Calendar. Attendees get an invitation. The user must approve it.",
        params=_S(summary="Event title", start="Start, ISO 8601 with timezone", end="End, ISO 8601 with timezone",
                  **{"description?": "Details", "attendees?": "Comma-separated email addresses"}),
        describe=lambda a: f"Create calendar event “{str(a.get('summary', ''))[:60]}” at {a.get('start', '?')}",
        audit=lambda a: {"summary": str(a.get("summary", ""))[:100], "attendees": len(str(a.get("attendees") or "").split(",")) if a.get("attendees") else 0},
        run=calendar_create),
    "google_drive_search": dict(
        label="Google Drive: search files", desc="Search the user's Google Drive by file name and content.",
        params=_S(query="What to look for"), describe=lambda a: f"Searching Google Drive: {a.get('query', '')}", run=drive_search),
    "google_drive_read": dict(
        label="Google Drive: read a file", desc="Read the text of a Google Drive file (Docs, Sheets, Slides, PDF, Word, text). "
                                               "Use an id from google_drive_search.",
        params=_S(file_id="File id"), describe=lambda a: "Reading a Google Drive file", run=drive_read),
    "gmail_search": dict(
        label="Gmail: search emails", desc="Search the user's Gmail with Gmail search syntax (e.g. 'from:boss newer_than:7d').",
        params=_S(**{"query?": "Gmail search query"}), describe=lambda a: f"Searching Gmail: {a.get('query', 'inbox')}", run=gmail_search),
    "gmail_read": dict(
        label="Gmail: read an email", desc="Read one email by id (from gmail_search).",
        params=_S(message_id="Message id"), describe=lambda a: "Reading an email", run=gmail_read),
}


def tools_for(ctx):
    if not settings.GOOGLE_ENABLED or not ctx.get("uid") or not connection(ctx["uid"]):
        return {}
    return {n: {**t, "available": lambda: True, "integration": True} for n, t in TOOLS.items()}

