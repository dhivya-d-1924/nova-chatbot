"""Outgoing email for account links. Without SMTP, links are printed to the console in development only."""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from .config import settings

log = logging.getLogger("nova.mail")


class MailNotConfigured(RuntimeError):
    pass


def smtp_send(to, subject, body):
    if not settings.SMTP_HOST:
        raise MailNotConfigured("SMTP_HOST is not set")
    msg = EmailMessage()
    msg["Subject"] = subject.replace("\n", " ")[:200]
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if settings.SMTP_TLS == "ssl":
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15, context=ctx) as smtp:
            _login_and_send(smtp, msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_TLS == "starttls":
                smtp.starttls(context=ctx)
            _login_and_send(smtp, msg)


def _login_and_send(smtp, msg):
    if settings.SMTP_USER:
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
    smtp.send_message(msg)


def send_account_email(to, subject, body, link):
    """Deliver an account email. Returns True if sent (or printed in development)."""
    if not settings.SMTP_HOST:
        if settings.IS_PROD:
            log.error("Cannot send '%s' email: SMTP_HOST is not configured", subject)
            return False
        # development/testing only: the link is shown in the server console, never in production logs
        print(f"\n[{subject}] for {to}:\n  {link}\n", flush=True)
        return True
    try:
        smtp_send(to, subject, f"{body}\n\n{link}\n\nIf you didn't ask for this, you can ignore this email.")
        return True
    except Exception:  # noqa: BLE001
        log.exception("Could not send '%s' email", subject)
        return False
