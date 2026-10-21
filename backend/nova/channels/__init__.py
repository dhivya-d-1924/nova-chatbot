"""Channels: chat with Nova from WhatsApp (Meta Cloud API), Microsoft Teams (Azure Bot) and email (IMAP + SMTP)."""
from .routes import bp

__all__ = ["bp", "start_background"]


def start_background(app):
    """Start per-process background work (the email poller). Safe to call on every request."""
    from .email_channel import start_poller
    start_poller(app)
