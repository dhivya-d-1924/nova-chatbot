"""Error recording, structured logging and configurable alerts."""
import json
import logging
import threading
import time
import urllib.request
import uuid

from flask import g, has_request_context
from sqlalchemy import func, insert, select

from . import db
from .config import settings

log = logging.getLogger("nova.monitor")
_alert_lock = threading.Lock()
_last_alert = 0.0


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname, "logger": record.name,
               "msg": record.getMessage()}
        if has_request_context():
            out["request_id"] = getattr(g, "request_id", None)
        for key in ("http", "audit"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging():
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if settings.LOG_FORMAT == "json"
                         else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.handlers = [handler]
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log_error(uid, message):
    try:
        with db.engine.begin() as c:
            c.execute(insert(db.errors).values(id=str(uuid.uuid4()), user_id=uid, message=message[:1000],
                                               created_at=db.now_us()))
    except Exception:  # noqa: BLE001
        log.exception("Could not log error")
    maybe_alert()


def maybe_alert():
    """Notify ALERT_WEBHOOK_URL / ALERT_EMAIL when errors in the last 10 minutes pass ALERT_ERROR_THRESHOLD.
    At most one alert every 30 minutes per process."""
    global _last_alert
    if not (settings.ALERT_WEBHOOK_URL or settings.ALERT_EMAIL):
        return
    if time.time() - _last_alert < 1800:
        return
    try:
        with db.engine.connect() as c:
            n = c.execute(select(func.count()).select_from(db.errors)
                          .where(db.errors.c.created_at >= db.now_us() - 600 * 10**6)).scalar()
    except Exception:  # noqa: BLE001
        return
    if n < settings.ALERT_ERROR_THRESHOLD:
        return
    with _alert_lock:
        if time.time() - _last_alert < 1800:
            return
        _last_alert = time.time()
    text = f"Nova: {n} errors in the last 10 minutes (threshold {settings.ALERT_ERROR_THRESHOLD}). Check the admin dashboard."
    threading.Thread(target=_send_alert, args=(text,), daemon=True).start()


def _send_alert(text):
    if settings.ALERT_WEBHOOK_URL:
        try:
            # ALERT_WEBHOOK_URL is validated as http(s) at startup
            req = urllib.request.Request(settings.ALERT_WEBHOOK_URL, data=json.dumps({"text": text}).encode(),  # noqa: S310
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).close()  # noqa: S310  admin-configured URL
        except Exception:  # noqa: BLE001
            log.exception("alert webhook failed")
    if settings.ALERT_EMAIL:
        try:
            from .mail import smtp_send
            smtp_send(settings.ALERT_EMAIL, "Nova alert", text)
        except Exception:  # noqa: BLE001
            log.exception("alert email failed")
