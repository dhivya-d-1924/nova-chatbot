"""Internal app integrations (any REST API, set up by an administrator) and each person's Google Workspace account."""
from .routes import bp

__all__ = ["bp", "tools_for"]


def tools_for(ctx):
    """Extra agent tools for this user: enabled internal app actions plus their Google tools when connected."""
    from . import google, rest
    out = {}
    try:
        out.update(rest.tools_for(ctx))
    except Exception:  # noqa: BLE001  a broken integration must never break the chat
        import logging
        logging.getLogger("nova.integrations").exception("could not load internal app tools")
    out.update(google.tools_for(ctx))
    return out
