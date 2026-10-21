"""Database schema, connection management and versioned migrations.

New installs get the full schema from create_all(). Existing databases are upgraded in place:
missing columns and indexes are added (additive, non-destructive), then numbered data migrations
run once each and are recorded in schema_migrations.
"""
import logging
import threading
import time

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.pool import StaticPool

log = logging.getLogger("nova.db")

# MySQL's TEXT/BLOB hold only 64 KB; agent state, reports and images need the LONG variants.
LongText = Text().with_variant(mysql.LONGTEXT(), "mysql")
LongBlob = LargeBinary().with_variant(mysql.LONGBLOB(), "mysql")

md = MetaData()
engine = None

users = Table(
    "users", md,
    Column("id", String(36), primary_key=True),
    Column("email", String(255), unique=True, nullable=False),
    Column("name", String(100), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(16), nullable=False, default="user", server_default="user"),
    Column("disabled", Integer, nullable=False, default=0, server_default="0"),
    Column("memory_enabled", Integer, nullable=False, default=0, server_default="0"),
    Column("email_verified", Integer, nullable=False, default=0, server_default="0"),
    Column("mfa_enabled", Integer, nullable=False, default=0, server_default="0"),
    Column("mfa_secret", Text, nullable=True),          # encrypted at rest
    Column("mfa_recovery", Text, nullable=True),        # JSON list of hashed one-time codes
    Column("mfa_last_step", BigInteger, nullable=True),  # blocks TOTP code replay
    Column("failed_logins", Integer, nullable=False, default=0, server_default="0"),
    Column("locked_until", BigInteger, nullable=True),
    Column("password_changed_at", BigInteger, nullable=True),
    Column("settings", Text, nullable=True),            # JSON: synced UI preferences
    Column("created_at", BigInteger, nullable=False),
    Column("last_login", BigInteger, nullable=True),
)
sessions = Table(
    "sessions", md,
    Column("id", String(64), primary_key=True),          # SHA-256 of the cookie value; the raw token is never stored
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, index=True),
    Column("csrf", String(64), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("last_seen", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False, index=True),
    Column("ip", String(64), nullable=True),
    Column("user_agent", String(200), nullable=True),
)
conversations = Table(
    "conversations", md,
    Column("id", String(36), primary_key=True),
    Column("title", String(200), nullable=False),
    Column("user_id", String(36), index=True, nullable=True),
    Column("pinned", Integer, nullable=False, default=0, server_default="0"),
    Column("archived", Integer, nullable=False, default=0, server_default="0"),
    Column("summary", LongText, nullable=True),          # rolling summary of messages older than the context window
    Column("summary_upto", BigInteger, nullable=True),   # created_at of the last message included in the summary
    Column("subject_id", String(36), nullable=True),     # study chat: answers only from this subject's material
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)
messages = Table(
    "messages", md,
    Column("id", String(36), primary_key=True),
    Column("conversation_id", String(36), ForeignKey("conversations.id"), index=True, nullable=False),
    Column("role", String(16), nullable=False),
    Column("content", LongText, nullable=False),
    Column("sources", LongText, nullable=True),
    Column("steps", LongText, nullable=True),
    Column("edited_at", BigInteger, nullable=True),
    Column("created_at", BigInteger, nullable=False),
)
feedback = Table(
    "feedback", md,
    Column("message_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("rating", Integer, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
errors = Table(
    "errors", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=True),
    Column("message", Text, nullable=False),
    Column("created_at", BigInteger, nullable=False, index=True),
)
documents = Table(
    "documents", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), index=True, nullable=False),
    Column("filename", String(255), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("pages", Integer, nullable=False),
    Column("chunk_count", Integer, nullable=False),
    Column("size", Integer, nullable=False),
    Column("sha256", String(64), nullable=True),
    Column("version", Integer, nullable=False, default=1, server_default="1"),
    Column("ocr", Integer, nullable=False, default=0, server_default="0"),
    Column("embedded", Integer, nullable=False, default=0, server_default="0"),
    Column("created_at", BigInteger, nullable=False),
)
doc_chunks = Table(
    "doc_chunks", md,
    Column("id", String(36), primary_key=True),
    Column("document_id", String(36), index=True, nullable=False),
    Column("user_id", String(36), index=True, nullable=False),
    Column("page", Integer, nullable=False),
    Column("idx", Integer, nullable=False),
    Column("body", LongText, nullable=False),
    Column("embedding", LongBlob, nullable=True),        # float32 vector, when EMBEDDING_MODEL is set
)
reports = Table(
    "reports", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), index=True, nullable=False),
    Column("title", String(200), nullable=False),
    Column("content", LongText, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
handoffs = Table(
    "handoffs", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), index=True, nullable=False),
    Column("conversation_id", String(36), nullable=True),
    Column("summary", Text, nullable=False),
    Column("context", LongText, nullable=True),
    Column("status", String(12), nullable=False, default="open"),
    Column("reply", Text, nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("resolved_at", BigInteger, nullable=True),
)
tool_log = Table(
    "tool_log", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=True),
    Column("conversation_id", String(36), nullable=True),
    Column("tool", String(64), nullable=False),
    Column("args", Text, nullable=True),
    Column("status", String(12), nullable=False),
    Column("summary", Text, nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Column("created_at", BigInteger, nullable=False, index=True),
)
pending_actions = Table(
    "pending_actions", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("conversation_id", String(36), nullable=False, index=True),
    Column("message_id", String(36), nullable=True),
    Column("state", LongText, nullable=False),
    Column("status", String(12), nullable=False, default="open"),
    Column("expires_at", BigInteger, nullable=True),
    Column("created_at", BigInteger, nullable=False),
)
attachments = Table(
    "attachments", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), index=True, nullable=False),
    Column("message_id", String(36), index=True, nullable=True),
    Column("mime", String(32), nullable=False),
    Column("data", LongBlob, nullable=False),
    Column("size", Integer, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
memories = Table(
    "memories", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), index=True, nullable=False),
    Column("text", String(300), nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
eval_cases = Table(
    "eval_cases", md,
    Column("id", String(36), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("question", Text, nullable=False),
    Column("keywords", Text, nullable=False),
    Column("expected_tool", String(64), nullable=True),
    Column("use_tools", Integer, nullable=False, default=0),
    Column("created_at", BigInteger, nullable=False),
)
eval_runs = Table(
    "eval_runs", md,
    Column("id", String(36), primary_key=True),
    Column("created_at", BigInteger, nullable=False),
    Column("model", String(100), nullable=False),
    Column("status", String(12), nullable=False),
    Column("judge", Integer, nullable=False, default=0),
    Column("summary", LongText, nullable=False),
    Column("results", LongText, nullable=False),
)
audit_log = Table(
    "audit_log", md,
    Column("id", String(36), primary_key=True),
    Column("created_at", BigInteger, nullable=False, index=True),
    Column("actor_id", String(36), nullable=True, index=True),
    Column("action", String(64), nullable=False, index=True),
    Column("target_type", String(32), nullable=True),
    Column("target_id", String(64), nullable=True),
    Column("ip", String(64), nullable=True),
    Column("detail", Text, nullable=True),
)
usage = Table(
    "usage", md,
    Column("id", String(36), primary_key=True),
    Column("created_at", BigInteger, nullable=False, index=True),
    Column("user_id", String(36), nullable=True, index=True),
    Column("conversation_id", String(36), nullable=True),
    Column("kind", String(16), nullable=False),          # chat | agent | summary | embedding | eval | judge
    Column("model", String(100), nullable=False),
    Column("prompt_tokens", Integer, nullable=False, default=0),
    Column("completion_tokens", Integer, nullable=False, default=0),
    Column("estimated", Integer, nullable=False, default=0),  # 1 when the provider did not report usage
    Column("cost_usd", Float, nullable=False, default=0.0),
    Column("latency_ms", Integer, nullable=False, default=0),
    Column("status", String(12), nullable=False),        # ok | error
)
integrations = Table(           # an internal app (any REST API) registered by an administrator
    "integrations", md,
    Column("id", String(36), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("slug", String(30), nullable=False, unique=True),  # tool name prefix, e.g. hr
    Column("description", Text, nullable=True),
    Column("base_url", String(500), nullable=False),
    Column("auth_type", String(12), nullable=False, default="none"),  # none | bearer | header | basic
    Column("auth_header", String(100), nullable=True),     # header name for auth_type=header, e.g. X-API-Key
    Column("secret", Text, nullable=True),                 # encrypted at rest; never returned by the API
    Column("audience", String(12), nullable=False, default="all"),  # all | admin
    Column("send_user", Integer, nullable=False, default=1),  # add X-Nova-User-Email / X-Nova-User-Id headers
    Column("enabled", Integer, nullable=False, default=1),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)
integration_actions = Table(    # one endpoint the agent may call, with its parameters
    "integration_actions", md,
    Column("id", String(36), primary_key=True),
    Column("integration_id", String(36), ForeignKey("integrations.id"), nullable=False, index=True),
    Column("name", String(30), nullable=False),            # e.g. get_employee
    Column("label", String(100), nullable=False),          # shown to people, e.g. "Look up an employee"
    Column("description", Text, nullable=False),           # tells the model when to use it
    Column("method", String(8), nullable=False),           # GET | POST | PUT | PATCH | DELETE
    Column("path", String(500), nullable=False),           # e.g. /employees/{employee_id}
    Column("params", Text, nullable=False),                # JSON list of {name, type, description, required, in}
    Column("approval", Integer, nullable=False, default=1),  # 1 = the user must approve each call
    Column("enabled", Integer, nullable=False, default=1),
    Column("created_at", BigInteger, nullable=False),
)
user_connections = Table(       # a person's own account in an external service (Google Workspace)
    "user_connections", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, index=True),
    Column("provider", String(16), nullable=False),
    Column("account", String(255), nullable=True),
    Column("scopes", Text, nullable=True),
    Column("access_token", Text, nullable=True),           # encrypted
    Column("refresh_token", Text, nullable=True),          # encrypted
    Column("expires_at", BigInteger, nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)
subjects = Table(               # Study mode: a subject with its syllabus and material
    "subjects", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, index=True),
    Column("name", String(120), nullable=False),
    Column("description", Text, nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)
subject_documents = Table(
    "subject_documents", md,
    Column("subject_id", String(36), primary_key=True),
    Column("document_id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("role", String(12), nullable=False),            # syllabus | material
    Column("added_at", BigInteger, nullable=False),
)
study_plans = Table(            # one current plan per subject; days are JSON with a done flag each
    "study_plans", md,
    Column("id", String(36), primary_key=True),
    Column("subject_id", String(36), nullable=False, index=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("days", Integer, nullable=False),
    Column("minutes_per_day", Integer, nullable=False),
    Column("plan", LongText, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
study_notes = Table(
    "study_notes", md,
    Column("id", String(36), primary_key=True),
    Column("subject_id", String(36), nullable=False, index=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("title", String(200), nullable=False),
    Column("day", Integer, nullable=True),
    Column("content", LongText, nullable=False),
    Column("sources", LongText, nullable=True),
    Column("created_at", BigInteger, nullable=False),
)
doubts = Table(                 # the open doubts index: questions the material couldn't answer, or added by the student
    "doubts", md,
    Column("id", String(36), primary_key=True),
    Column("subject_id", String(36), nullable=False, index=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("question", Text, nullable=False),
    Column("status", String(12), nullable=False, default="open"),  # open | resolved
    Column("note", Text, nullable=True),
    Column("origin", String(12), nullable=False, default="manual"),  # chat | manual
    Column("conversation_id", String(36), nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("resolved_at", BigInteger, nullable=True),
)
channel_links = Table(          # a WhatsApp number / Teams account / email address linked to one Nova user
    "channel_links", md,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False, index=True),
    Column("channel", String(16), nullable=False),        # whatsapp | teams | email
    Column("external_id", String(255), nullable=False),   # wa_id, Entra object id, or lower-case email address
    Column("display", String(255), nullable=True),        # masked number, Teams name or address, shown in Account
    Column("conversation_id", String(36), nullable=True),  # the rolling chat for WhatsApp and Teams
    Column("meta", Text, nullable=True),                   # JSON: e.g. Teams serviceUrl/conversation for proactive replies
    Column("created_at", BigInteger, nullable=False),
    Column("last_used_at", BigInteger, nullable=True),
)
channel_link_codes = Table(     # one-time codes shown in Account and sent from the app to prove ownership
    "channel_link_codes", md,
    Column("id", String(64), primary_key=True),           # SHA-256 of channel + code; the code itself is never stored
    Column("user_id", String(36), nullable=False, index=True),
    Column("channel", String(16), nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
channel_events = Table(         # inbound message ids already handled (providers retry webhooks)
    "channel_events", md,
    Column("id", String(255), primary_key=True),
    Column("created_at", BigInteger, nullable=False, index=True),
)
channel_threads = Table(        # email Message-IDs -> conversation, so replies continue the same chat
    "channel_threads", md,
    Column("id", String(255), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("conversation_id", String(36), nullable=False),
    Column("created_at", BigInteger, nullable=False, index=True),
)
channel_state = Table(          # small key/value store, e.g. the email poller lease shared by all workers
    "channel_state", md,
    Column("key", String(64), primary_key=True),
    Column("value", String(255), nullable=True),
    Column("updated_at", BigInteger, nullable=False),
)
schema_migrations = Table(
    "schema_migrations", md,
    Column("version", Integer, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("applied_at", BigInteger, nullable=False),
)
Index("ix_messages_conv_created", messages.c.conversation_id, messages.c.created_at)
Index("ix_conversations_user_updated", conversations.c.user_id, conversations.c.updated_at)
Index("ix_usage_user_created", usage.c.user_id, usage.c.created_at)
Index("ux_integration_action_name", integration_actions.c.integration_id, integration_actions.c.name, unique=True)
Index("ux_user_connection", user_connections.c.user_id, user_connections.c.provider, unique=True)
Index("ux_channel_links_external", channel_links.c.channel, channel_links.c.external_id, unique=True)

_clock = threading.Lock()
_last = 0


def now_us() -> int:
    """Strictly increasing microsecond timestamp so message order is stable."""
    global _last
    with _clock:
        _last = max(_last + 1, time.time_ns() // 1000)
        return _last


def init_engine(url):
    """Create the engine. SQLite gets WAL + foreign keys + busy timeout; servers get pool pre-ping and recycling."""
    global engine
    if engine is not None:
        engine.dispose()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_recycle=1800, pool_size=10, max_overflow=20)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            try:
                if ":memory:" not in url:
                    cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.execute("PRAGMA synchronous=NORMAL")
            finally:
                cur.close()
    return engine


# ------------------------------------------------------------------ migrations
def _add_missing_columns(conn):
    """Additive schema sync for databases created by older versions."""
    insp = inspect(conn)
    existing_tables = set(insp.get_table_names())
    added = []
    for table in md.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = col.type.compile(dialect=conn.dialect)
            default = ""
            if col.server_default is not None:
                default = f" DEFAULT '{col.server_default.arg}'" if not str(col.server_default.arg).isdigit() \
                    else f" DEFAULT {col.server_default.arg}"
            null = "" if col.nullable else " NOT NULL"
            if not col.nullable and col.server_default is None:
                null = ""  # cannot add NOT NULL without a default; the data migration backfills it
            conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}{default}{null}"))
            added.append(f"{table.name}.{col.name}")
    return added


def _add_missing_indexes(conn):
    insp = inspect(conn)
    created = []
    for table in md.sorted_tables:
        have = {i["name"] for i in insp.get_indexes(table.name)}
        for idx in table.indexes:
            if idx.name and idx.name not in have:
                idx.create(conn)
                created.append(idx.name)
    return created


def _m1_backfill(conn):
    """Existing users predate verification: treat them as verified. Existing documents are version 1."""
    conn.execute(update(users).where(users.c.created_at < now_us()).values(email_verified=1))
    conn.execute(update(documents).where(documents.c.version.is_(None)).values(version=1))


def _m2_expire_old_approvals(conn):
    conn.execute(update(pending_actions).where(pending_actions.c.expires_at.is_(None))
                 .values(expires_at=now_us()))  # approvals created before expiry existed are closed


MIGRATIONS = [
    (1, "backfill verification and document versions", _m1_backfill),
    (2, "expire approvals without a deadline", _m2_expire_old_approvals),
]


def migrate():
    """Bring the database schema up to date. Safe to run on every start and from several workers."""
    insp = inspect(engine)
    fresh = "users" not in insp.get_table_names()
    md.create_all(engine)
    with engine.begin() as conn:
        added = _add_missing_columns(conn)
        indexes = _add_missing_indexes(conn)
    if added:
        log.info("migrations: added columns %s", ", ".join(added))
    if indexes:
        log.info("migrations: created indexes %s", ", ".join(indexes))
    applied = []
    for version, name, fn in MIGRATIONS:
        with engine.begin() as conn:
            if conn.execute(select(schema_migrations.c.version).where(schema_migrations.c.version == version)).first():
                continue
            if not fresh:
                fn(conn)
            try:
                conn.execute(insert(schema_migrations).values(version=version, name=name, applied_at=now_us()))
            except Exception:  # noqa: BLE001, S112  another worker recorded it first
                continue
            applied.append(version)
    if applied:
        log.info("migrations: applied %s", applied)
    return {"fresh": fresh, "columns": added, "indexes": indexes, "applied": applied}


def schema_version():
    with engine.connect() as c:
        return c.execute(select(schema_migrations.c.version).order_by(schema_migrations.c.version.desc())).scalar() or 0


LATEST_VERSION = MIGRATIONS[-1][0]
