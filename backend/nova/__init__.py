"""Nova AI agent chatbot: Flask application factory."""
import logging
import time
import uuid

from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import channels, db, llm, maintenance, monitor, security
from .config import DIST, ENV_PATH, settings
from .errors import ApiError

log = logging.getLogger("nova")


def create_app(overrides=None):
    """Build the app. overrides (dict) replace environment variables, which tests use for isolation."""
    settings.load(overrides)
    monitor.configure_logging()
    settings.validate()
    db.init_engine(settings.DATABASE_URL)
    db.migrate()
    security.init_limiter()
    llm.reset_clients()

    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=(max(settings.MAX_UPLOAD_MB, settings.MAX_IMAGE_MB) + 1) * 1024 * 1024,
                      SECRET_KEY=settings.SECRET_KEY, JSON_SORT_KEYS=False, TESTING=settings.IS_TEST)
    if settings.TRUSTED_PROXY_COUNT:
        n = settings.TRUSTED_PROXY_COUNT
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)
    if settings.ALLOWED_ORIGINS:
        CORS(app, resources={r"/api/*": {"origins": settings.ALLOWED_ORIGINS}}, supports_credentials=True,
             allow_headers=["Content-Type", "X-CSRF-Token"], expose_headers=["X-Request-Id"], max_age=600)

    @app.before_request
    def before():
        g.request_id = request.headers.get("X-Request-Id", "")[:64] or uuid.uuid4().hex[:16]
        g.t0 = time.perf_counter()
        if not request.path.startswith("/api/"):
            return
        security.origin_guard()
        if request.method == "OPTIONS":
            return
        security.enforce_rate("api", settings.API_RATE_LIMIT)
        if request.method == "POST":
            if request.path.startswith("/api/auth/"):
                security.enforce_rate("auth", settings.AUTH_RATE_LIMIT)
            elif request.path.startswith("/api/chat"):
                security.enforce_rate("chat", settings.RATE_LIMIT)
            elif request.path in ("/api/documents", "/api/attachments"):
                security.enforce_rate("upload", settings.UPLOAD_RATE_LIMIT)
        if not settings.IS_TEST:
            maintenance.tick()
            if settings.EMAIL_CHANNEL_ENABLED:
                channels.start_background(app)  # once per process; CLI commands never start the poller

    @app.after_request
    def after(resp):
        security.security_headers(resp)
        resp.headers["X-Request-Id"] = getattr(g, "request_id", "")
        if request.path.startswith("/api/") and request.path not in ("/api/health/live",):
            ms = int((time.perf_counter() - getattr(g, "t0", time.perf_counter())) * 1000)
            log.info("%s %s %s %sms", request.method, request.path, resp.status_code, ms,
                     extra={"http": {"method": request.method, "path": request.path, "status": resp.status_code, "ms": ms,
                                     "user": getattr(getattr(g, "user", None), "id", None)}})
        return resp

    @app.errorhandler(ApiError)
    def handle_api_error(e):
        return security.json_error(e.status, e.message, e.code)

    @app.errorhandler(413)
    def too_large(_):
        return security.json_error(413, f"File is too large (max {settings.MAX_UPLOAD_MB} MB).", "too_large")

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/") or not (DIST / "index.html").exists():
            return security.json_error(404, "Not found", "not_found")
        return _index()

    @app.errorhandler(405)
    def not_allowed(_):
        return security.json_error(405, "Method not allowed", "method_not_allowed")

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        if isinstance(e, HTTPException):
            return security.json_error(e.code or 500, e.description or "Request failed")
        log.exception("Unhandled error")
        # never leak stack traces or internals to the client
        return security.json_error(500, "Something went wrong on the server. Please try again.", "server_error")

    from .account_routes import bp as account_bp
    from .admin_routes import bp as admin_bp
    from .auth_routes import bp as auth_bp
    from .chat_routes import bp as chat_bp
    from .conversation_routes import bp as conversation_bp
    from .data_routes import bp as data_bp
    from .integrations import bp as integrations_bp
    from .study import bp as study_bp
    for bp in (auth_bp, account_bp, conversation_bp, chat_bp, data_bp, admin_bp, channels.bp, study_bp, integrations_bp):
        app.register_blueprint(bp)

    # ---- built frontend (single-server deployment)
    def _index():
        resp = send_from_directory(DIST, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/")
    def index():
        if not (DIST / "index.html").exists():
            return jsonify(status="ok", message="API is running. Build the frontend (npm run build) or use the Vite dev server.")
        return _index()

    @app.get("/<path:path>")
    def static_files(path):
        if path.startswith("api/"):
            return security.json_error(404, "Not found", "not_found")
        target = (DIST / path).resolve()
        if DIST.exists() and target.is_file() and DIST.resolve() in target.parents:
            resp = send_from_directory(DIST, path)
            if path.startswith("assets/"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # hashed file names
            return resp
        if not (DIST / "index.html").exists():
            return security.json_error(404, "Not found", "not_found")
        return _index()  # client-side routes

    from . import cli
    cli.register(app)

    if not settings.IS_TEST:
        if not ENV_PATH.exists() and not settings.IS_PROD:
            log.warning("No .env file at %s. Copy .env.example to .env (check it is not named .env.txt).", ENV_PATH)
        log.info("Nova ready (env=%s, model=%s, db=%s)", settings.APP_ENV, settings.LLM_MODEL,
                 settings.public()["database"])
    return app
