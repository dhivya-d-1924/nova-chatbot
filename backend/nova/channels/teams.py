"""Microsoft Teams via an Azure Bot (Bot Framework REST API, no SDK needed).

Inbound: Teams (through the Bot Connector) calls POST /api/channels/teams/messages with a signed JWT. We check the
signature against Microsoft's published keys, the issuer, the audience (your app id), the expiry and that the token's
serviceUrl matches the activity, then that the message comes from your tenant and from a 1:1 chat.
Outbound: replies are posted to the activity's serviceUrl with an app token from Microsoft Entra ID.

Only 1:1 (personal) chats are answered, so private answers never land in a team channel or group chat.
"""
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request

import jwt

from ..config import settings
from . import core

log = logging.getLogger("nova.channels.teams")
OPENID_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"
SCOPE = "https://api.botframework.com/.default"
SERVICE_HOSTS = (".botframework.com", ".trafficmanager.net", ".teams.microsoft.com", ".botframework.azure.us",
                 ".teams.microsoft.us")
MAX_TEXT = 20000
_lock = threading.Lock()
_jwks = {"client": None, "at": 0.0}
_token = {"value": None, "exp": 0.0}


class AuthError(Exception):
    pass


def _http_json(url, data=None, headers=None, form=False):
    body = None
    h = dict(headers or {})
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if data is not None else "GET")  # noqa: S310
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310  Microsoft endpoints, validated serviceUrl
        raw = r.read()
    return json.loads(raw) if raw else {}


def _signing_key(token):
    """Microsoft's current signing key for this token (keys are cached and refreshed daily)."""
    with _lock:
        if _jwks["client"] is None or time.time() - _jwks["at"] > 86400:
            jwks_uri = _http_json(OPENID_URL)["jwks_uri"]
            _jwks["client"] = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=86400)
            _jwks["at"] = time.time()
        client = _jwks["client"]
    return client.get_signing_key_from_jwt(token).key


def valid_service_url(url):
    p = urllib.parse.urlparse(url or "")
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host.endswith(h) or host == h.lstrip(".") for h in SERVICE_HOSTS)


def verify_request(auth_header, activity):
    """Raise AuthError unless the request was really sent by the Bot Connector for this bot."""
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise AuthError("missing token")
    token = auth_header[7:].strip()
    try:
        claims = jwt.decode(token, _signing_key(token), algorithms=["RS256"], audience=settings.TEAMS_APP_ID,
                            issuer=ISSUER, leeway=300, options={"require": ["exp", "iss", "aud"]})
    except Exception as e:  # noqa: BLE001  any decoding/signature problem is an authentication failure
        raise AuthError(f"invalid token: {e.__class__.__name__}") from e
    service_url = (activity.get("serviceUrl") or "").rstrip("/")
    if claims.get("serviceurl") and claims["serviceurl"].rstrip("/") != service_url:
        raise AuthError("serviceUrl mismatch")
    if not valid_service_url(service_url):
        raise AuthError("unexpected serviceUrl")
    return claims


def _app_token():
    with _lock:
        if _token["value"] and time.time() < _token["exp"] - 300:
            return _token["value"]
    tenant = settings.TEAMS_TENANT_ID or "botframework.com"
    data = _http_json(f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/oauth2/v2.0/token",
                      {"grant_type": "client_credentials", "client_id": settings.TEAMS_APP_ID,
                       "client_secret": settings.TEAMS_APP_PASSWORD, "scope": SCOPE}, form=True)
    with _lock:
        _token["value"], _token["exp"] = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return _token["value"]


def _post(service_url, conversation_id, activity, reply_to=None):
    if not valid_service_url(service_url):
        raise ValueError("refusing to post to an unexpected serviceUrl")
    conv = urllib.parse.quote(conversation_id, safe="")
    url = f"{service_url.rstrip('/')}/v3/conversations/{conv}/activities"
    if reply_to:
        url += "/" + urllib.parse.quote(reply_to, safe="")
    return _http_json(url, activity, headers={"Authorization": f"Bearer {_app_token()}"})


def send_text(service_url, conversation_id, reply_to, text):
    for part in core.chunks(text, MAX_TEXT):
        _post(service_url, conversation_id, {"type": "message", "text": part, "textFormat": "markdown"}, reply_to)


def send_typing(service_url, conversation_id):
    _post(service_url, conversation_id, {"type": "typing"})


def _tenant(activity):
    return ((activity.get("channelData") or {}).get("tenant") or {}).get("id") or \
        (activity.get("conversation") or {}).get("tenantId") or ""


def clean_text(text):
    t = re.sub(r"<at>.*?</at>", "", text or "", flags=re.S)  # @mention of the bot
    t = re.sub(r"<[^>]+>", "", t)
    return t.replace("&nbsp;", " ").strip()


def handle_activity(activity):
    """Route one verified activity. Returns an HTTP status for the Bot Connector."""
    if settings.TEAMS_TENANT_ID and _tenant(activity).lower() != settings.TEAMS_TENANT_ID.lower():
        log.warning("Teams: rejected activity from another tenant")
        return 403
    service_url = activity.get("serviceUrl", "")
    conv = activity.get("conversation") or {}
    conv_id, act_id = conv.get("id", ""), activity.get("id")
    kind = activity.get("type")
    personal = conv.get("conversationType", "personal") == "personal"
    if kind == "conversationUpdate":
        bot_id = (activity.get("recipient") or {}).get("id")
        added = [m.get("id") for m in activity.get("membersAdded") or []]
        if personal and bot_id in added and conv_id:
            core.submit_call(lambda: send_text(service_url, conv_id, None,
                                               "Hi! I'm Nova, your company's AI assistant. To start, link your Nova "
                                               "account: open Nova > Account > Connected apps > Microsoft Teams > Link, "
                                               "then send me the code like this: link 123456"))
        return 200
    if kind != "message" or not conv_id:
        return 200
    sender = activity.get("from") or {}
    aad = (sender.get("aadObjectId") or "").lower()
    if not personal:
        core.submit_call(lambda: send_text(service_url, conv_id, act_id,
                                           "For privacy I only answer in a 1:1 chat. Open a chat with me directly."))
        return 200
    if not aad:
        return 200
    meta = json.dumps({"service_url": service_url, "conversation_id": conv_id, "tenant": _tenant(activity)})
    core.submit(core.Inbound(
        channel="teams", sender=aad, text=clean_text(activity.get("text"))[:4000], event_id=act_id or "",
        display=(sender.get("name") or "Teams user")[:200], meta=meta,
        reply=lambda t: send_text(service_url, conv_id, act_id, t),
        typing=lambda: send_typing(service_url, conv_id)))
    return 200
