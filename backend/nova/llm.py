"""AI provider access: one client with timeouts and exponential-backoff retries (built into the OpenAI SDK),
token/cost accounting, per-user quotas, a global monthly budget and per-user concurrency limits."""
import logging
import time
import uuid
from datetime import datetime, timezone

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)
from sqlalchemy import func, insert, select

from . import db
from .config import settings
from .errors import ApiError

log = logging.getLogger("nova.llm")
_client = None
_embed_client = None
_stream_usage_ok = True


def reset_clients():
    global _client, _embed_client, _stream_usage_ok
    _client, _embed_client, _stream_usage_ok = None, None, True


def client():
    global _client
    if _client is None:
        if not settings.LLM_CONFIGURED:
            raise ApiError(503, "No AI provider is configured. Set LLM_API_KEY in backend/.env and restart the server.",
                           "llm_not_configured")
        _client = OpenAI(api_key=settings.LLM_API_KEY or "not-needed", base_url=settings.LLM_BASE_URL,
                         timeout=settings.LLM_TIMEOUT, max_retries=settings.LLM_MAX_RETRIES)
    return _client


def embed_client():
    global _embed_client
    if _embed_client is None:
        _embed_client = OpenAI(api_key=settings.EMBEDDING_API_KEY or "not-needed", base_url=settings.EMBEDDING_BASE_URL,
                               timeout=settings.LLM_TIMEOUT, max_retries=settings.LLM_MAX_RETRIES)
    return _embed_client


def friendly_error(e):
    if isinstance(e, ApiError):
        return e.message
    if isinstance(e, AuthenticationError):
        return "The AI provider rejected the API key. An administrator needs to check LLM_API_KEY."
    if isinstance(e, RateLimitError):
        return "The AI provider is rate limiting requests or the quota is used up. Try again shortly."
    if isinstance(e, APITimeoutError):
        return "The AI provider took too long to respond. Try again."
    if isinstance(e, APIConnectionError):
        return "Could not reach the AI provider. It may be down, or LLM_BASE_URL is wrong. Try again shortly."
    if isinstance(e, APIStatusError):
        try:
            detail = (e.response.text or "").strip().replace("\n", " ")[:220]
        except Exception:  # noqa: BLE001
            detail = ""
        hint = f" Model: {settings.LLM_MODEL}." if e.status_code == 404 else ""
        return f"The AI provider returned an error ({e.status_code}).{hint} Provider says: {detail}"
    log.exception("LLM error")
    return "The AI provider failed to respond. Try again."


# ------------------------------------------------------------------ accounting
def estimate_tokens(text_or_msgs):
    if isinstance(text_or_msgs, list):
        total = 0
        for m in text_or_msgs:
            c = m.get("content") if isinstance(m, dict) else ""
            if isinstance(c, list):
                total += sum(len(p.get("text", "")) if p.get("type") == "text" else 3000 for p in c)
            else:
                total += len(str(c or ""))
        return total // 4 + 4 * len(text_or_msgs)
    return len(text_or_msgs or "") // 4


def cost(prompt_tokens, completion_tokens):
    return (prompt_tokens * settings.PRICE_IN + completion_tokens * settings.PRICE_OUT) / 1_000_000


def record_usage(ctx, kind, prompt_tokens, completion_tokens, latency_ms, status="ok", estimated=False, model=None):
    try:
        with db.engine.begin() as c:
            c.execute(insert(db.usage).values(
                id=str(uuid.uuid4()), created_at=db.now_us(), user_id=(ctx or {}).get("uid"),
                conversation_id=(ctx or {}).get("cid"), kind=kind, model=(model or settings.LLM_MODEL)[:100],
                prompt_tokens=int(prompt_tokens or 0), completion_tokens=int(completion_tokens or 0),
                estimated=1 if estimated else 0, cost_usd=cost(prompt_tokens or 0, completion_tokens or 0),
                latency_ms=int(latency_ms), status=status))
    except Exception:  # noqa: BLE001
        log.exception("could not record usage")


def _day_start_us():
    now = datetime.now(timezone.utc)
    return int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1e6)


def _month_start_us():
    now = datetime.now(timezone.utc)
    return int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp() * 1e6)


def tokens_today(uid):
    with db.engine.connect() as c:
        return c.execute(select(func.coalesce(func.sum(db.usage.c.prompt_tokens + db.usage.c.completion_tokens), 0))
                         .where(db.usage.c.user_id == uid, db.usage.c.created_at >= _day_start_us())).scalar() or 0


def spend_this_month():
    with db.engine.connect() as c:
        return float(c.execute(select(func.coalesce(func.sum(db.usage.c.cost_usd), 0.0))
                               .where(db.usage.c.created_at >= _month_start_us())).scalar() or 0.0)


def check_quota(uid):
    """Refuse new AI work when the user's daily token allowance or the global monthly budget is used up."""
    if settings.DAILY_TOKEN_LIMIT and tokens_today(uid) >= settings.DAILY_TOKEN_LIMIT:
        raise ApiError(429, "You've reached today's usage limit. It resets at midnight UTC.", "quota_exceeded")
    if settings.MONTHLY_BUDGET_USD and spend_this_month() >= settings.MONTHLY_BUDGET_USD:
        raise ApiError(503, "The assistant has reached its monthly spending limit. An administrator can raise it.",
                       "budget_exceeded")


# ------------------------------------------------------------------ calls
def _usage_of(resp_usage):
    if not resp_usage:
        return None
    return getattr(resp_usage, "prompt_tokens", 0) or 0, getattr(resp_usage, "completion_tokens", 0) or 0


def complete(messages, ctx=None, kind="chat", **kwargs):
    """Non-streaming completion. Returns the message object and records usage (success or failure)."""
    t0 = time.perf_counter()
    try:
        resp = client().chat.completions.create(model=settings.LLM_MODEL, messages=messages, **kwargs)
    except Exception:
        record_usage(ctx, kind, estimate_tokens(messages), 0, (time.perf_counter() - t0) * 1000, "error", True)
        raise
    u = _usage_of(getattr(resp, "usage", None))
    msg = resp.choices[0].message
    if u:
        record_usage(ctx, kind, u[0], u[1], (time.perf_counter() - t0) * 1000)
    else:
        record_usage(ctx, kind, estimate_tokens(messages), estimate_tokens(msg.content or ""),
                     (time.perf_counter() - t0) * 1000, estimated=True)
    return msg


def stream(messages, ctx=None, kind="chat"):
    """Yield text pieces. Usage is recorded when the stream ends, is cancelled or fails."""
    global _stream_usage_ok
    t0 = time.perf_counter()
    out, usage_seen, status = [], None, "ok"
    kwargs = {"stream_options": {"include_usage": True}} if settings.LLM_STREAM_USAGE and _stream_usage_ok else {}
    try:
        try:
            resp = client().chat.completions.create(model=settings.LLM_MODEL, stream=True, messages=messages, **kwargs)
        except BadRequestError as e:
            if kwargs and "stream_options" in str(e):
                _stream_usage_ok = False  # provider doesn't support usage in streams; estimate instead
                resp = client().chat.completions.create(model=settings.LLM_MODEL, stream=True, messages=messages)
            else:
                raise
        for chunk in resp:
            if getattr(chunk, "usage", None):
                usage_seen = _usage_of(chunk.usage)
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                piece = chunk.choices[0].delta.content
                out.append(piece)
                yield piece
    except GeneratorExit:
        status = "cancelled"
        raise
    except Exception:
        status = "error"
        raise
    finally:
        latency = (time.perf_counter() - t0) * 1000
        if usage_seen:
            record_usage(ctx, kind, usage_seen[0], usage_seen[1], latency, "ok" if status != "error" else "error")
        else:
            record_usage(ctx, kind, estimate_tokens(messages), estimate_tokens("".join(out)), latency,
                         "error" if status == "error" else "ok", estimated=True)


def embed(texts, ctx=None):
    """Return a list of float vectors, or raise. Batches of 64."""
    vectors = []
    t0 = time.perf_counter()
    total = 0
    for i in range(0, len(texts), 64):
        batch = [t[:8000] for t in texts[i:i + 64]]
        resp = embed_client().embeddings.create(model=settings.EMBEDDING_MODEL, input=batch)
        vectors.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        u = getattr(resp, "usage", None)
        total += (getattr(u, "prompt_tokens", 0) or 0) if u else sum(len(t) for t in batch) // 4
    record_usage(ctx, "embedding", total, 0, (time.perf_counter() - t0) * 1000, model=settings.EMBEDDING_MODEL)
    return vectors
