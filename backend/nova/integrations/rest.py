"""Internal app integrations: an administrator registers any REST API (HR, CRM, tickets, inventory…) and the actions
the assistant may call. Each action becomes an agent tool.

Safety:
- Only administrators can add apps, actions or secrets. Secrets are encrypted at rest and never sent to the browser
  or the model.
- Actions that change data (POST/PUT/PATCH/DELETE) always need the user's approval before they run.
- Values from the model are typed and URL-encoded; the request can only go to the app's own base URL (no redirects).
- Internal (private) addresses are allowed, because that is where internal apps live, but cloud metadata and
  link-local addresses are always blocked, and INTEGRATION_ALLOWED_HOSTS can pin the allowed hosts.
- Responses are size-limited and handed to the model as untrusted data.
"""
import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import select

from .. import db, security, tools
from ..config import settings

MAX_RESPONSE = 200_000
METADATA_IPS = {"169.254.169.254", "169.254.170.2", "100.100.100.200", "fd00:ec2::254"}
SLUG = r"[a-z][a-z0-9_]{1,29}"
PARAM_NAME = r"[A-Za-z_][A-Za-z0-9_]{0,39}"
TYPES = ("string", "number", "integer", "boolean")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


# ------------------------------------------------------------------ HTTP client for internal apps
def _blocked(ip):
    a = ipaddress.ip_address(ip)
    return str(a) in METADATA_IPS or a.is_link_local or a.is_multicast or a.is_unspecified


def check_host(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise tools.ToolError("The app address must start with http:// or https://")
    host = p.hostname.lower()
    if settings.INTEGRATION_ALLOWED_HOSTS and not any(host == h or host.endswith("." + h) for h in settings.INTEGRATION_ALLOWED_HOSTS):
        raise tools.ToolError(f"{host} is not in INTEGRATION_ALLOWED_HOSTS.")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise tools.ToolError(f"Couldn't find the server {host}.")
    if any(_blocked(i[4][0]) for i in infos):
        raise tools.ToolError("That address is not allowed (cloud metadata / link-local).")
    return host


def _guarded_connection(address, *args, **kwargs):
    sock = socket.create_connection(address, *args, **kwargs)
    if _blocked(sock.getpeername()[0]):  # re-checked after connecting, against DNS rebinding
        sock.close()
        raise tools.ToolError("That address is not allowed (cloud metadata / link-local).")
    return sock


class _Conn(http.client.HTTPConnection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._create_connection = _guarded_connection


class _SConn(http.client.HTTPSConnection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._create_connection = _guarded_connection


class _H(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_Conn, req)


class _HS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_SConn, req, context=ssl.create_default_context())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise tools.ToolError(f"The app tried to redirect the request (HTTP {code}); redirects are not followed.")


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _H, _HS, _NoRedirect)


def send(method, url, headers, body=None):
    """Make the request. Returns (status, content_type, text). Raises ToolError with a plain message on failure."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310  host checked first
    try:
        with _opener().open(req, timeout=settings.INTEGRATION_TIMEOUT) as r:
            raw = r.read(MAX_RESPONSE + 1)
            return r.status, r.headers.get("Content-Type", ""), raw[:MAX_RESPONSE].decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        snippet = e.read(600).decode("utf-8", errors="replace").strip()
        raise tools.ToolError(f"The app returned HTTP {e.code}{': ' + snippet[:300] if snippet else ''}") from e
    except urllib.error.URLError as e:
        reason = e.reason if not isinstance(e.reason, tools.ToolError) else str(e.reason)
        raise tools.ToolError(f"Couldn't reach the app: {reason}") from e
    except (TimeoutError, socket.timeout) as e:
        raise tools.ToolError(f"The app didn't answer within {settings.INTEGRATION_TIMEOUT} seconds.") from e


# ------------------------------------------------------------------ calling an action
def _coerce(kind, value, name):
    try:
        if kind == "integer":
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if kind == "number":
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("true", "yes", "1"):
                return True
            if str(value).lower() in ("false", "no", "0"):
                return False
            raise ValueError
    except (TypeError, ValueError):
        raise tools.ToolError(f"'{name}' must be a {kind}.")
    return str(value)[:2000]


def build_request(integ, action, args, user=None):
    """Turn tool arguments into (method, url, headers, body). Pure: no network, so it is easy to test."""
    args = args if isinstance(args, dict) else {}
    path, query, body = action.path, {}, {}
    for p in json.loads(action.params or "[]"):
        name = p["name"]
        value = args.get(name)
        if value is None or value == "":
            if p.get("required"):
                raise tools.ToolError(f"Missing required value '{name}'.")
            continue
        value = _coerce(p.get("type", "string"), value, name)
        where = p.get("in") or ("query" if action.method in ("GET", "DELETE") else "body")
        if where == "path":
            path = path.replace("{" + name + "}", urllib.parse.quote(str(value), safe=""))
        elif where == "query":
            query[name] = str(value).lower() if isinstance(value, bool) else value
        else:
            body[name] = value
    if re.search(r"\{[^}]*\}", path):
        raise tools.ToolError("A value needed for the address is missing.")
    base = integ.base_url.rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(base).netloc:
        raise tools.ToolError("The request would leave the app's address.")
    headers = {"Accept": "application/json", "User-Agent": "Nova-Assistant/1.0"}
    if action.method in WRITE_METHODS or body:
        headers["Content-Type"] = "application/json"
    secret = security.decrypt(integ.secret) if integ.secret else ""
    if integ.auth_type == "bearer" and secret:
        headers["Authorization"] = f"Bearer {secret}"
    elif integ.auth_type == "header" and secret and integ.auth_header:
        headers[integ.auth_header] = secret
    elif integ.auth_type == "basic" and secret:
        headers["Authorization"] = "Basic " + base64.b64encode(secret.encode()).decode()
    if integ.send_user and user:
        headers["X-Nova-User-Email"] = user.get("email") or ""
        headers["X-Nova-User-Id"] = user.get("uid") or ""
    send_body = body if (body or action.method in ("POST", "PUT", "PATCH")) else None
    return action.method, url, headers, send_body


def format_response(status, ctype, text, limit=6000):
    if "json" in ctype or text[:1] in "[{":
        try:
            text = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
        except ValueError:
            pass
    text = text.strip() or "(empty response)"
    return (text[:limit] + ("\n…(truncated)" if len(text) > limit else "")), f"HTTP {status}"


def call(integ, action, args, user=None):
    method, url, headers, body = build_request(integ, action, args, user)
    check_host(url)
    status, ctype, text = send(method, url, headers, body)
    return format_response(status, ctype, text)


# ------------------------------------------------------------------ agent tools
def _load(iid, aid):
    with db.engine.connect() as c:
        integ = c.execute(select(db.integrations).where(db.integrations.c.id == iid, db.integrations.c.enabled == 1)).first()
        action = c.execute(select(db.integration_actions).where(db.integration_actions.c.id == aid,
                                                                db.integration_actions.c.enabled == 1)).first()
    if not integ or not action:
        raise tools.ToolError("This app action has been switched off by an administrator.")
    return integ, action


def schema(params):
    props, required = {}, []
    for p in params:
        props[p["name"]] = {"type": p.get("type", "string"), "description": p.get("description") or p["name"]}
        if p.get("required"):
            required.append(p["name"])
    return {"type": "object", "properties": props, "required": required}


def tools_for(ctx):
    """Agent tools for every enabled action this user may use (re-read on every run, so changes apply at once)."""
    with db.engine.connect() as c:
        rows = c.execute(select(db.integrations, db.integration_actions.c.id.label("aid"),
                                db.integration_actions.c.name.label("aname"), db.integration_actions.c.label.label("alabel"),
                                db.integration_actions.c.description.label("adesc"), db.integration_actions.c.method,
                                db.integration_actions.c.params, db.integration_actions.c.approval)
                         .select_from(db.integrations.join(db.integration_actions,
                                                           db.integration_actions.c.integration_id == db.integrations.c.id))
                         .where(db.integrations.c.enabled == 1, db.integration_actions.c.enabled == 1)
                         .order_by(db.integrations.c.name, db.integration_actions.c.name)).all()
    out = {}
    for r in rows:
        if r.audience == "admin" and ctx.get("role") != "admin":
            continue
        name = f"{r.slug}__{r.aname}"[:64]
        params = json.loads(r.params or "[]")
        label = f"{r.name}: {r.alabel}"
        out[name] = dict(
            label=label, desc=f"[{r.name}] {r.adesc}"[:1000], params=schema(params), available=lambda: True,
            sensitive=bool(r.approval) or r.method in WRITE_METHODS, integration=True,
            describe=lambda a, lbl=label: f"{lbl}" + (f" ({', '.join(f'{k}={str(v)[:40]}' for k, v in list(a.items())[:4])})" if a else ""),
            audit=lambda a, slug=r.slug, an=r.aname: {"app": slug, "action": an, "args": {k: str(v)[:100] for k, v in a.items()}},
            run=lambda c_, a, iid=r.id, aid=r.aid: _run(c_, a, iid, aid))
    return out


def _run(ctx, args, iid, aid):
    integ, action = _load(iid, aid)
    text, summary = call(integ, action, args, {"email": ctx.get("email"), "uid": ctx.get("uid")})
    return text, summary, {}
