"""Environment-based configuration with validation at startup.

APP_ENV selects the profile: development (default), testing or production.
Production refuses to start with unsafe settings instead of silently running insecurely.
"""
import logging
import os
import secrets
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"
DIST = BACKEND_DIR.parent / "frontend" / "dist"
log = logging.getLogger("nova.config")


class ConfigError(RuntimeError):
    """Raised when the configuration is unsafe or invalid for the selected environment."""


def _load_env_file():
    try:
        load_dotenv(ENV_PATH, encoding="utf-8-sig")  # utf-8-sig also handles a BOM from Notepad/PowerShell
    except UnicodeDecodeError:
        print(f"\n!! Could not read {ENV_PATH}: save it as UTF-8 and restart.\n")


class Settings:
    """Typed view over environment variables. Call load() to (re)read them."""

    def __init__(self):
        self.errors, self.warnings = [], []
        self._src = {}

    # ---- readers
    def _raw(self, name, default=""):
        v = self._src.get(name, os.environ.get(name))
        return default if v is None else str(v)

    def _str(self, name, default=""):
        return self._raw(name, "").strip() or default  # an empty value means "use the default"

    def _bool(self, name, default=False):
        v = self._str(name, "")
        if not v:
            return default
        if v.lower() in ("1", "true", "yes", "on"):
            return True
        if v.lower() in ("0", "false", "no", "off"):
            return False
        self.errors.append(f"{name} must be true or false (got '{v}').")
        return default

    def _int(self, name, default, lo=None, hi=None):
        v = self._str(name, "")
        if not v:
            return default
        try:
            n = int(v)
        except ValueError:
            self.errors.append(f"{name} must be a whole number (got '{v}').")
            return default
        if (lo is not None and n < lo) or (hi is not None and n > hi):
            self.errors.append(f"{name} must be between {lo} and {hi} (got {n}).")
            return default
        return n

    def _float(self, name, default):
        v = self._str(name, "")
        if not v:
            return default
        try:
            return float(v)
        except ValueError:
            self.errors.append(f"{name} must be a number (got '{v}').")
            return default

    def _list(self, name, default=""):
        return [x.strip() for x in self._str(name, default).split(",") if x.strip()]

    # ---- load
    def load(self, overrides=None, read_env_file=True):
        if read_env_file and not (overrides or {}).get("_SKIP_ENV_FILE"):
            _load_env_file()
        self._src = {k: v for k, v in (overrides or {}).items() if not k.startswith("_")}
        self.errors, self.warnings = [], []

        env = self._str("APP_ENV", "development").lower()
        if env not in ("development", "testing", "production"):
            self.errors.append("APP_ENV must be development, testing or production.")
            env = "development"
        self.APP_ENV = env
        self.IS_PROD = env == "production"
        self.IS_TEST = env == "testing"
        prod = self.IS_PROD

        # --- secrets
        self.SECRET_KEY = self._str("SECRET_KEY")
        if not self.SECRET_KEY:
            if prod:
                self.errors.append("SECRET_KEY must be set in production "
                                   "(python -c \"import secrets;print(secrets.token_hex(32))\").")
            self.SECRET_KEY = secrets.token_hex(32) if self.IS_TEST else _dev_secret()
        elif prod and (len(self.SECRET_KEY) < 32 or self.SECRET_KEY in ("change-me", "secret", "dev")):
            self.errors.append("SECRET_KEY is too short or a placeholder; use at least 32 random characters.")
        self.DATA_ENCRYPTION_KEY = self._str("DATA_ENCRYPTION_KEY") or self.SECRET_KEY

        # --- LLM provider
        self.LLM_API_KEY = self._str("LLM_API_KEY")
        self.LLM_BASE_URL = self._str("LLM_BASE_URL") or None
        self.LLM_MODEL = self._str("LLM_MODEL", "gpt-4o-mini")
        self.SYSTEM_PROMPT = self._raw("SYSTEM_PROMPT", "You are a helpful, accurate AI assistant.").strip() \
            or "You are a helpful, accurate AI assistant."
        self.LLM_TIMEOUT = self._int("LLM_TIMEOUT_SECONDS", 60, 5, 600)
        self.LLM_MAX_RETRIES = self._int("LLM_MAX_RETRIES", 2, 0, 6)
        self.LLM_STREAM_USAGE = self._bool("LLM_STREAM_USAGE", True)
        self.PRICE_IN = self._float("LLM_PRICE_INPUT_PER_1M", 0.0)
        self.PRICE_OUT = self._float("LLM_PRICE_OUTPUT_PER_1M", 0.0)
        self.EMBEDDING_MODEL = self._str("EMBEDDING_MODEL")
        self.EMBEDDING_BASE_URL = self._str("EMBEDDING_BASE_URL") or self.LLM_BASE_URL
        self.EMBEDDING_API_KEY = self._str("EMBEDDING_API_KEY") or self.LLM_API_KEY
        if self.LLM_BASE_URL and not _is_http_url(self.LLM_BASE_URL):
            self.errors.append("LLM_BASE_URL must be an http(s) URL.")
        self.LLM_CONFIGURED = bool(self.LLM_API_KEY or self.LLM_BASE_URL)
        if not self.LLM_CONFIGURED:
            self.warnings.append("LLM_API_KEY is empty: chat requests will return a clear 'not configured' error.")

        # --- database
        self.DATABASE_URL = self._str("DATABASE_URL", "sqlite:///chat.db")
        if self.DATABASE_URL.startswith("sqlite:///") and not self.DATABASE_URL.startswith("sqlite:////"):
            rel = self.DATABASE_URL[len("sqlite:///"):]
            if rel and rel != ":memory:":  # relative paths resolve next to the backend, not the shell's cwd
                self.DATABASE_URL = "sqlite:///" + str((BACKEND_DIR / rel).resolve())
        if prod and self.DATABASE_URL.startswith("sqlite") and ":memory:" in self.DATABASE_URL:
            self.errors.append("An in-memory database cannot be used in production.")

        # --- web / origins / cookies
        default_origin = "" if prod else "http://localhost:5173"
        self.ALLOWED_ORIGINS = self._list("ALLOWED_ORIGINS", default_origin)
        for o in self.ALLOWED_ORIGINS:
            if o == "*":
                self.errors.append("ALLOWED_ORIGINS cannot be '*' (credentials are sent with requests).")
            elif not _is_http_url(o) or urllib.parse.urlparse(o).path not in ("", "/"):
                self.errors.append(f"ALLOWED_ORIGINS entry '{o}' must look like https://app.example.com")
        self.APP_URL = self._str("APP_URL", "" if prod else "http://localhost:5173").rstrip("/")
        if prod and not self.APP_URL:
            self.errors.append("APP_URL must be set in production (used in email links and origin checks).")
        elif self.APP_URL and not _is_http_url(self.APP_URL):
            self.errors.append("APP_URL must be an http(s) URL.")
        self.ALLOW_HTTP = self._bool("ALLOW_INSECURE_HTTP", not prod)
        if prod and self.APP_URL.startswith("http://") and not self.ALLOW_HTTP:
            self.errors.append("APP_URL must use https in production (set ALLOW_INSECURE_HTTP=true only for a local test).")
        self.COOKIE_SECURE = self._bool("COOKIE_SECURE", prod and not self.ALLOW_HTTP)
        self.COOKIE_SAMESITE = self._str("COOKIE_SAMESITE", "Lax").capitalize()
        if self.COOKIE_SAMESITE not in ("Lax", "Strict", "None"):
            self.errors.append("COOKIE_SAMESITE must be Lax, Strict or None.")
        if self.COOKIE_SAMESITE == "None" and not self.COOKIE_SECURE:
            self.errors.append("COOKIE_SAMESITE=None requires COOKIE_SECURE=true (HTTPS).")
        self.TRUSTED_PROXY_COUNT = self._int("TRUSTED_PROXY_COUNT", 0, 0, 5)
        self.FLASK_DEBUG = self._bool("FLASK_DEBUG", False)
        if prod and self.FLASK_DEBUG:
            self.errors.append("FLASK_DEBUG must be off in production.")
        self.PORT = self._int("PORT", 5000, 1, 65535)

        # --- sessions & accounts
        self.SESSION_DAYS = self._int("SESSION_DAYS", 30, 1, 365)
        self.SESSION_IDLE_HOURS = self._int("SESSION_IDLE_HOURS", 7 * 24, 1, 24 * 365)
        self.REGISTRATION_OPEN = self._bool("REGISTRATION_OPEN", True)
        self.ADMIN_EMAILS = {e.lower() for e in self._list("ADMIN_EMAILS")}
        self.BOOTSTRAP_FIRST_ADMIN = self._bool("BOOTSTRAP_FIRST_ADMIN", not prod)
        if prod and self.BOOTSTRAP_FIRST_ADMIN:
            self.warnings.append("BOOTSTRAP_FIRST_ADMIN is on in production: the first registrant becomes admin.")
        self.SMTP_HOST = self._str("SMTP_HOST")
        self.SMTP_PORT = self._int("SMTP_PORT", 587, 1, 65535)
        self.SMTP_USER = self._str("SMTP_USER")
        self.SMTP_PASSWORD = self._raw("SMTP_PASSWORD", "")
        self.SMTP_FROM = self._str("SMTP_FROM") or self.SMTP_USER
        self.SMTP_TLS = self._str("SMTP_TLS", "starttls").lower()  # starttls | ssl | none
        self.REQUIRE_EMAIL_VERIFICATION = self._bool("REQUIRE_EMAIL_VERIFICATION", prod and bool(self.SMTP_HOST))
        if self.REQUIRE_EMAIL_VERIFICATION and not self.SMTP_HOST and not prod:
            self.warnings.append("REQUIRE_EMAIL_VERIFICATION is on without SMTP: verification links are printed in the console.")
        if self.REQUIRE_EMAIL_VERIFICATION and not self.SMTP_HOST and prod:
            self.errors.append("REQUIRE_EMAIL_VERIFICATION needs SMTP_HOST in production, or nobody can verify.")
        if prod and not self.SMTP_HOST:
            self.warnings.append("SMTP_HOST is empty: password-reset and verification emails cannot be delivered.")
        if prod and self.ADMIN_EMAILS and not self.REQUIRE_EMAIL_VERIFICATION:
            self.warnings.append("ADMIN_EMAILS is ignored in production unless REQUIRE_EMAIL_VERIFICATION is on "
                                 "(otherwise anyone could register that address). Use: flask --app app create-admin")
        self.LOGIN_MAX_FAILURES = self._int("LOGIN_MAX_FAILURES", 5, 1, 100)
        self.LOGIN_LOCKOUT_MINUTES = self._int("LOGIN_LOCKOUT_MINUTES", 15, 1, 24 * 60)
        self.MFA_ISSUER = self._str("MFA_ISSUER", "Nova")

        # --- rate limits & quotas
        self.REDIS_URL = self._str("REDIS_URL")
        self.RATE_LIMIT = self._int("RATE_LIMIT_PER_MINUTE", 20, 1, 10000)
        self.AUTH_RATE_LIMIT = self._int("AUTH_RATE_LIMIT_PER_MINUTE", 10, 1, 10000)
        self.UPLOAD_RATE_LIMIT = self._int("UPLOAD_RATE_LIMIT_PER_MINUTE", 20, 1, 10000)
        self.REGISTER_RATE_LIMIT = self._int("REGISTER_RATE_LIMIT_PER_HOUR", 10, 1, 100000)
        self.API_RATE_LIMIT = self._int("API_RATE_LIMIT_PER_MINUTE", 300, 1, 100000)
        self.MAX_CONCURRENT_STREAMS = self._int("MAX_CONCURRENT_STREAMS_PER_USER", 2, 1, 50)
        self.DAILY_TOKEN_LIMIT = self._int("DAILY_TOKEN_LIMIT_PER_USER", 0, 0)
        self.MONTHLY_BUDGET_USD = self._float("MONTHLY_BUDGET_USD", 0.0)
        workers = self._int("WEB_CONCURRENCY", 1, 1, 256)
        if prod and workers > 1 and not self.REDIS_URL:
            self.warnings.append("Several workers without REDIS_URL: rate limits and quotas are counted per worker.")

        # --- chat, documents, agent
        self.MAX_HISTORY = self._int("MAX_HISTORY_MESSAGES", 30, 2, 500)
        self.MAX_CHARS = self._int("MAX_MESSAGE_CHARS", 8000, 100, 200000)
        self.SUMMARIZE_HISTORY = self._bool("SUMMARIZE_HISTORY", True)
        self.MAX_UPLOAD_MB = self._int("MAX_UPLOAD_MB", 15, 1, 200)
        self.MAX_DOCS_PER_USER = self._int("MAX_DOCS_PER_USER", 30, 1, 10000)
        self.MAX_PDF_PAGES = self._int("MAX_PDF_PAGES", 500, 1, 10000)
        self.RAG_TOP_K = self._int("RAG_TOP_K", 6, 1, 30)
        self.OCR_ENABLED = self._bool("OCR_ENABLED", True)
        self.OCR_MAX_PAGES = self._int("OCR_MAX_PAGES", 30, 1, 1000)
        self.AGENT_MAX_STEPS = self._int("AGENT_MAX_STEPS", 6, 1, 30)
        self.AGENT_TIMEOUT = self._int("AGENT_TIMEOUT_SECONDS", 180, 10, 3600)
        self.TOOL_TIMEOUT = self._int("TOOL_TIMEOUT_SECONDS", 30, 1, 600)
        self.APPROVAL_TTL_MINUTES = self._int("APPROVAL_TTL_MINUTES", 60, 1, 7 * 24 * 60)
        self.MAX_IMAGE_MB = self._int("MAX_IMAGE_MB", 5, 1, 50)
        self.MEMORY_LIMIT = self._int("MEMORY_LIMIT", 100, 1, 10000)

        # --- retention, logging, alerts
        self.LOG_RETENTION_DAYS = self._int("LOG_RETENTION_DAYS", 90, 1, 3650)
        self.CHAT_RETENTION_DAYS = self._int("CHAT_RETENTION_DAYS", 0, 0, 36500)  # 0 = keep until the user deletes
        self.LOG_FORMAT = self._str("LOG_FORMAT", "json" if prod else "text").lower()
        self.LOG_LEVEL = self._str("LOG_LEVEL", "INFO").upper()
        self.ALERT_WEBHOOK_URL = self._str("ALERT_WEBHOOK_URL")
        self.ALERT_EMAIL = self._str("ALERT_EMAIL")
        self.ALERT_ERROR_THRESHOLD = self._int("ALERT_ERROR_THRESHOLD", 20, 1, 100000)
        if self.ALERT_WEBHOOK_URL and not _is_http_url(self.ALERT_WEBHOOK_URL):
            self.errors.append("ALERT_WEBHOOK_URL must be an http(s) URL.")

        # --- internal app integrations and Google Workspace
        self.INTEGRATION_TIMEOUT = self._int("INTEGRATION_TIMEOUT_SECONDS", 15, 1, 120)
        self.INTEGRATION_ALLOWED_HOSTS = [h.lower() for h in self._list("INTEGRATION_ALLOWED_HOSTS")]
        self.GOOGLE_CLIENT_ID = self._str("GOOGLE_CLIENT_ID")
        self.GOOGLE_CLIENT_SECRET = self._raw("GOOGLE_CLIENT_SECRET", "").strip()
        self.GOOGLE_ALLOWED_DOMAIN = self._str("GOOGLE_ALLOWED_DOMAIN").lower().lstrip("@")
        self.GOOGLE_ENABLED = bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)
        if bool(self.GOOGLE_CLIENT_ID) != bool(self.GOOGLE_CLIENT_SECRET):
            (self.errors if prod else self.warnings).append("Google Workspace needs both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")

        # --- Microsoft 365 (Entra ID) for Outlook Calendar, Teams and automatic replies
        self.MS_CLIENT_ID = self._str("MS_CLIENT_ID")
        self.MS_CLIENT_SECRET = self._raw("MS_CLIENT_SECRET", "").strip()
        self.MS_TENANT_ID = self._str("MS_TENANT_ID", "organizations")
        self.MS_EXTRA_SCOPES = [x for x in self._str("MS_EXTRA_SCOPES").replace(",", " ").split() if x]
        self.MS_ENABLED = bool(self.MS_CLIENT_ID and self.MS_CLIENT_SECRET)
        if bool(self.MS_CLIENT_ID) != bool(self.MS_CLIENT_SECRET):
            (self.errors if prod else self.warnings).append("Microsoft 365 needs both MS_CLIENT_ID and MS_CLIENT_SECRET.")
        if prod and self.MS_ENABLED and self.MS_TENANT_ID in ("common", "consumers"):
            self.errors.append("MS_TENANT_ID must be your organisation's tenant id (or 'organizations') in production.")
        self.WORKSPACE_JOBS = self._bool("WORKSPACE_JOBS", True)
        self.WORKSPACE_JOB_INTERVAL = self._int("WORKSPACE_JOB_INTERVAL_SECONDS", 60, 10, 3600)
        # activity history, notifications, finished approvals and past reminders older than this are deleted
        self.WORKSPACE_RETENTION_DAYS = self._int("WORKSPACE_RETENTION_DAYS", 180, 7, 3650)
        # Simulated Gmail/Teams/Calendar accounts with sample data, to try the workspace without real accounts
        self.WORKSPACE_DEMO = self._bool("WORKSPACE_DEMO", False)
        if self.WORKSPACE_DEMO:
            if prod:
                self.errors.append("WORKSPACE_DEMO must not be used in production (it replaces Google and Microsoft with sample data).")
            if not self.GOOGLE_ENABLED:
                self.GOOGLE_CLIENT_ID, self.GOOGLE_CLIENT_SECRET, self.GOOGLE_ENABLED = "demo", "demo", True
            if not self.MS_ENABLED:
                self.MS_CLIENT_ID, self.MS_CLIENT_SECRET, self.MS_ENABLED = "demo", "demo", True
            self.warnings.append("WORKSPACE_DEMO is on: Google and Microsoft are simulated with sample data; nothing reaches real accounts.")

        # --- channels: WhatsApp, Microsoft Teams, email (each turns on when its credentials are set)
        self.CHANNEL_LINK_CODE_MINUTES = self._int("CHANNEL_LINK_CODE_MINUTES", 10, 1, 120)
        self.CHANNEL_DOCUMENTS = self._str("CHANNEL_DOCUMENTS", "command").lower()  # command ("docs ..."), always, off
        if self.CHANNEL_DOCUMENTS not in ("command", "always", "off"):
            self.errors.append("CHANNEL_DOCUMENTS must be command, always or off.")
            self.CHANNEL_DOCUMENTS = "command"
        self.CHANNEL_RATE_LIMIT = self._int("CHANNEL_RATE_LIMIT_PER_MINUTE", 10, 1, 1000)
        wa = {k: self._str(k) for k in ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_APP_SECRET",
                                       "WHATSAPP_VERIFY_TOKEN")}
        self.WHATSAPP_ACCESS_TOKEN = wa["WHATSAPP_ACCESS_TOKEN"]
        self.WHATSAPP_PHONE_NUMBER_ID = wa["WHATSAPP_PHONE_NUMBER_ID"]
        self.WHATSAPP_APP_SECRET = wa["WHATSAPP_APP_SECRET"]
        self.WHATSAPP_VERIFY_TOKEN = wa["WHATSAPP_VERIFY_TOKEN"]
        self.WHATSAPP_API_VERSION = self._str("WHATSAPP_API_VERSION", "v23.0")
        self.WHATSAPP_API_BASE = self._str("WHATSAPP_API_BASE", "https://graph.facebook.com").rstrip("/")
        self.WHATSAPP_DISPLAY_NUMBER = self._str("WHATSAPP_DISPLAY_NUMBER")
        self.WHATSAPP_ENABLED = all(wa.values())
        if any(wa.values()) and not self.WHATSAPP_ENABLED:
            missing = ", ".join(k for k, v in wa.items() if not v)
            (self.errors if prod else self.warnings).append(f"WhatsApp is partly configured; also set {missing}.")
        if self.WHATSAPP_VERIFY_TOKEN and len(self.WHATSAPP_VERIFY_TOKEN) < 16:
            self.errors.append("WHATSAPP_VERIFY_TOKEN must be at least 16 random characters.")

        self.TEAMS_APP_ID = self._str("TEAMS_APP_ID")
        self.TEAMS_APP_PASSWORD = self._raw("TEAMS_APP_PASSWORD", "").strip()
        self.TEAMS_TENANT_ID = self._str("TEAMS_TENANT_ID")
        self.TEAMS_BOT_NAME = self._str("TEAMS_BOT_NAME", "Nova")
        self.TEAMS_ENABLED = bool(self.TEAMS_APP_ID and self.TEAMS_APP_PASSWORD)
        if bool(self.TEAMS_APP_ID) != bool(self.TEAMS_APP_PASSWORD):
            (self.errors if prod else self.warnings).append("Teams needs both TEAMS_APP_ID and TEAMS_APP_PASSWORD.")
        if prod and self.TEAMS_ENABLED and not self.TEAMS_TENANT_ID:
            self.errors.append("TEAMS_TENANT_ID must be set in production so only your organisation can use the bot.")

        self.EMAIL_CHANNEL_IMAP_HOST = self._str("EMAIL_CHANNEL_IMAP_HOST")
        self.EMAIL_CHANNEL_IMAP_PORT = self._int("EMAIL_CHANNEL_IMAP_PORT", 993, 1, 65535)
        self.EMAIL_CHANNEL_USER = self._str("EMAIL_CHANNEL_USER")
        self.EMAIL_CHANNEL_PASSWORD = self._raw("EMAIL_CHANNEL_PASSWORD", "")
        self.EMAIL_CHANNEL_ADDRESS = (self._str("EMAIL_CHANNEL_ADDRESS") or self.EMAIL_CHANNEL_USER).lower()
        self.EMAIL_CHANNEL_NAME = self._str("EMAIL_CHANNEL_NAME", "Nova")
        self.EMAIL_CHANNEL_FOLDER = self._str("EMAIL_CHANNEL_FOLDER", "INBOX")
        self.EMAIL_CHANNEL_POLL_SECONDS = self._int("EMAIL_CHANNEL_POLL_SECONDS", 60, 10, 3600)
        self.EMAIL_CHANNEL_AUTHSERV_ID = self._str("EMAIL_CHANNEL_AUTHSERV_ID").lower()
        self.EMAIL_CHANNEL_POLLER = self._bool("EMAIL_CHANNEL_POLLER", True)
        self.EMAIL_CHANNEL_ENABLED = bool(self.EMAIL_CHANNEL_IMAP_HOST and self.EMAIL_CHANNEL_USER and self.EMAIL_CHANNEL_PASSWORD)
        if self.EMAIL_CHANNEL_ENABLED:
            if not self.SMTP_HOST:
                self.errors.append("The email channel needs SMTP_HOST to send replies.")
            if not self.EMAIL_CHANNEL_AUTHSERV_ID:
                self.errors.append("EMAIL_CHANNEL_AUTHSERV_ID is required (the receiving server's name in "
                                   "Authentication-Results, e.g. mx.google.com) so forged senders are rejected.")

        if prod and self.LLM_API_KEY in ("sk-your-key-here", "changeme"):
            self.errors.append("LLM_API_KEY still has the placeholder value from .env.example.")
        return self

    def validate(self):
        """Log warnings; raise ConfigError in production when there are errors (other profiles only warn)."""
        for w in self.warnings:
            log.warning("config: %s", w)
        if self.errors:
            msg = "Invalid configuration:\n  - " + "\n  - ".join(self.errors)
            if self.IS_PROD or self.IS_TEST:
                raise ConfigError(msg)
            log.error(msg)

    def public(self):
        """Non-secret summary that is safe to show administrators."""
        return {"env": self.APP_ENV, "model": self.LLM_MODEL, "llm_configured": self.LLM_CONFIGURED,
                "embeddings": bool(self.EMBEDDING_MODEL), "smtp": bool(self.SMTP_HOST), "redis": bool(self.REDIS_URL),
                "email_verification": self.REQUIRE_EMAIL_VERIFICATION, "registration_open": self.REGISTRATION_OPEN,
                "database": self.DATABASE_URL.split(":", 1)[0].split("+")[0],
                "google_workspace": self.GOOGLE_ENABLED, "microsoft_365": self.MS_ENABLED,
                "channels": {"whatsapp": self.WHATSAPP_ENABLED, "teams": self.TEAMS_ENABLED, "email": self.EMAIL_CHANNEL_ENABLED}}


def _is_http_url(u):
    p = urllib.parse.urlparse(u)
    return p.scheme in ("http", "https") and bool(p.netloc)


def _dev_secret():
    """Development only: a random key saved next to the backend so sign-ins survive restarts."""
    path = BACKEND_DIR / ".secret_key"
    try:
        if path.exists():
            return path.read_text().strip()
        key = secrets.token_hex(32)
        path.write_text(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return key
    except OSError:
        log.warning("Could not save .secret_key; reset links will stop working on restart. Set SECRET_KEY.")
        return secrets.token_hex(32)


settings = Settings()
