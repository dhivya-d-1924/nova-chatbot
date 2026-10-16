"""Documents, image attachments, memory, reports, human-help requests and health checks."""
import re
import uuid

from flask import Blueprint, Response, g, jsonify, request
from sqlalchemy import delete, func, insert, select, text, update

from . import db, llm, rag
from .agent import create_handoff
from .chat import get_conv
from .config import settings
from .errors import ApiError
from .security import audit, auth
from .validation import Bool, Str, parse

bp = Blueprint("data", __name__, url_prefix="/api")
IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
               (b"GIF89a", "image/gif"))


# ------------------------------------------------------------------ health
@bp.get("/health")
def health():
    """Backwards-compatible status used by the chat header."""
    return jsonify(status="ok", model=settings.LLM_MODEL, configured=settings.LLM_CONFIGURED)


@bp.get("/health/live")
def live():
    """Liveness: the process is up and serving requests."""
    return jsonify(status="ok")


@bp.get("/health/ready")
def ready():
    """Readiness: the database answers and the schema is current. 503 otherwise."""
    checks = {}
    try:
        with db.engine.connect() as c:
            c.execute(text("SELECT 1"))
        checks["database"] = "ok"
        version = db.schema_version()
        checks["schema"] = "ok" if version >= db.LATEST_VERSION else f"behind ({version} < {db.LATEST_VERSION})"
    except Exception:  # noqa: BLE001
        checks["database"] = "unavailable"
    from .security import RedisLimiter, limiter
    if isinstance(limiter, RedisLimiter):
        try:
            limiter.r.ping()
            checks["redis"] = "ok"
        except Exception:  # noqa: BLE001
            checks["redis"] = "unavailable"
    checks["llm"] = "configured" if settings.LLM_CONFIGURED else "not configured"
    ok = checks.get("database") == "ok" and checks.get("schema") == "ok" and checks.get("redis", "ok") == "ok"
    return jsonify(status="ok" if ok else "unavailable", checks=checks), 200 if ok else 503


# ------------------------------------------------------------------ documents
def doc_dict(d):
    m = d if isinstance(d, dict) else d._mapping
    return {"id": m["id"], "filename": m["filename"], "kind": m["kind"], "pages": m["pages"], "size": m["size"],
            "created_at": m["created_at"], "version": m["version"] or 1, "chunks": m["chunk_count"],
            "ocr": bool(m["ocr"]), "embedded": bool(m["embedded"])}


@bp.get("/documents")
@auth()
def list_documents():
    with db.engine.connect() as c:
        rows = c.execute(select(db.documents).where(db.documents.c.user_id == g.user.id)
                         .order_by(db.documents.c.created_at.desc())).all()
    return jsonify([doc_dict(r) for r in rows])


@bp.get("/documents/config")
@auth()
def documents_config():
    return jsonify(extensions=rag.allowed_extensions(), max_mb=settings.MAX_UPLOAD_MB, ocr=rag.ocr_available(),
                   semantic=bool(settings.EMBEDDING_MODEL), max_docs=settings.MAX_DOCS_PER_USER)


@bp.post("/documents")
@auth()
def upload_document():
    files = request.files.getlist("file")
    if len(files) != 1 or not files[0].filename:
        raise ApiError(400, "Choose one file to upload.", "no_file")
    f = files[0]
    data = f.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    doc = rag.ingest(g.user.id, f.filename, data)
    audit("document.uploaded", target_type="document", target_id=doc["id"], size=doc["size"], kind=doc["kind"])
    return jsonify(doc_dict(doc)), 201


@bp.delete("/documents/<did>")
@auth()
def delete_document(did):
    rag.delete_document(g.user.id, did)
    audit("document.deleted", target_type="document", target_id=did)
    return "", 204


@bp.post("/documents/<did>/reindex")
@auth()
def reindex_document(did):
    rag.reindex(g.user.id, did)
    with db.engine.connect() as c:
        row = c.execute(select(db.documents).where(db.documents.c.id == did, db.documents.c.user_id == g.user.id)).first()
    return jsonify(doc_dict(row))


# ------------------------------------------------------------------ image attachments
def sniff_image(data):
    """Identify an image from its first bytes (never trust the file name or declared type)."""
    for sig, mime in IMAGE_MAGIC:
        if data.startswith(sig):
            return mime
    return "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None


@bp.post("/attachments")
@auth()
def upload_attachment():
    f = request.files.get("file")
    if not f:
        raise ApiError(400, "Choose an image to attach.", "no_file")
    data = f.read(settings.MAX_IMAGE_MB * 1024 * 1024 + 1)
    if len(data) > settings.MAX_IMAGE_MB * 1024 * 1024:
        raise ApiError(413, f"Image is too large (max {settings.MAX_IMAGE_MB} MB).", "too_large")
    mime = sniff_image(data)
    if not mime:
        raise ApiError(400, "Only PNG, JPEG, WEBP or GIF images are supported.", "unsupported_type")
    aid = str(uuid.uuid4())
    with db.engine.begin() as c:
        waiting = c.execute(select(func.count()).select_from(db.attachments).where(
            db.attachments.c.user_id == g.user.id, db.attachments.c.message_id.is_(None))).scalar()
        if waiting >= 10:
            raise ApiError(400, "Too many unsent images. Send your message or remove some images first.", "too_many")
        c.execute(insert(db.attachments).values(id=aid, user_id=g.user.id, message_id=None, mime=mime,
                                                data=data, size=len(data), created_at=db.now_us()))
    return jsonify(id=aid, mime=mime, size=len(data)), 201


@bp.delete("/attachments/<aid>")
@auth()
def delete_attachment(aid):
    """Remove an image that was attached to the message box but not sent."""
    with db.engine.begin() as c:
        r = c.execute(delete(db.attachments).where(db.attachments.c.id == aid, db.attachments.c.user_id == g.user.id,
                                                   db.attachments.c.message_id.is_(None)))
    if not r.rowcount:
        raise ApiError(404, "Image not found.", "not_found")
    return "", 204


@bp.get("/attachments/<aid>")
@auth()
def get_attachment(aid):
    with db.engine.connect() as c:
        r = c.execute(select(db.attachments).where(db.attachments.c.id == aid, db.attachments.c.user_id == g.user.id)).first()
    if not r:
        raise ApiError(404, "Image not found.", "not_found")
    return Response(r.data, mimetype=r.mime, headers={"Cache-Control": "private, max-age=3600",
                                                      "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------ memory (opt-in)
def memory_item(r):
    return {"id": r.id, "text": r.text, "created_at": r.created_at}


@bp.get("/memory")
@auth()
def get_memory():
    with db.engine.connect() as c:
        enabled = c.execute(select(db.users.c.memory_enabled).where(db.users.c.id == g.user.id)).scalar()
        rows = c.execute(select(db.memories).where(db.memories.c.user_id == g.user.id)
                         .order_by(db.memories.c.created_at.desc())).all()
    return jsonify(enabled=bool(enabled), limit=settings.MEMORY_LIMIT, items=[memory_item(r) for r in rows])


@bp.put("/memory/settings")
@auth()
def memory_settings():
    d = parse(request, {"enabled": Bool(required=True)})
    with db.engine.begin() as c:
        c.execute(update(db.users).where(db.users.c.id == g.user.id).values(memory_enabled=1 if d["enabled"] else 0))
    audit("memory." + ("enabled" if d["enabled"] else "disabled"))
    return jsonify(enabled=d["enabled"])


@bp.post("/memory")
@auth()
def add_memory():
    d = parse(request, {"text": Str(min=1, max=300, required=True, label="Note")})
    with db.engine.begin() as c:
        if not c.execute(select(db.users.c.memory_enabled).where(db.users.c.id == g.user.id)).scalar():
            raise ApiError(400, "Turn memory on first.", "memory_off")
        n = c.execute(select(func.count()).select_from(db.memories).where(db.memories.c.user_id == g.user.id)).scalar()
        if n >= settings.MEMORY_LIMIT:
            raise ApiError(400, "Memory is full. Delete something first.", "memory_full")
        row = {"id": str(uuid.uuid4()), "user_id": g.user.id, "text": d["text"], "created_at": db.now_us()}
        c.execute(insert(db.memories).values(**row))
    return jsonify({"id": row["id"], "text": row["text"], "created_at": row["created_at"]}), 201


@bp.delete("/memory/<mid>")
@auth()
def delete_memory(mid):
    with db.engine.begin() as c:
        r = c.execute(delete(db.memories).where(db.memories.c.id == mid, db.memories.c.user_id == g.user.id))
    if not r.rowcount:
        raise ApiError(404, "Memory not found.", "not_found")
    return "", 204


@bp.delete("/memory")
@auth()
def clear_memory():
    with db.engine.begin() as c:
        c.execute(delete(db.memories).where(db.memories.c.user_id == g.user.id))
    audit("memory.cleared")
    return "", 204


# ------------------------------------------------------------------ reports & handoffs
@bp.get("/reports")
@auth()
def list_reports():
    with db.engine.connect() as c:
        rows = c.execute(select(db.reports.c.id, db.reports.c.title, db.reports.c.created_at)
                         .where(db.reports.c.user_id == g.user.id).order_by(db.reports.c.created_at.desc()).limit(100)).all()
    return jsonify([{"id": r.id, "title": r.title, "created_at": r.created_at} for r in rows])


@bp.get("/reports/<rid>")
@auth()
def download_report(rid):
    with db.engine.connect() as c:
        r = c.execute(select(db.reports).where(db.reports.c.id == rid, db.reports.c.user_id == g.user.id)).first()
    if not r:
        raise ApiError(404, "Report not found.", "not_found")
    name = re.sub(r"[^\w\- ]+", "", r.title).strip().replace(" ", "-")[:60] or "report"
    return Response(f"# {r.title}\n\n{r.content}\n", mimetype="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@bp.post("/handoffs")
@auth()
def request_handoff():
    d = parse(request, {"note": Str(min=1, max=2000, required=True, label="Your note"),
                        "conversation_id": Str(max=64)})
    cid = d.get("conversation_id") or None
    if cid:
        with db.engine.connect() as c:
            get_conv(c, cid)
    hid = create_handoff(g.user.id, cid, d["note"])
    audit("handoff.requested", target_type="handoff", target_id=hid)
    return jsonify(ok=True, id=hid), 201


@bp.get("/handoffs")
@auth()
def my_handoffs():
    with db.engine.connect() as c:
        rows = c.execute(select(db.handoffs).where(db.handoffs.c.user_id == g.user.id)
                         .order_by(db.handoffs.c.created_at.desc()).limit(50)).all()
    return jsonify([{"id": h.id, "summary": h.summary, "status": h.status, "reply": h.reply,
                     "conversation_id": h.conversation_id, "created_at": h.created_at} for h in rows])


@bp.get("/usage")
@auth()
def my_usage():
    """The user's own token usage today (and the allowance, if any)."""
    return jsonify(tokens_today=llm.tokens_today(g.user.id), daily_limit=settings.DAILY_TOKEN_LIMIT or None)
