"""Microsoft 365 for each person (Microsoft Entra ID sign-in + Microsoft Graph): Outlook Calendar, Teams chats and
channels, and Outlook automatic replies.

- Authorization code flow with PKCE and a confidential client; tokens encrypted at rest and refreshed automatically.
- MS_TENANT_ID pins the organisation; the token's tenant is checked as well.
- Graph throttling (429/503 with Retry-After) is retried a limited number of times; 401 refreshes once; revoked
  consent removes the connection and tells the user to reconnect.
- Least privilege by default; scopes that need an administrator's consent are opt-in (MS_EXTRA_SCOPES).
"""
import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from sqlalchemy import delete, insert, select, update

from .. import db, security, tools
from ..config import settings
from . import oauth

log = logging.getLogger("nova.workspace.microsoft")
LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
# All of these can be granted by the user themself unless the tenant blocks user consent.
BASE_SCOPES = ["openid", "profile", "email", "offline_access", "User.Read", "User.ReadBasic.All", "Calendars.ReadWrite",
               "MailboxSettings.ReadWrite", "Chat.ReadWrite", "ChannelMessage.Send", "Team.ReadBasic.All",
               "Channel.ReadBasic.All"]
# Need an administrator's consent; add the ones you approved with MS_EXTRA_SCOPES.
ADMIN_SCOPES = {"ChannelMessage.Read.All": "read Teams channel messages", "Presence.Read.All": "see colleagues' presence",
                "OnlineMeetingTranscript.Read.All": "read meeting transcripts"}
MAX_RETRIES = 3


class GraphError(Exception):
    def __init__(self, status, message):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def scopes():
    return BASE_SCOPES + [s for s in settings.MS_EXTRA_SCOPES if s not in BASE_SCOPES]


def redirect_uri():
    return f"{settings.APP_URL.rstrip('/')}/api/workspace/microsoft/callback"


def _tenant():
    return urllib.parse.quote(settings.MS_TENANT_ID or "organizations", safe="")


# ------------------------------------------------------------------ HTTP
def _http(method, url, data=None, headers=None, form=False):
    """One HTTP call. Returns (status, headers, parsed JSON or {}). Raises GraphError for HTTP errors."""
    body, h = None, dict(headers or {})
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)  # noqa: S310  fixed Microsoft hosts
    try:
        with urllib.request.urlopen(req, timeout=25) as r:  # noqa: S310
            raw = r.read(10 * 1024 * 1024)
            return r.status, dict(r.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read(4000).decode("utf-8", errors="replace")
        try:
            err = json.loads(raw)
            msg = (err.get("error") or {}).get("message") if isinstance(err.get("error"), dict) else \
                err.get("error_description") or err.get("error") or raw
        except ValueError:
            msg = raw
        exc = GraphError(e.code, str(msg)[:400])
        exc.retry_after = e.headers.get("Retry-After") if e.headers else None
        raise exc from e
    except urllib.error.URLError as e:
        raise GraphError(0, f"couldn't reach Microsoft ({e.reason})") from e


# ------------------------------------------------------------------ connect
def auth_url(uid):
    state, challenge = oauth.begin(uid, "microsoft")
    q = {"client_id": settings.MS_CLIENT_ID, "response_type": "code", "redirect_uri": redirect_uri(),
         "response_mode": "query", "scope": " ".join(scopes()), "state": state, "code_challenge": challenge,
         "code_challenge_method": "S256", "prompt": "select_account"}
    if settings.WORKSPACE_DEMO:   # simulated account: skip Microsoft's sign-in, same callback and PKCE checks
        return f"{redirect_uri()}?{urllib.parse.urlencode({'code': 'demo', 'state': state})}"
    return f"{LOGIN}/{_tenant()}/oauth2/v2.0/authorize?{urllib.parse.urlencode(q)}"


def _claims(id_token):
    """Read the id_token we just received from Microsoft's token endpoint over TLS (no third party involved)."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:  # noqa: BLE001
        return {}


def _token(data):
    data = {"client_id": settings.MS_CLIENT_ID, "client_secret": settings.MS_CLIENT_SECRET, **data}
    _, _, tok = _http("POST", f"{LOGIN}/{_tenant()}/oauth2/v2.0/token", data, form=True)
    return tok


def finish(uid, code, verifier):
    tok = _token({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
                  "code_verifier": verifier, "scope": " ".join(scopes())})
    access = tok.get("access_token")
    if not access:
        raise GraphError(400, "Microsoft didn't return an access token.")
    claims = _claims(tok.get("id_token", ""))
    tenant = (settings.MS_TENANT_ID or "").lower()
    if tenant not in ("", "organizations", "common", "consumers") and claims.get("tid", "").lower() != tenant:
        raise GraphError(403, "Only accounts from your organisation's Microsoft 365 tenant can be connected.")
    _, _, me = _http("GET", f"{GRAPH}/me?$select=id,displayName,mail,userPrincipalName",
                     headers={"Authorization": f"Bearer {access}"})
    email = (me.get("mail") or me.get("userPrincipalName") or "").lower()
    now = db.now_us()
    values = {"account": email, "scopes": tok.get("scope", " ".join(scopes())), "access_token": security.encrypt(access),
              "expires_at": now + int(tok.get("expires_in", 3600)) * 10**6, "updated_at": now}
    if tok.get("refresh_token"):
        values["refresh_token"] = security.encrypt(tok["refresh_token"])
    with db.engine.begin() as c:
        row = c.execute(select(db.user_connections.c.id).where(db.user_connections.c.user_id == uid,
                                                               db.user_connections.c.provider == "microsoft")).first()
        if row:
            c.execute(update(db.user_connections).where(db.user_connections.c.id == row.id).values(**values))
        else:
            c.execute(insert(db.user_connections).values(id=str(uuid.uuid4()), user_id=uid, provider="microsoft",
                                                         created_at=now, **values))
    return email


def connection(uid):
    with db.engine.connect() as c:
        return c.execute(select(db.user_connections).where(db.user_connections.c.user_id == uid,
                                                           db.user_connections.c.provider == "microsoft")).first()


def disconnect(uid):
    """Forget the tokens. (Microsoft has no token-revoke endpoint for delegated tokens; users can also remove the app
    at myapps.microsoft.com, and admins can revoke sessions in Entra ID.)"""
    with db.engine.begin() as c:
        return c.execute(delete(db.user_connections).where(db.user_connections.c.user_id == uid,
                                                           db.user_connections.c.provider == "microsoft")).rowcount > 0


def _drop(uid):
    with db.engine.begin() as c:
        c.execute(delete(db.user_connections).where(db.user_connections.c.user_id == uid,
                                                    db.user_connections.c.provider == "microsoft"))


def access_token(uid, force_refresh=False):
    row = connection(uid)
    if not row:
        raise tools.ToolError("Microsoft 365 isn't connected. Connect it in Workspace > Accounts.")
    if not force_refresh and row.access_token and row.expires_at and row.expires_at > db.now_us() + 60 * 10**6:
        return security.decrypt(row.access_token)
    if not row.refresh_token:
        raise tools.ToolError("Microsoft 365 access expired. Reconnect it in Workspace > Accounts.")
    try:
        tok = _token({"grant_type": "refresh_token", "refresh_token": security.decrypt(row.refresh_token),
                      "scope": " ".join(scopes())})
    except GraphError as e:
        if e.status in (400, 401) and ("invalid_grant" in e.message or "AADSTS" in e.message):
            _drop(uid)
            raise tools.ToolError("Microsoft 365 access was revoked or expired. Reconnect it in Workspace > Accounts.")
        raise tools.ToolError(f"Couldn't refresh Microsoft 365 access: {e.message}")
    values = {"access_token": security.encrypt(tok["access_token"]), "updated_at": db.now_us(),
              "expires_at": db.now_us() + int(tok.get("expires_in", 3600)) * 10**6}
    if tok.get("refresh_token"):  # Microsoft rotates refresh tokens
        values["refresh_token"] = security.encrypt(tok["refresh_token"])
    with db.engine.begin() as c:
        c.execute(update(db.user_connections).where(db.user_connections.c.id == row.id).values(**values))
    return tok["access_token"]


def graph(uid, method, path, body=None, params=None, headers=None):
    """Call Microsoft Graph as the user, with throttling retries and one token refresh on 401."""
    url = path if path.startswith("https://") else f"{GRAPH}{path}"
    if not url.startswith(GRAPH + "/"):
        raise tools.ToolError("Unexpected Microsoft Graph address.")
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    refreshed = False
    for attempt in range(MAX_RETRIES + 1):
        h = {"Authorization": f"Bearer {access_token(uid, force_refresh=refreshed)}", **(headers or {})}
        try:
            _, _, data = _http(method, url, body, h)
            return data
        except GraphError as e:
            if e.status == 401 and not refreshed:
                refreshed = True
                continue
            if e.status in (429, 503, 504) and attempt < MAX_RETRIES:
                try:
                    wait = min(float(getattr(e, "retry_after", None) or 2 ** attempt), 10)
                except ValueError:
                    wait = 2
                time.sleep(wait)
                continue
            raise _friendly(e)
    raise tools.ToolError("Microsoft 365 is busy right now (throttled). Try again in a minute.")


def _friendly(e):
    if e.status == 403:
        return tools.ToolError("Microsoft 365 refused this request. Your account or your administrator hasn't granted this "
                               f"permission yet ({e.message[:150]}).")
    if e.status == 404:
        return tools.ToolError("Microsoft 365 couldn't find that item.")
    if e.status in (429, 503):
        return tools.ToolError("Microsoft 365 is busy right now (throttled). Try again in a minute.")
    return tools.ToolError(f"Microsoft 365 error {e.status}: {e.message[:200]}")
