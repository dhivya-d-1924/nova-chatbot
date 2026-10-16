"""Administrator API: overview, usage/cost metrics, users, support requests, audit log, errors and evaluations.
Every route requires the admin role; state changes are audit-logged; lists are paginated and filterable."""
import json
import re
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request
from sqlalchemy import delete, func, insert, or_, select, update

from . import agent as ag
from . import db, llm, security
from .chat import save_message
from .config import settings
from .errors import ApiError
from .security import audit, auth
from .users import admin_count, user_dict
from .validation import Bool, Str, StrList, page_args, parse, query_str

bp = Blueprint("admin", __name__, url_prefix="/api/admin")
DAY_US = 86400 * 10**6


def day_key(us):
    return datetime.fromtimestamp(us / 1e6, timezone.utc).strftime("%Y-%m-%d")


def _emails(c, ids):
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: u.email for u in c.execute(select(db.users.c.id, db.users.c.email).where(db.users.c.id.in_(ids)))}


def paged(rows, total, page, limit):
    return {"items": rows, "total": total, "page": page, "pages": max(1, -(-total // limit)), "limit": limit}


def _like(q):
    return "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@bp.get("/stats")
@auth(admin=True)
def admin_stats():
    today = datetime.now(timezone.utc)
    days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    since = db.now_us() - 14 * DAY_US
    with db.engine.connect() as c:
        count = lambda t: c.execute(select(func.count()).select_from(t)).scalar()  # noqa: E731
        per_day = Counter(day_key(r.created_at) for r in c.execute(
            select(db.messages.c.created_at).where(db.messages.c.role == "user", db.messages.c.created_at >= since)))
        up = c.execute(select(func.count()).select_from(db.feedback).where(db.feedback.c.rating == 1)).scalar()
        down = c.execute(select(func.count()).select_from(db.feedback).where(db.feedback.c.rating == -1)).scalar()
        errs = c.execute(select(db.errors).order_by(db.errors.c.created_at.desc()).limit(10)).all()
        downs = c.execute(
            select(db.feedback.c.user_id, db.feedback.c.created_at, db.messages.c.content)
            .select_from(db.feedback.join(db.messages, db.feedback.c.message_id == db.messages.c.id))
            .where(db.feedback.c.rating == -1).order_by(db.feedback.c.created_at.desc()).limit(10)).all()
        tl = c.execute(select(db.tool_log).order_by(db.tool_log.c.created_at.desc()).limit(15)).all()
        open_handoffs = c.execute(select(func.count()).select_from(db.handoffs).where(db.handoffs.c.status == "open")).scalar()
        totals = {"users": count(db.users), "conversations": count(db.conversations), "messages": count(db.messages),
                  "errors": count(db.errors), "open_handoffs": open_handoffs}
        emails = _emails(c, [e.user_id for e in errs] + [d.user_id for d in downs] + [t.user_id for t in tl])
    return jsonify(
        totals={**totals, "questions_today": per_day.get(days[-1], 0)},
        per_day=[{"date": d, "count": per_day.get(d, 0)} for d in days],
        feedback={"up": up, "down": down},
        config=settings.public(),
        recent_errors=[{"message": e.message, "user": emails.get(e.user_id, "unknown"), "created_at": e.created_at} for e in errs],
        recent_tools=[{"tool": t.tool, "status": t.status, "args": t.args, "user": emails.get(t.user_id, "unknown"),
                       "duration_ms": t.duration_ms, "created_at": t.created_at} for t in tl],
        recent_downvotes=[{"user": emails.get(d.user_id, "unknown"), "snippet": d.content[:160], "created_at": d.created_at}
                          for d in downs],
    )


@bp.get("/usage")
@auth(admin=True)
def admin_usage():
    """AI usage for the last N days: tokens, estimated cost, latency, failures, by day/model/kind, plus tool metrics."""
    try:
        days_n = max(1, min(90, int(request.args.get("days", 14))))
    except ValueError:
        raise ApiError(400, "days must be a number.", "bad_request")
    since = db.now_us() - days_n * DAY_US
    with db.engine.connect() as c:
        rows = c.execute(select(db.usage).where(db.usage.c.created_at >= since)).all()
        tools_rows = c.execute(select(db.tool_log.c.tool, db.tool_log.c.status, db.tool_log.c.duration_ms)
                               .where(db.tool_log.c.created_at >= since)).all()
        top = c.execute(select(db.usage.c.user_id, func.sum(db.usage.c.prompt_tokens + db.usage.c.completion_tokens).label("tokens_sum"),
                               func.sum(db.usage.c.cost_usd).label("cost_sum"))
                        .where(db.usage.c.created_at >= since, db.usage.c.user_id.is_not(None))
                        .group_by(db.usage.c.user_id).order_by(func.sum(db.usage.c.prompt_tokens + db.usage.c.completion_tokens).desc())
                        .limit(10)).all()
        emails = _emails(c, [t.user_id for t in top])
    today = datetime.now(timezone.utc)
    day_list = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_n - 1, -1, -1)]
    by_day = {d: {"date": d, "requests": 0, "failed": 0, "tokens": 0, "cost_usd": 0.0} for d in day_list}
    by_model, by_kind = defaultdict(lambda: {"requests": 0, "tokens": 0, "cost_usd": 0.0}), Counter()
    lat = []
    for r in rows:
        d = by_day.get(day_key(r.created_at))
        tok = r.prompt_tokens + r.completion_tokens
        if d:
            d["requests"] += 1
            d["failed"] += r.status == "error"
            d["tokens"] += tok
            d["cost_usd"] += r.cost_usd
        m = by_model[r.model]
        m["requests"] += 1
        m["tokens"] += tok
        m["cost_usd"] += r.cost_usd
        by_kind[r.kind] += 1
        if r.status == "ok" and r.kind in ("chat", "agent"):
            lat.append(r.latency_ms)
    lat.sort()
    pct = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))] if lat else 0  # noqa: E731
    tools_stats = defaultdict(lambda: {"calls": 0, "errors": 0, "declined": 0, "durations": []})
    for t in tools_rows:
        s = tools_stats[t.tool]
        s["calls"] += 1
        s["errors"] += t.status == "error"
        s["declined"] += t.status == "declined"
        if t.duration_ms is not None:
            s["durations"].append(t.duration_ms)
    total_req = len(rows)
    return jsonify(
        days=days_n, priced=bool(settings.PRICE_IN or settings.PRICE_OUT),
        totals={"requests": total_req, "failed": sum(1 for r in rows if r.status == "error"),
                "prompt_tokens": sum(r.prompt_tokens for r in rows), "completion_tokens": sum(r.completion_tokens for r in rows),
                "cost_usd": round(sum(r.cost_usd for r in rows), 4), "estimated_share": round(
                    sum(1 for r in rows if r.estimated) / total_req, 2) if total_req else 0,
                "latency_p50_ms": pct(0.5), "latency_p95_ms": pct(0.95),
                "month_cost_usd": round(llm.spend_this_month(), 4), "monthly_budget_usd": settings.MONTHLY_BUDGET_USD or None},
        per_day=[{**v, "cost_usd": round(v["cost_usd"], 4)} for v in by_day.values()],
        by_model=[{"model": k, **{**v, "cost_usd": round(v["cost_usd"], 4)}} for k, v in by_model.items()],
        by_kind=dict(by_kind),
        top_users=[{"user": emails.get(t.user_id, "deleted user"), "tokens": int(t.tokens_sum or 0), "cost_usd": round(t.cost_sum or 0, 4)}
                   for t in top],
        tools=[{"tool": k, "calls": v["calls"], "errors": v["errors"], "declined": v["declined"],
                "success_rate": round((v["calls"] - v["errors"]) / v["calls"], 2) if v["calls"] else None,
                "avg_ms": int(sum(v["durations"]) / len(v["durations"])) if v["durations"] else None}
               for k, v in sorted(tools_stats.items(), key=lambda kv: -kv[1]["calls"])],
    )


# ------------------------------------------------------------------ users
@bp.get("/users")
@auth(admin=True)
def admin_users():
    page, limit, offset = page_args(25, 100)
    q, role, status = query_str("q"), request.args.get("role", ""), request.args.get("status", "")
    stmt = select(db.users)
    if q:
        stmt = stmt.where(or_(func.lower(db.users.c.email).like(_like(q), escape="\\"),
                              func.lower(db.users.c.name).like(_like(q), escape="\\")))
    if role in ("user", "admin"):
        stmt = stmt.where(db.users.c.role == role)
    if status == "disabled":
        stmt = stmt.where(db.users.c.disabled == 1)
    elif status == "active":
        stmt = stmt.where(db.users.c.disabled == 0)
    elif status == "locked":
        stmt = stmt.where(db.users.c.locked_until > db.now_us())
    with db.engine.connect() as c:
        total = c.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = c.execute(stmt.order_by(db.users.c.created_at).limit(limit).offset(offset)).all()
        ids = [u.id for u in rows]
        convs = dict(c.execute(select(db.conversations.c.user_id, func.count()).where(db.conversations.c.user_id.in_(ids))
                               .group_by(db.conversations.c.user_id)).all()) if ids else {}
        msgs = dict(c.execute(
            select(db.conversations.c.user_id, func.count())
            .select_from(db.messages.join(db.conversations, db.messages.c.conversation_id == db.conversations.c.id))
            .where(db.messages.c.role == "user", db.conversations.c.user_id.in_(ids))
            .group_by(db.conversations.c.user_id)).all()) if ids else {}
        sess = dict(c.execute(select(db.sessions.c.user_id, func.count()).where(db.sessions.c.user_id.in_(ids))
                              .group_by(db.sessions.c.user_id)).all()) if ids else {}
    now = db.now_us()
    items = [{**{k: v for k, v in user_dict(u).items() if k != "settings"}, "disabled": bool(u.disabled),
              "last_login": u.last_login, "conversations": convs.get(u.id, 0), "messages": msgs.get(u.id, 0),
              "sessions": sess.get(u.id, 0), "locked": bool(u.locked_until and u.locked_until > now)} for u in rows]
    return jsonify(paged(items, total, page, limit))


@bp.patch("/users/<uid>")
@auth(admin=True)
def admin_update_user(uid):
    d = parse(request, {"role": Str(choices=("user", "admin")), "disabled": Bool(), "unlock": Bool()})
    if not d:
        raise ApiError(400, "Nothing to update.", "bad_request")
    if uid == g.user.id and (d.get("role") == "user" or d.get("disabled")):
        raise ApiError(400, "You can't remove your own admin access or disable your own account.", "self")
    values = {}
    if "role" in d:
        values["role"] = d["role"]
    if "disabled" in d:
        values["disabled"] = 1 if d["disabled"] else 0
    if d.get("unlock"):
        values.update(failed_logins=0, locked_until=None)
    with db.engine.begin() as c:
        target = c.execute(select(db.users).where(db.users.c.id == uid)).first()
        if not target:
            raise ApiError(404, "User not found.", "not_found")
        losing_admin = target.role == "admin" and (values.get("role") == "user" or values.get("disabled"))
        if losing_admin and admin_count(c) <= 1:
            raise ApiError(400, "There must be at least one active administrator.", "last_admin")
        c.execute(update(db.users).where(db.users.c.id == uid).values(**values))
        if values.get("disabled") or values.get("role") == "user":
            security.revoke_sessions(uid, conn=c)  # takes effect immediately on every device
    audit("admin.user_updated", target_type="user", target_id=uid,
          **{k: v for k, v in d.items() if k in ("role", "disabled", "unlock")})
    return jsonify(ok=True)


@bp.post("/users/<uid>/revoke-sessions")
@auth(admin=True)
def admin_revoke_sessions(uid):
    with db.engine.connect() as c:
        if not c.execute(select(db.users.c.id).where(db.users.c.id == uid)).first():
            raise ApiError(404, "User not found.", "not_found")
    n = security.revoke_sessions(uid, except_id=g.session.id if uid == g.user.id else None)
    audit("admin.sessions_revoked", target_type="user", target_id=uid, count=n)
    return jsonify(ok=True, revoked=n)


# ------------------------------------------------------------------ support requests
@bp.get("/handoffs")
@auth(admin=True)
def admin_handoffs():
    page, limit, offset = page_args(20, 100)
    status = request.args.get("status", "")
    stmt = select(db.handoffs)
    if status in ("open", "resolved"):
        stmt = stmt.where(db.handoffs.c.status == status)
    with db.engine.connect() as c:
        total = c.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = c.execute(stmt.order_by(db.handoffs.c.status.desc(), db.handoffs.c.created_at.desc())
                         .limit(limit).offset(offset)).all()
        emails = _emails(c, [h.user_id for h in rows])
    items = [{"id": h.id, "user": emails.get(h.user_id, "deleted user"), "summary": h.summary, "status": h.status,
              "reply": h.reply, "context": json.loads(h.context or "[]"), "created_at": h.created_at,
              "resolved_at": h.resolved_at, "has_conversation": bool(h.conversation_id)} for h in rows]
    return jsonify(paged(items, total, page, limit))


@bp.post("/handoffs/<hid>/resolve")
@auth(admin=True)
def admin_resolve_handoff(hid):
    d = parse(request, {"reply": Str(min=1, max=4000, required=True, label="Reply")})
    with db.engine.begin() as c:
        h = c.execute(select(db.handoffs).where(db.handoffs.c.id == hid)).first()
        if not h:
            raise ApiError(404, "Request not found.", "not_found")
        claimed = c.execute(update(db.handoffs).where(db.handoffs.c.id == hid, db.handoffs.c.status == "open")
                            .values(status="resolved", reply=d["reply"], resolved_at=db.now_us()))
        if claimed.rowcount != 1:
            raise ApiError(409, "This request was already resolved.", "already_resolved")
        conv_ok = h.conversation_id and c.execute(select(db.conversations.c.id).where(
            db.conversations.c.id == h.conversation_id, db.conversations.c.user_id == h.user_id)).first()
    if conv_ok:
        save_message(h.conversation_id, "assistant", f"**Reply from human support ({g.user.name}):**\n\n{d['reply']}")
    audit("admin.handoff_resolved", target_type="handoff", target_id=hid)
    return jsonify(ok=True, delivered_to_chat=bool(conv_ok))


# ------------------------------------------------------------------ audit & errors
@bp.get("/audit")
@auth(admin=True)
def admin_audit():
    page, limit, offset = page_args(50, 200)
    action, q = query_str("action", 64), query_str("q")
    stmt = select(db.audit_log)
    if action:
        stmt = stmt.where(db.audit_log.c.action.like(action.replace("%", "") + "%"))
    if q:
        with db.engine.connect() as c:
            ids = [u.id for u in c.execute(select(db.users.c.id).where(func.lower(db.users.c.email).like(_like(q), escape="\\")))]
        stmt = stmt.where(or_(db.audit_log.c.actor_id.in_(ids), db.audit_log.c.target_id.in_(ids),
                              db.audit_log.c.ip == q))
    with db.engine.connect() as c:
        total = c.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = c.execute(stmt.order_by(db.audit_log.c.created_at.desc()).limit(limit).offset(offset)).all()
        emails = _emails(c, [r.actor_id for r in rows] + [r.target_id for r in rows if r.target_type == "user"])
        actions = [r.action for r in c.execute(select(db.audit_log.c.action).distinct().order_by(db.audit_log.c.action))]
    items = [{"id": r.id, "created_at": r.created_at, "action": r.action,
              "actor": emails.get(r.actor_id, r.actor_id and "deleted user") or "anonymous",
              "target": (emails.get(r.target_id, r.target_id) if r.target_type == "user" else r.target_id),
              "target_type": r.target_type, "ip": r.ip, "detail": json.loads(r.detail) if r.detail else None} for r in rows]
    return jsonify({**paged(items, total, page, limit), "actions": actions})


@bp.get("/errors")
@auth(admin=True)
def admin_errors():
    page, limit, offset = page_args(25, 100)
    q = query_str("q")
    stmt = select(db.errors)
    if q:
        stmt = stmt.where(func.lower(db.errors.c.message).like(_like(q), escape="\\"))
    with db.engine.connect() as c:
        total = c.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = c.execute(stmt.order_by(db.errors.c.created_at.desc()).limit(limit).offset(offset)).all()
        emails = _emails(c, [e.user_id for e in rows])
    return jsonify(paged([{"message": e.message, "user": emails.get(e.user_id, "unknown"), "created_at": e.created_at}
                          for e in rows], total, page, limit))


# ------------------------------------------------------------------ evaluation
SEED_CASES = [
    {"name": "Exact arithmetic uses the calculator", "question": "What is 48213 multiplied by 9127?",
     "keywords": ["440040051"], "expected_tool": "calculate", "use_tools": 1},
    {"name": "Basic factual question", "question": "What is the capital of France? Answer in one sentence.",
     "keywords": ["Paris"], "expected_tool": None, "use_tools": 0},
    {"name": "Current time uses the time tool", "question": "What is the current time in Asia/Kolkata?",
     "keywords": [], "expected_tool": "current_time", "use_tools": 1},
]


def normalize_answer(t):
    return re.sub(r"(?<=\d),(?=\d{3})", "", t).lower()


def judge_score(question, answer, ctx):
    m = llm.complete([
        {"role": "system", "content": "You grade answers. Reply with only a single digit from 1 to 5."},
        {"role": "user", "content": f"Question: {question}\n\nAnswer: {answer[:3000]}\n\nHow well does the answer address "
                                    "the question? 1 = irrelevant or wrong, 5 = fully relevant, accurate and helpful."}],
        ctx, kind="judge", max_tokens=5)
    found = re.search(r"[1-5]", m.content or "")
    return int(found.group()) if found else None


def run_eval_case(case, judge, ctx, defs):
    t0, answer, steps, error = time.perf_counter(), "", [], None
    try:
        msgs = [{"role": "system", "content": settings.SYSTEM_PROMPT}, {"role": "user", "content": case.question}]
        if case.use_tools and defs:
            answer = ag.run_to_end(ag.agent_events(ctx, ag.agent_system(msgs, ctx, defs), steps, defs)).get("text", "")
        else:
            answer = llm.complete(msgs, ctx, kind="eval").content or ""
    except Exception as e:  # noqa: BLE001
        error = llm.friendly_error(e)
    latency = int((time.perf_counter() - t0) * 1000)
    kws = json.loads(case.keywords or "[]")
    norm = normalize_answer(answer)
    keywords_ok = all(k.lower() in norm for k in kws) if kws else None
    tool_ok = any(x["tool"] == case.expected_tool and x["status"] == "done" for x in steps) if case.expected_tool else None
    relevance = None
    if judge and answer.strip() and not error:
        try:
            relevance = judge_score(case.question, answer, ctx)
        except Exception:  # noqa: BLE001
            relevance = None
    passed = error is None and bool(answer.strip()) and keywords_ok is not False and tool_ok is not False
    return {"case_id": case.id, "name": case.name, "question": case.question, "passed": passed, "latency_ms": latency,
            "keywords_ok": keywords_ok, "tool_ok": tool_ok, "relevance": relevance, "error": error,
            "answer": answer.strip()[:600]}


def summarize_eval(results, total):
    done = len(results)
    lat = [r["latency_ms"] for r in results]
    rel = [r["relevance"] for r in results if r["relevance"] is not None]
    tool = [r["tool_ok"] for r in results if r["tool_ok"] is not None]
    return {"total": total, "done": done, "passed": sum(1 for r in results if r["passed"]),
            "avg_latency_ms": int(sum(lat) / done) if done else 0, "max_latency_ms": max(lat, default=0),
            "avg_relevance": round(sum(rel) / len(rel), 2) if rel else None,
            "tool_success": round(sum(tool) / len(tool), 2) if tool else None}


def eval_worker(run_id, uid, judge):
    ctx = {"uid": uid, "role": "admin", "name": "Evaluator", "email": "", "cid": None, "eval": True, "memory": False,
           "cancel": threading.Event()}
    results, status = [], "done"
    try:
        defs = {n: t for n, t in ag.tool_defs(ctx).items() if n in ag.EVAL_TOOLS}
        with db.engine.connect() as c:
            cases = c.execute(select(db.eval_cases).order_by(db.eval_cases.c.created_at)).all()
        for case in cases:
            results.append(run_eval_case(case, judge, ctx, defs))
            with db.engine.begin() as c:
                c.execute(update(db.eval_runs).where(db.eval_runs.c.id == run_id).values(
                    summary=json.dumps(summarize_eval(results, len(cases))), results=json.dumps(results)))
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("nova.eval").exception("Evaluation failed")
        status = "failed"
    with db.engine.begin() as c:
        c.execute(update(db.eval_runs).where(db.eval_runs.c.id == run_id).values(
            status=status, summary=json.dumps(summarize_eval(results, max(len(results), 1))), results=json.dumps(results)))


def eval_run_dict(r, full=False):
    status = r.status
    if status == "running" and db.now_us() - r.created_at > 15 * 60 * 10**6:
        status = "failed"  # the server restarted mid-run
    d = {"id": r.id, "created_at": r.created_at, "model": r.model, "status": status, "judge": bool(r.judge),
         "summary": json.loads(r.summary)}
    if full:
        d["results"] = json.loads(r.results)
    return d


def case_dict(r):
    m = r if isinstance(r, dict) else r._mapping
    return {"id": m["id"], "name": m["name"], "question": m["question"], "keywords": json.loads(m["keywords"]),
            "expected_tool": m["expected_tool"], "use_tools": bool(m["use_tools"])}


@bp.get("/evals")
@auth(admin=True)
def admin_evals():
    with db.engine.begin() as c:
        if not c.execute(select(func.count()).select_from(db.eval_cases)).scalar():
            for i, sc in enumerate(SEED_CASES):
                c.execute(insert(db.eval_cases).values(id=str(uuid.uuid4()), name=sc["name"], question=sc["question"],
                                                       keywords=json.dumps(sc["keywords"]), expected_tool=sc["expected_tool"],
                                                       use_tools=sc["use_tools"], created_at=db.now_us() + i))
        cases = c.execute(select(db.eval_cases).order_by(db.eval_cases.c.created_at)).all()
        runs = c.execute(select(db.eval_runs).order_by(db.eval_runs.c.created_at.desc()).limit(10)).all()
    return jsonify(cases=[case_dict(r) for r in cases], runs=[eval_run_dict(r) for r in runs], tools=sorted(ag.EVAL_TOOLS))


@bp.post("/evals/cases")
@auth(admin=True)
def admin_add_eval_case():
    raw = request.get_json(silent=True) or {}
    if isinstance(raw.get("keywords"), str):
        raw["keywords"] = [k.strip() for k in raw["keywords"].split(",") if k.strip()]
    d = parse(raw, {"name": Str(min=1, max=100, required=True, label="Name"),
                    "question": Str(min=1, max=1000, required=True, label="Question"),
                    "keywords": StrList(max_items=10, item_max=100, default=[]), "expected_tool": Str(max=64),
                    "use_tools": Bool()})
    tool = d.get("expected_tool") or None
    if tool and tool not in ag.EVAL_TOOLS:
        raise ApiError(400, "That tool can't be used in evaluations.", "bad_tool")
    with db.engine.begin() as c:
        if c.execute(select(func.count()).select_from(db.eval_cases)).scalar() >= 50:
            raise ApiError(400, "You can keep up to 50 test cases.", "limit")
        row = {"id": str(uuid.uuid4()), "name": d["name"], "question": d["question"],
               "keywords": json.dumps([k.strip() for k in d.get("keywords", []) if k.strip()]),
               "expected_tool": tool, "use_tools": 1 if (d.get("use_tools") or tool) else 0, "created_at": db.now_us()}
        c.execute(insert(db.eval_cases).values(**row))
    audit("admin.eval_case_added", target_type="eval_case", target_id=row["id"])
    return jsonify(case_dict(row)), 201


@bp.delete("/evals/cases/<cid>")
@auth(admin=True)
def admin_delete_eval_case(cid):
    with db.engine.begin() as c:
        r = c.execute(delete(db.eval_cases).where(db.eval_cases.c.id == cid))
    if not r.rowcount:
        raise ApiError(404, "Test case not found.", "not_found")
    audit("admin.eval_case_deleted", target_type="eval_case", target_id=cid)
    return "", 204


@bp.post("/evals/run")
@auth(admin=True)
def admin_run_eval():
    d = parse(request, {"judge": Bool(default=False)})
    llm.client()  # clear error if no provider key
    llm.check_quota(g.user.id)
    with db.engine.begin() as c:
        for r in c.execute(select(db.eval_runs).where(db.eval_runs.c.status == "running")).all():
            if eval_run_dict(r)["status"] == "running":
                raise ApiError(409, "An evaluation is already running. Wait for it to finish.", "busy")
        if not c.execute(select(func.count()).select_from(db.eval_cases)).scalar():
            raise ApiError(400, "Add at least one test case first.", "no_cases")
        rid = str(uuid.uuid4())
        c.execute(insert(db.eval_runs).values(id=rid, created_at=db.now_us(), model=settings.LLM_MODEL, status="running",
                                              judge=1 if d["judge"] else 0, summary=json.dumps(summarize_eval([], 1)),
                                              results="[]"))
    threading.Thread(target=eval_worker, args=(rid, g.user.id, d["judge"]), daemon=True).start()
    audit("admin.eval_started", target_type="eval_run", target_id=rid)
    return jsonify(id=rid), 202


@bp.get("/evals/runs/<rid>")
@auth(admin=True)
def admin_eval_run(rid):
    with db.engine.connect() as c:
        r = c.execute(select(db.eval_runs).where(db.eval_runs.c.id == rid)).first()
    if not r:
        raise ApiError(404, "Run not found.", "not_found")
    return jsonify(eval_run_dict(r, full=True))


