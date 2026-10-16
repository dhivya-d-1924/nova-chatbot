"""Authentication primitives: server-side sessions in HttpOnly cookies, CSRF tokens, rate limiting,
login throttling, TOTP multi-factor auth, field encryption, audit logging and security headers."""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import struct
import threading
import time
import urllib.parse
import uuid
from collections import defaultdict, deque
from functools import wraps

from flask import g, has_request_context, jsonify, request
from sqlalchemy import delete, insert, select, update
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .config import settings
from .errors import ApiError

log = logging.getLogger("nova.security")
SESSION_COOKIE = "nova_session"
US = 10**6

# ------------------------------------------------------------------ passwords
COMMON_PASSWORDS = set("""
password password1 password12 password123 passw0rd 12345678 123456789 1234567890 qwerty123 qwertyuiop
iloveyou letmein1 welcome1 admin123 administrator abc12345 11111111 00000000 sunshine1 princess1 football1
baseball1 monkey123 dragon123 trustno1 changeme changeme1 superman1 starwars1 qwerty12 asdfghjk zxcvbnm1
""".split())
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def hash_password(pw):
    return generate_password_hash(pw)  # scrypt with a per-password salt (werkzeug default)


def verify_password(pw_hash, pw):
    """Constant work whether or not the account exists, so timing does not reveal registered emails."""
    if not pw_hash:
        check_password_hash(_DUMMY_HASH, pw or "")
        return False
    return check_password_hash(pw_hash, pw or "")


def check_password_policy(pw, email=""):
    if not isinstance(pw, str) or len(pw) < 8:
        raise ApiError(400, "Password must be at least 8 characters.", "weak_password")
    if len(pw) > 128:
        raise ApiError(400, "Password is too long (max 128 characters).", "weak_password")
    low = pw.lower()
    if low in COMMON_PASSWORDS or len(set(pw)) < 4:
        raise ApiError(400, "That password is too common. Choose something harder to guess.", "weak_password")
    if email and low == email.lower():
        raise ApiError(400, "Your password can't be your email address.", "weak_password")


# ------------------------------------------------------------------ request metadata
def client_ip():
    """remote_addr after ProxyFix (TRUSTED_PROXY_COUNT): X-Forwarded-For is only honoured from trusted proxies."""
    return (request.remote_addr or "?")[:64]


# ------------------------------------------------------------------ rate limiting
class MemoryLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._counters = defaultdict(int)
        self._lock = threading.Lock()

    def hit(self, key, limit, window):
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50000:  # bound memory on a long-running worker
                for k in [k for k, v in self._hits.items() if not v][:10000]:
                    self._hits.pop(k, None)
            return True

    def acquire(self, key, limit, ttl):
        with self._lock:
            if self._counters[key] >= limit:
                return False
            self._counters[key] += 1
            return True

    def release(self, key):
        with self._lock:
            self._counters[key] = max(0, self._counters[key] - 1)

    def reset(self):
        with self._lock:
            self._hits.clear()
            self._counters.clear()


class RedisLimiter:
    """Shared across workers and servers. Fixed windows keyed by the window number."""

    def __init__(self, url):
        import redis  # optional dependency
        self.r = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    def hit(self, key, limit, window):
        bucket = f"rl:{key}:{int(time.time() // window)}"
        try:
            pipe = self.r.pipeline()
            pipe.incr(bucket)
            pipe.expire(bucket, window + 1)
            count = pipe.execute()[0]
        except Exception:  # noqa: BLE001  fail open on a Redis outage, but say so
            log.warning("rate limiter: Redis unavailable, allowing request")
            return True
        return count <= limit

    def acquire(self, key, limit, ttl):
        k = f"cc:{key}"
        try:
            n = self.r.incr(k)
            self.r.expire(k, ttl)
            if n > limit:
                self.r.decr(k)
                return False
            return True
        except Exception:  # noqa: BLE001
            return True

    def release(self, key):
        try:
            if self.r.decr(f"cc:{key}") < 0:
                self.r.set(f"cc:{key}", 0)
        except Exception:  # noqa: BLE001, S110  best effort; the key expires on its own
            pass

    def reset(self):
        for pattern in ("rl:*", "cc:*"):
            for k in self.r.scan_iter(pattern):
                self.r.delete(k)


limiter = MemoryLimiter()


def init_limiter():
    global limiter
    if settings.REDIS_URL:
        try:
            lim = RedisLimiter(settings.REDIS_URL)
            lim.r.ping()
            limiter = lim
            log.info("rate limiter: using Redis")
            return
        except Exception as e:  # noqa: BLE001
            if settings.IS_PROD:
                raise RuntimeError(f"REDIS_URL is set but Redis is not reachable: {e}") from e
            log.warning("rate limiter: Redis not reachable (%s); using in-memory limits", e)
    limiter = MemoryLimiter()


def enforce_rate(bucket, limit, window=60, key=None):
    if not limiter.hit(f"{bucket}:{key or client_ip()}", limit, window):
        raise ApiError(429, "Too many requests. Wait a minute and try again.", "rate_limited")


# ------------------------------------------------------------------ sessions
def _token_id(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    now = db.now_us()
    row = {"id": _token_id(token), "user_id": user_id, "csrf": secrets.token_urlsafe(24), "created_at": now,
           "last_seen": now, "expires_at": now + settings.SESSION_DAYS * 86400 * US, "ip": client_ip(),
           "user_agent": (request.headers.get("User-Agent") or "")[:200]}
    with db.engine.begin() as c:
        c.execute(insert(db.sessions).values(**row))
    return token, row


def set_session_cookie(resp, token):
    resp.set_cookie(SESSION_COOKIE, token, max_age=settings.SESSION_DAYS * 86400, httponly=True,
                    secure=settings.COOKIE_SECURE, samesite=settings.COOKIE_SAMESITE, path="/api")
    return resp


def clear_session_cookie(resp):
    resp.delete_cookie(SESSION_COOKIE, path="/api", secure=settings.COOKIE_SECURE, samesite=settings.COOKIE_SAMESITE)
    return resp


def revoke_sessions(user_id, except_id=None, conn=None):
    q = delete(db.sessions).where(db.sessions.c.user_id == user_id)
    if except_id:
        q = q.where(db.sessions.c.id != except_id)
    if conn is not None:
        return conn.execute(q).rowcount
    with db.engine.begin() as c:
        return c.execute(q).rowcount


def load_session():
    """Return (session_row, user_row) for the request's cookie, or (None, None)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 200:
        return None, None
    sid = _token_id(token)
    now = db.now_us()
    with db.engine.connect() as c:
        row = c.execute(select(db.sessions, db.users).join(db.users, db.users.c.id == db.sessions.c.user_id)
                        .where(db.sessions.c.id == sid)).first()
    if not row:
        return None, None
    s = row._mapping
    idle = settings.SESSION_IDLE_HOURS * 3600 * US
    if s["expires_at"] < now or now - s["last_seen"] > idle or s["disabled"]:
        with db.engine.begin() as c:
            c.execute(delete(db.sessions).where(db.sessions.c.id == sid))
        return None, None
    if now - s["last_seen"] > 5 * 60 * US:  # throttle writes
        with db.engine.begin() as c:
            c.execute(update(db.sessions).where(db.sessions.c.id == sid).values(last_seen=now, ip=client_ip()))
    with db.engine.connect() as c:
        sess = c.execute(select(db.sessions).where(db.sessions.c.id == sid)).first()
        user = c.execute(select(db.users).where(db.users.c.id == s["user_id"])).first()
    return sess, user


def _origin_allowed():
    origin = request.headers.get("Origin")
    if not origin:
        return True  # same-origin navigations and non-browser clients; the CSRF token still applies
    allowed = set(settings.ALLOWED_ORIGINS)
    if settings.APP_URL:
        p = urllib.parse.urlparse(settings.APP_URL)
        allowed.add(f"{p.scheme}://{p.netloc}")
    allowed.add(request.host_url.rstrip("/"))
    return origin.rstrip("/") in allowed


def check_csrf(sess):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get("X-CSRF-Token", "")
    if not sent or not hmac.compare_digest(sent, sess.csrf):
        raise ApiError(403, "Your session token is missing or out of date. Reload the page and try again.", "csrf")


def auth(admin=False):
    """Require a signed-in user (and optionally the admin role). Applies CSRF checks to unsafe methods."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            sess, user = load_session()
            if not user:
                raise ApiError(401, "Please sign in.", "unauthenticated")
            check_csrf(sess)
            g.session, g.user = sess, user
            if admin and user.role != "admin":
                audit("access.denied", target_type="route", target_id=request.path[:64])
                raise ApiError(403, "Administrator access required.", "forbidden")
            return fn(*a, **kw)
        return wrapper
    return deco


def origin_guard():
    """before_request: block cross-site state-changing requests from origins that are not allowed."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.path.startswith("/api/") and not _origin_allowed():
        raise ApiError(403, "Requests from this site are not allowed.", "bad_origin")


# ------------------------------------------------------------------ login throttling
def register_failed_login(user):
    fails = (user.failed_logins or 0) + 1
    values = {"failed_logins": fails}
    if fails >= settings.LOGIN_MAX_FAILURES:
        # progressive: each further failure doubles the lockout, capped at 24 hours
        extra = min(2 ** (fails - settings.LOGIN_MAX_FAILURES), 96)
        values["locked_until"] = db.now_us() + settings.LOGIN_LOCKOUT_MINUTES * 60 * US * extra
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == user.id).values(**values))
    return values.get("locked_until")


def clear_failed_logins(user_id, conn):
    conn.execute(update(db.users).where(db.users.c.id == user_id).values(failed_logins=0, locked_until=None))


def lock_message(until_us):
    minutes = max(1, int((until_us - db.now_us()) / 60 / US) + 1)
    return f"Too many failed sign-in attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''} or reset your password."


# ------------------------------------------------------------------ field encryption
def _fernet():
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(("nova-field-key:" + settings.DATA_ENCRYPTION_KEY).encode()).digest())
    return Fernet(key)


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value):
    return _fernet().decrypt(value.encode()).decode()


# ------------------------------------------------------------------ TOTP (RFC 6238)
def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_now(secret, at=None):
    return _hotp(secret, int((at or time.time()) // 30))


def verify_totp(secret, code, last_step=None, window=1):
    """Return the matched time step, or None. Steps at or before last_step are rejected (no replay)."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    step = int(time.time() // 30)
    for s in range(step - window, step + window + 1):
        if last_step is not None and s <= last_step:
            continue
        if hmac.compare_digest(_hotp(secret, s), code):
            return s
    return None


def totp_uri(secret, email):
    label = urllib.parse.quote(f"{settings.MFA_ISSUER}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={urllib.parse.quote(settings.MFA_ISSUER)}&digits=6&period=30"


def new_recovery_codes(n=8):
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return codes, [hashlib.sha256(c.encode()).hexdigest() for c in codes]


def use_recovery_code(stored_json, code):
    """Return the remaining hashes if code matched, else None."""
    hashes = json.loads(stored_json or "[]")
    h = hashlib.sha256(str(code or "").strip().lower().encode()).hexdigest()
    if h in hashes:
        hashes.remove(h)
        return hashes
    return None


# ------------------------------------------------------------------ audit log
def audit(action, actor_id=None, target_type=None, target_id=None, **detail):
    """Record a security-relevant event. Never pass passwords, tokens or message contents."""
    if has_request_context():
        actor = actor_id if actor_id is not None else getattr(getattr(g, "user", None), "id", None)
        ip = client_ip()
    else:
        actor, ip = actor_id, None
    try:
        with db.engine.begin() as c:
            c.execute(insert(db.audit_log).values(
                id=str(uuid.uuid4()), created_at=db.now_us(), actor_id=actor, action=action,
                target_type=target_type, target_id=str(target_id)[:64] if target_id else None, ip=ip,
                detail=json.dumps(detail, default=str)[:1000] if detail else None))
    except Exception:  # noqa: BLE001
        log.exception("could not write audit log")
    log.info("audit %s", action, extra={"audit": {"action": action, "actor": actor, "target": target_id}})


# ------------------------------------------------------------------ headers
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; media-src 'self' blob:; "
       "connect-src 'self'{extra}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def security_headers(resp):
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if settings.COOKIE_SECURE:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
        h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    else:
        extra = "".join(f" {o}" for o in settings.ALLOWED_ORIGINS)
        h.setdefault("Content-Security-Policy", CSP.format(extra=extra))
    return resp


def json_error(status, message, code=None):
    body = {"error": message}
    if code:
        body["code"] = code
    return jsonify(body), status
