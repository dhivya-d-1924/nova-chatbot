"""Registration, sign-in (with lockout and optional TOTP), sign-out, email verification and password reset."""
import json
import uuid

from flask import Blueprint, g, jsonify, make_response, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from . import db, security
from .config import settings
from .errors import ApiError
from .mail import send_account_email
from .security import audit, auth, enforce_rate
from .users import admin_on_register, admin_on_verify, adopt_orphan_chats, user_dict
from .validation import Str, parse

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
EMAIL_PATTERN = r"[^@\s]+@[^@\s]+\.[^@\s]+"
RESET_MAX_AGE = 3600
VERIFY_MAX_AGE = 3 * 24 * 3600
MFA_MAX_AGE = 300


def _serializer(salt):
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=salt)


def _pv(u):
    """Password fingerprint: links signed with it stop working once the password changes (single use)."""
    return (u.password_hash or "")[-12:]


def session_response(user, status=200, extra=None):
    token, row = security.create_session(user.id)
    resp = make_response(jsonify(user=user_dict(user), csrf=row["csrf"], **(extra or {})), status)
    return security.set_session_cookie(resp, token)


def _get_user(c, **where):
    q = select(db.users)
    for k, v in where.items():
        q = q.where(db.users.c[k] == v)
    return c.execute(q).first()


def send_verification(u):
    token = _serializer("verify-email").dumps({"uid": u.id, "email": u.email})
    return send_account_email(u.email, "Verify your email for Nova",
                              "Confirm your email address by opening this link (valid for 3 days):",
                              f"{settings.APP_URL}/?verify={token}")


@bp.post("/register")
def register():
    enforce_rate("register", settings.REGISTER_RATE_LIMIT, 3600)
    d = parse(request, {"name": Str(min=1, max=100, required=True, label="Name"),
                        "email": Str(min=3, max=255, required=True, pattern=EMAIL_PATTERN, label="Email"),
                        "password": Str(min=1, max=200, strip=False, required=True, label="Password")})
    email = d["email"].lower()
    security.check_password_policy(d["password"], email)
    with db.engine.begin() as c:
        first = c.execute(select(func.count()).select_from(db.users)).scalar() == 0
        if not settings.REGISTRATION_OPEN and not (first and settings.BOOTSTRAP_FIRST_ADMIN):
            raise ApiError(403, "Registration is closed. Ask an administrator for an account.", "registration_closed")
        if _get_user(c, email=email):
            raise ApiError(409, "An account with this email already exists. Sign in or reset your password.", "exists")
        is_admin = admin_on_register(email, first)
        now = db.now_us()
        row = {"id": str(uuid.uuid4()), "email": email, "name": d["name"], "password_hash": security.hash_password(d["password"]),
               "role": "admin" if is_admin else "user", "disabled": 0, "email_verified": 0, "created_at": now,
               "password_changed_at": now, "last_login": None if settings.REQUIRE_EMAIL_VERIFICATION else now}
        try:
            c.execute(insert(db.users).values(**row))
        except IntegrityError:
            raise ApiError(409, "An account with this email already exists. Sign in or reset your password.", "exists")
        if is_admin and first:
            adopt_orphan_chats(c, row["id"])
        user = _get_user(c, id=row["id"])
    audit("user.register", actor_id=user.id, target_type="user", target_id=user.id, role=user.role)
    if settings.REQUIRE_EMAIL_VERIFICATION:
        send_verification(user)
        return jsonify(verification_required=True,
                       message="Account created. Check your email for a link to verify your address, then sign in."), 201
    return session_response(user, 201)


@bp.post("/login")
def login():
    d = parse(request, {"email": Str(max=255, required=True, label="Email"),
                        "password": Str(max=200, strip=False, required=True, label="Password")})
    email = d["email"].lower()
    enforce_rate("login-email", 20, 3600, key=email)  # slows distributed guessing against one account
    with db.engine.connect() as c:
        u = _get_user(c, email=email)
    now = db.now_us()
    if u and u.locked_until and u.locked_until > now:
        security.verify_password(None, d["password"])  # same timing as a normal attempt
        audit("login.locked", target_type="user", target_id=u.id)
        raise ApiError(429, security.lock_message(u.locked_until), "locked")
    if not security.verify_password(u.password_hash if u else None, d["password"]):
        if u:
            until = security.register_failed_login(u)
            audit("login.failed", target_type="user", target_id=u.id, locked=bool(until))
            if until:
                raise ApiError(429, security.lock_message(until), "locked")
        else:
            audit("login.failed", target_type="email", target_id=None)
        raise ApiError(401, "Incorrect email or password.", "bad_credentials")
    if u.disabled:
        raise ApiError(403, "This account has been disabled. Contact an administrator.", "disabled")
    if settings.REQUIRE_EMAIL_VERIFICATION and not u.email_verified:
        raise ApiError(403, "Verify your email address first. We can send the link again.", "email_unverified")
    if u.mfa_enabled:
        token = _serializer("mfa-login").dumps({"uid": u.id, "pv": _pv(u)})
        return jsonify(mfa_required=True, mfa_token=token)
    return _finish_login(u)


def _finish_login(u):
    with db.engine.begin() as c:
        security.clear_failed_logins(u.id, c)
        c.execute(update(db.users).where(db.users.c.id == u.id).values(last_login=db.now_us()))
        u = _get_user(c, id=u.id)
    audit("login.success", actor_id=u.id, target_type="user", target_id=u.id, mfa=bool(u.mfa_enabled))
    return session_response(u)


@bp.post("/mfa")
def login_mfa():
    d = parse(request, {"mfa_token": Str(max=500, required=True), "code": Str(max=40, required=True, label="Code")})
    try:
        data = _serializer("mfa-login").loads(d["mfa_token"], max_age=MFA_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise ApiError(400, "The sign-in step expired. Enter your email and password again.", "mfa_expired")
    with db.engine.connect() as c:
        u = _get_user(c, id=data.get("uid"))
    if not u or u.disabled or not u.mfa_enabled or data.get("pv") != _pv(u):
        raise ApiError(400, "The sign-in step expired. Enter your email and password again.", "mfa_expired")
    if u.locked_until and u.locked_until > db.now_us():
        raise ApiError(429, security.lock_message(u.locked_until), "locked")
    step = security.verify_totp(security.decrypt(u.mfa_secret), d["code"], u.mfa_last_step)
    values = {}
    if step is not None:
        values["mfa_last_step"] = step
    else:
        remaining = security.use_recovery_code(u.mfa_recovery, d["code"])
        if remaining is None:
            until = security.register_failed_login(u)
            audit("login.mfa_failed", target_type="user", target_id=u.id)
            if until:
                raise ApiError(429, security.lock_message(until), "locked")
            raise ApiError(401, "That code isn't right. Check your authenticator app and try again.", "bad_code")
        values["mfa_recovery"] = json.dumps(remaining)
        audit("login.recovery_code_used", actor_id=u.id, target_type="user", target_id=u.id, remaining=len(remaining))
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == u.id).values(**values))
    return _finish_login(u)


@bp.post("/logout")
def logout():
    sess, user = security.load_session()
    if sess:
        security.check_csrf(sess)
        with db.engine.begin() as c:
            c.execute(delete(db.sessions).where(db.sessions.c.id == sess.id))
        audit("logout", actor_id=user.id)
    resp = make_response("", 204)
    return security.clear_session_cookie(resp)


@bp.post("/logout-all")
@auth()
def logout_all():
    n = security.revoke_sessions(g.user.id)
    audit("logout.all", sessions=n)
    return security.clear_session_cookie(make_response("", 204))


@bp.get("/me")
@auth()
def me():
    return jsonify(user=user_dict(g.user), csrf=g.session.csrf)


@bp.get("/config")
def public_config():
    """What the sign-in screen needs to know (no secrets)."""
    with db.engine.connect() as c:
        has_users = c.execute(select(db.users.c.id).limit(1)).first() is not None
    return jsonify(registration_open=settings.REGISTRATION_OPEN or (not has_users and settings.BOOTSTRAP_FIRST_ADMIN),
                   email_verification=settings.REQUIRE_EMAIL_VERIFICATION, password_min=8)


@bp.post("/forgot")
def forgot_password():
    d = parse(request, {"email": Str(max=255, required=True, label="Email")})
    email = d["email"].lower()
    enforce_rate("forgot-email", 3, 3600, key=email)  # no mail-bombing one address
    with db.engine.connect() as c:
        u = _get_user(c, email=email)
    if u and not u.disabled:
        token = _serializer("password-reset").dumps({"uid": u.id, "pv": _pv(u)})
        send_account_email(u.email, "Reset your Nova password", "Use this link within 1 hour to reset your password:",
                           f"{settings.APP_URL}/?reset={token}")
        audit("password.reset_requested", actor_id=u.id, target_type="user", target_id=u.id)
    # Same answer whether or not the account exists
    return jsonify(ok=True, message="If an account exists for that email, a reset link has been sent.")


@bp.post("/reset")
def reset_password():
    d = parse(request, {"token": Str(max=500, required=True), "password": Str(max=200, strip=False, required=True,
                                                                               label="Password")})
    try:
        data = _serializer("password-reset").loads(d["token"], max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise ApiError(400, "This reset link is invalid or has expired. Request a new one.", "bad_token")
    with db.engine.begin() as c:
        u = _get_user(c, id=data.get("uid"))
        if not u or u.disabled or data.get("pv") != _pv(u):  # link is single-use
            raise ApiError(400, "This reset link is invalid or has expired. Request a new one.", "bad_token")
        security.check_password_policy(d["password"], u.email)
        c.execute(update(db.users).where(db.users.c.id == u.id).values(
            password_hash=security.hash_password(d["password"]), password_changed_at=db.now_us(),
            failed_logins=0, locked_until=None, email_verified=1))  # the link proved control of the address
        security.revoke_sessions(u.id, conn=c)  # sign out everywhere else
        u = _get_user(c, id=u.id)
    audit("password.reset", actor_id=u.id, target_type="user", target_id=u.id)
    if u.mfa_enabled:
        return jsonify(ok=True, message="Password changed. Sign in with your new password and your authenticator code.")
    return session_response(u)


@bp.post("/verify")
def verify_email():
    d = parse(request, {"token": Str(max=500, required=True)})
    try:
        data = _serializer("verify-email").loads(d["token"], max_age=VERIFY_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise ApiError(400, "This verification link is invalid or has expired. Request a new one.", "bad_token")
    with db.engine.begin() as c:
        u = _get_user(c, id=data.get("uid"))
        if not u or u.email != data.get("email"):
            raise ApiError(400, "This verification link is invalid or has expired. Request a new one.", "bad_token")
        values = {"email_verified": 1}
        if admin_on_verify(u.email) and u.role != "admin":
            values["role"] = "admin"
        c.execute(update(db.users).where(db.users.c.id == u.id).values(**values))
    audit("email.verified", actor_id=u.id, target_type="user", target_id=u.id, promoted=values.get("role") == "admin")
    return jsonify(ok=True, message="Your email is verified. You can sign in now.")


@bp.post("/resend-verification")
def resend_verification():
    d = parse(request, {"email": Str(max=255, required=True, label="Email")})
    email = d["email"].lower()
    enforce_rate("verify-email", 3, 3600, key=email)
    with db.engine.connect() as c:
        u = _get_user(c, email=email)
    if u and not u.email_verified and not u.disabled:
        send_verification(u)
    return jsonify(ok=True, message="If that account needs verifying, we've sent a new link.")
