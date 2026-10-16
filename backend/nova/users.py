"""User records: public representation, synced settings and admin bootstrap rules."""
import json

from sqlalchemy import func, select, update

from . import db
from .config import settings

LANG_PATTERN = r"[a-z]{2,3}(-[A-Z]{2})?"
DEFAULT_SETTINGS = {"theme": "dark", "voice_lang": "", "auto_read": False, "agent_tools": True, "agent_mode": ""}


def user_settings(u):
    try:
        saved = json.loads(u.settings or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}


def user_dict(u):
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
            "email_verified": bool(u.email_verified), "mfa_enabled": bool(u.mfa_enabled),
            "memory_enabled": bool(u.memory_enabled), "created_at": u.created_at, "settings": user_settings(u)}


def admin_on_register(email, first):
    """Who becomes an administrator at registration. Production never trusts an unverified email address."""
    if first and settings.BOOTSTRAP_FIRST_ADMIN:
        return True
    if email in settings.ADMIN_EMAILS and not settings.REQUIRE_EMAIL_VERIFICATION and not settings.IS_PROD:
        return True
    return False


def admin_on_verify(email):
    return email in settings.ADMIN_EMAILS and settings.REQUIRE_EMAIL_VERIFICATION


def admin_count(c):
    return c.execute(select(func.count()).select_from(db.users).where(db.users.c.role == "admin",
                                                                       db.users.c.disabled == 0)).scalar()


def adopt_orphan_chats(c, uid):
    """Chats created before accounts existed belong to the first administrator."""
    c.execute(update(db.conversations).where(db.conversations.c.user_id.is_(None)).values(user_id=uid))
