"""Endpoints: administrators manage internal apps and their actions; everyone sees what's available and connects
their own Google account."""
import json
import re
import urllib.parse
import uuid

from flask import Blueprint, g, jsonify, redirect, request
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .. import db, security, tools
from ..config import settings
from ..errors import ApiError
from ..security import audit, auth, enforce_rate
from ..validation import Bool, Str, parse
from . import google, rest

bp = Blueprint("integrations", __name__, url_prefix="/api")


def _integration_dict(r, actions=None):
    d = {"id": r.id, "name": r.name, "slug": r.slug, "description": r.description, "base_url": r.base_url,
         "auth_type": r.auth_type, "auth_header": r.auth_header, "has_secret": bool(r.secret), "audience": r.audience,
         "send_user": bool(r.send_user), "enabled": bool(r.enabled), "created_at": r.created_at, "updated_at": r.updated_at}
    if actions is not None:
        d["actions"] = [_action_dict(a) for a in actions]
    return d


def _action_dict(a):
    return {"id": a.id, "name": a.name, "label": a.label, "description": a.description, "method": a.method, "path": a.path,
            "params": json.loads(a.params or "[]"), "approval": bool(a.approval) or a.method in rest.WRITE_METHODS,
            "enabled": bool(a.enabled), "tool": None}


def _get(c, iid):
    row = c.execute(select(db.integrations).where(db.integrations.c.id == iid)).first()
    if not row:
        raise ApiError(404, "App not found.", "not_found")
    return row


def _full(c, row):
    acts = c.execute(select(db.integration_actions).where(db.integration_actions.c.integration_id == row.id)
                     .order_by(db.integration_actions.c.name)).all()
    d = _integration_dict(row, acts)
    for a in d["actions"]:
        a["tool"] = f"{row.slug}__{a['name']}"[:64]
    return d


def _check_base_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.query or p.fragment or p.username:
        raise ApiError(400, "Base URL must look like https://hr.internal.example.com/api", "invalid_field")
    host = p.hostname.lower()
    if settings.INTEGRATION_ALLOWED_HOSTS and not any(host == h or host.endswith("." + h) for h in settings.INTEGRATION_ALLOWED_HOSTS):
        raise ApiError(400, f"{host} is not in INTEGRATION_ALLOWED_HOSTS.", "host_not_allowed")
    return url.rstrip("/")


INTEGRATION_FIELDS = {
    "name": Str(min=1, max=100, label="Name"), "slug": Str(min=2, max=30, pattern=rest.SLUG, label="Short name"),
    "description": Str(max=1000, label="Description"), "base_url": Str(min=8, max=500, label="Base URL"),
    "auth_type": Str(choices=("none", "bearer", "header", "basic"), label="Authentication"),
    "auth_header": Str(max=100, pattern=r"[A-Za-z0-9\-]{1,100}", label="Header name"),
    "secret": Str(max=4000, strip=False, label="Secret"), "audience": Str(choices=("all", "admin"), label="Who can use it"),
    "send_user": Bool(), "enabled": Bool(),
}


def _integration_values(d, creating):
    vals = {k: v for k, v in d.items() if v is not None and k != "secret"}
    if "base_url" in vals:
        vals["base_url"] = _check_base_url(vals["base_url"])
    for k in ("send_user", "enabled"):
        if k in vals:
            vals[k] = 1 if vals[k] else 0
    if "description" in vals:
        vals["description"] = vals["description"] or None
    if d.get("secret") is not None:
        vals["secret"] = security.encrypt(d["secret"]) if d["secret"] else None
    if creating:
        for k in ("name", "slug", "base_url"):
            if not vals.get(k):
                raise ApiError(400, f"{INTEGRATION_FIELDS[k].label} is required.", "invalid_field")
        vals.setdefault("auth_type", "none")
        vals.setdefault("audience", "all")
        vals.setdefault("send_user", 1)
        vals.setdefault("enabled", 1)
    if vals.get("auth_type") == "header" and not vals.get("auth_header") and creating:
        raise ApiError(400, "Enter the header name for the API key (for example X-API-Key).", "invalid_field")
    return vals


# ------------------------------------------------------------------ admin: apps
@bp.get("/admin/integrations")
@auth(admin=True)
def admin_list():
    with db.engine.connect() as c:
        rows = c.execute(select(db.integrations).order_by(db.integrations.c.name)).all()
        return jsonify([_full(c, r) for r in rows])


@bp.post("/admin/integrations")
@auth(admin=True)
def admin_create():
    vals = _integration_values(parse(request, INTEGRATION_FIELDS), creating=True)
    now = db.now_us()
    iid = str(uuid.uuid4())
    try:
        with db.engine.begin() as c:
            c.execute(insert(db.integrations).values(id=iid, created_at=now, updated_at=now, **vals))
    except IntegrityError:
        raise ApiError(409, "Another app already uses that short name.", "duplicate")
    audit("integration.created", target_type="integration", target_id=iid, slug=vals["slug"], host=urllib.parse.urlparse(vals["base_url"]).hostname)
    with db.engine.connect() as c:
        return jsonify(_full(c, _get(c, iid))), 201


@bp.patch("/admin/integrations/<iid>")
@auth(admin=True)
def admin_update(iid):
    vals = _integration_values(parse(request, INTEGRATION_FIELDS), creating=False)
    try:
        with db.engine.begin() as c:
            _get(c, iid)
            if vals:
                c.execute(update(db.integrations).where(db.integrations.c.id == iid).values(**vals, updated_at=db.now_us()))
    except IntegrityError:
        raise ApiError(409, "Another app already uses that short name.", "duplicate")
    audit("integration.updated", target_type="integration", target_id=iid,
          fields=sorted(k for k in vals if k != "secret") + (["secret"] if "secret" in vals else []))
    with db.engine.connect() as c:
        return jsonify(_full(c, _get(c, iid)))


@bp.delete("/admin/integrations/<iid>")
@auth(admin=True)
def admin_delete(iid):
    with db.engine.begin() as c:
        _get(c, iid)
        c.execute(delete(db.integration_actions).where(db.integration_actions.c.integration_id == iid))
        c.execute(delete(db.integrations).where(db.integrations.c.id == iid))
    audit("integration.deleted", target_type="integration", target_id=iid)
    return jsonify(ok=True)


# ------------------------------------------------------------------ admin: actions
ACTION_FIELDS = {
    "name": Str(min=2, max=30, pattern=rest.SLUG, label="Action name"), "label": Str(min=1, max=100, label="Label"),
    "description": Str(min=1, max=1000, label="Description"),
    "method": Str(choices=("GET", "POST", "PUT", "PATCH", "DELETE"), label="Method"),
    "path": Str(min=1, max=500, pattern=r"/[^\s]*", label="Path"), "approval": Bool(), "enabled": Bool(),
}


def _clean_params(raw, path, method):
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > 20:
        raise ApiError(400, "Parameters must be a list (up to 20).", "invalid_field")
    out, seen = [], set()
    for p in raw:
        if not isinstance(p, dict):
            raise ApiError(400, "Each parameter must be an object.", "invalid_field")
        name = str(p.get("name") or "").strip()
        if not re.fullmatch(rest.PARAM_NAME, name) or name in seen:
            raise ApiError(400, f"Parameter name '{name}' is invalid or repeated.", "invalid_field")
        seen.add(name)
        kind = p.get("type") or "string"
        where = p.get("in") or ("query" if method in ("GET", "DELETE") else "body")
        if kind not in rest.TYPES or where not in ("path", "query", "body"):
            raise ApiError(400, f"Parameter '{name}' has an invalid type or location.", "invalid_field")
        if where == "body" and method in ("GET", "DELETE"):
            raise ApiError(400, f"Parameter '{name}': {method} requests can't have a body; use query.", "invalid_field")
        out.append({"name": name, "type": kind, "in": where, "required": bool(p.get("required")) or where == "path",
                    "description": str(p.get("description") or "")[:300]})
    placeholders = set(re.findall(r"\{([^}]*)\}", path))
    path_params = {p["name"] for p in out if p["in"] == "path"}
    if placeholders != path_params:
        raise ApiError(400, "Every {placeholder} in the path needs a matching path parameter (and the other way round).",
                       "invalid_field")
    return out


@bp.post("/admin/integrations/<iid>/actions")
@auth(admin=True)
def admin_add_action(iid):
    d = parse(request, ACTION_FIELDS)
    for k in ("name", "label", "description", "method", "path"):
        if not d.get(k):
            raise ApiError(400, f"{ACTION_FIELDS[k].label} is required.", "invalid_field")
    params = _clean_params((request.get_json(silent=True) or {}).get("params"), d["path"], d["method"])
    approval = d["method"] in rest.WRITE_METHODS or bool(d.get("approval"))
    aid = str(uuid.uuid4())
    try:
        with db.engine.begin() as c:
            row = _get(c, iid)
            if c.execute(select(func.count()).select_from(db.integration_actions)).scalar() >= 60:
                raise ApiError(400, "Up to 60 app actions in total (the assistant works best with fewer tools).", "limit")
            c.execute(insert(db.integration_actions).values(
                id=aid, integration_id=iid, name=d["name"], label=d["label"], description=d["description"],
                method=d["method"], path=d["path"], params=json.dumps(params), approval=1 if approval else 0,
                enabled=0 if d.get("enabled") is False else 1, created_at=db.now_us()))
    except IntegrityError:
        raise ApiError(409, "This app already has an action with that name.", "duplicate")
    audit("integration.action_added", target_type="integration", target_id=iid, action_name=d["name"], method=d["method"])
    with db.engine.connect() as c:
        return jsonify(_full(c, row)), 201


@bp.patch("/admin/integrations/<iid>/actions/<aid>")
@auth(admin=True)
def admin_update_action(iid, aid):
    d = parse(request, ACTION_FIELDS)
    body = request.get_json(silent=True) or {}
    with db.engine.begin() as c:
        row = _get(c, iid)
        act = c.execute(select(db.integration_actions).where(db.integration_actions.c.id == aid,
                                                             db.integration_actions.c.integration_id == iid)).first()
        if not act:
            raise ApiError(404, "Action not found.", "not_found")
        method, path = d.get("method") or act.method, d.get("path") or act.path
        vals = {k: v for k, v in d.items() if v is not None and k not in ("approval", "enabled")}
        if "params" in body or "path" in d or "method" in d:
            vals["params"] = json.dumps(_clean_params(body.get("params", json.loads(act.params or "[]")), path, method))
        if d.get("enabled") is not None:
            vals["enabled"] = 1 if d["enabled"] else 0
        approval = d["approval"] if d.get("approval") is not None else bool(act.approval)
        vals["approval"] = 1 if (approval or method in rest.WRITE_METHODS) else 0
        try:
            c.execute(update(db.integration_actions).where(db.integration_actions.c.id == aid).values(**vals))
        except IntegrityError:
            raise ApiError(409, "This app already has an action with that name.", "duplicate")
    audit("integration.action_updated", target_type="integration", target_id=iid, action_name=act.name)
    with db.engine.connect() as c:
        return jsonify(_full(c, row))


@bp.delete("/admin/integrations/<iid>/actions/<aid>")
@auth(admin=True)
def admin_delete_action(iid, aid):
    with db.engine.begin() as c:
        row = _get(c, iid)
        n = c.execute(delete(db.integration_actions).where(db.integration_actions.c.id == aid,
                                                           db.integration_actions.c.integration_id == iid)).rowcount
    if not n:
        raise ApiError(404, "Action not found.", "not_found")
    audit("integration.action_deleted", target_type="integration", target_id=iid)
    with db.engine.connect() as c:
        return jsonify(_full(c, row))


@bp.post("/admin/integrations/<iid>/actions/<aid>/test")
@auth(admin=True)
def admin_test_action(iid, aid):
    """Run an action now with sample values, as the signed-in admin (even if it is switched off)."""
    enforce_rate("integration-test", 30, 60, key=g.user.id)
    args = (request.get_json(silent=True) or {}).get("args") or {}
    if not isinstance(args, dict):
        raise ApiError(400, "args must be an object.", "invalid_field")
    with db.engine.connect() as c:
        integ = _get(c, iid)
        act = c.execute(select(db.integration_actions).where(db.integration_actions.c.id == aid,
                                                             db.integration_actions.c.integration_id == iid)).first()
    if not act:
        raise ApiError(404, "Action not found.", "not_found")
    audit("integration.action_tested", target_type="integration", target_id=iid, action_name=act.name)
    try:
        text, summary = rest.call(integ, act, args, {"email": g.user.email, "uid": g.user.id})
        return jsonify(ok=True, summary=summary, result=text)
    except tools.ToolError as e:
        return jsonify(ok=False, error=str(e))


# ------------------------------------------------------------------ everyone: what's available
@bp.get("/integrations")
@auth()
def my_integrations():
    with db.engine.connect() as c:
        rows = c.execute(select(db.integrations).where(db.integrations.c.enabled == 1).order_by(db.integrations.c.name)).all()
        apps = []
        for r in rows:
            if r.audience == "admin" and g.user.role != "admin":
                continue
            acts = c.execute(select(db.integration_actions.c.label, db.integration_actions.c.method,
                                    db.integration_actions.c.approval)
                             .where(db.integration_actions.c.integration_id == r.id, db.integration_actions.c.enabled == 1)
                             .order_by(db.integration_actions.c.label)).all()
            apps.append({"name": r.name, "description": r.description,
                         "actions": [{"label": a.label, "approval": bool(a.approval) or a.method in rest.WRITE_METHODS}
                                     for a in acts]})
    conn = google.connection(g.user.id) if settings.GOOGLE_ENABLED else None
    return jsonify(apps=apps, google={"configured": settings.GOOGLE_ENABLED, "connected": bool(conn),
                                      "account": conn.account if conn else None, "connected_at": conn.created_at if conn else None,
                                      "domain": settings.GOOGLE_ALLOWED_DOMAIN or None})


# ------------------------------------------------------------------ Google Workspace (per user)
def _back(status, reason=None):
    q = {"integration": "google", "status": status}
    if reason:
        q["reason"] = reason[:200]
    return redirect(f"{settings.APP_URL.rstrip('/') or ''}/?{urllib.parse.urlencode(q)}")


@bp.get("/integrations/google/connect")
@auth()
def google_connect():
    if not settings.GOOGLE_ENABLED:
        raise ApiError(409, "Google Workspace isn't set up on this server yet. Ask your administrator.", "not_configured")
    enforce_rate("google-connect", 10, 3600, key=g.user.id)
    return jsonify(url=google.auth_url(g.user.id))


@bp.get("/integrations/google/callback")
def google_callback():
    """Google sends the browser here. The session cookie identifies the Nova user; the signed state must match it."""
    if not settings.GOOGLE_ENABLED:
        raise ApiError(404, "Not found", "not_found")
    _, user = security.load_session()
    if not user:
        return _back("error", "Sign in to Nova first, then connect Google again.")
    g.user = user
    if request.args.get("error"):
        return _back("cancelled", request.args.get("error"))
    try:
        verifier = google.check_state(request.args.get("state"), g.user.id)
        code = request.args.get("code") or ""
        if not code or len(code) > 2000:
            raise google.GoogleError("Missing authorization code.")
        email = google.finish(g.user.id, code, verifier)
    except google.GoogleError as e:
        return _back("error", str(e))
    audit("integration.google_connected", target_type="provider", target_id="google")
    return _back("connected", email)


@bp.delete("/integrations/google")
@auth()
def google_disconnect():
    if not google.disconnect(g.user.id):
        raise ApiError(404, "Google isn't connected.", "not_found")
    audit("integration.google_disconnected", target_type="provider", target_id="google")
    return jsonify(ok=True)
