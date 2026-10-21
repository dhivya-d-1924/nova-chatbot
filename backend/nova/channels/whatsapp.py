"""WhatsApp via the Meta WhatsApp Cloud API.

Inbound: Meta calls POST /api/channels/whatsapp/webhook. Every request must carry X-Hub-Signature-256, an HMAC of the
raw body made with your app secret; anything else is rejected before it is parsed.
Outbound: replies go to graph.facebook.com/<version>/<phone number id>/messages with your access token. Replies are
sent within the 24-hour customer-service window that the user's own message opens, so no templates are needed.
"""
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from ..config import settings
from . import core

log = logging.getLogger("nova.channels.whatsapp")
MAX_TEXT = 3800  # WhatsApp allows 4096 characters per text message


def verify_signature(raw, header):
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


def verify_subscription(args):
    """Meta's one-time GET check when you save the webhook URL. Returns the challenge to echo, or None."""
    token = args.get("hub.verify_token", "")
    if args.get("hub.mode") == "subscribe" and token and hmac.compare_digest(token, settings.WHATSAPP_VERIFY_TOKEN):
        return args.get("hub.challenge", "")
    return None


def _api(payload):
    url = f"{settings.WHATSAPP_API_BASE}/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",  # noqa: S310  fixed https host
                                 headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read()[:500].decode(errors="replace")
        log.error("WhatsApp API error %s: %s", e.code, detail)
        raise


def send_text(to, text):
    for part in core.chunks(text, MAX_TEXT):
        _api({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
              "text": {"preview_url": False, "body": part}})


def mark_read(message_id):
    """Shows blue ticks so the user knows Nova received the message (best effort)."""
    try:
        _api({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
    except Exception:  # noqa: BLE001
        log.debug("could not mark WhatsApp message read", exc_info=True)


def handle_payload(payload):
    """Turn a verified webhook payload into Inbound messages. Delivery/read status updates are ignored."""
    count = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if str((value.get("metadata") or {}).get("phone_number_id", "")) not in ("", settings.WHATSAPP_PHONE_NUMBER_ID):
                continue  # a different number on the same Meta app
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
            for msg in value.get("messages") or []:
                wa_id, mid, kind = str(msg.get("from", "")), str(msg.get("id", "")), msg.get("type")
                if not wa_id.isdigit() or not mid:
                    continue
                if kind == "text":
                    text = (msg.get("text") or {}).get("body", "")
                elif kind == "button":
                    text = (msg.get("button") or {}).get("text", "")
                elif kind == "interactive":
                    inter = msg.get("interactive") or {}
                    text = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
                else:
                    text = ""  # images, voice notes, etc.: the engine asks for text
                name = names.get(wa_id, "")
                display = f"{name} ({core.mask_phone(wa_id)})" if name else core.mask_phone(wa_id)
                core.submit(core.Inbound(channel="whatsapp", sender=wa_id, text=text[:4000], event_id=mid,
                                         display=display, reply=lambda t, to=wa_id: send_text(to, t),
                                         typing=lambda m=mid: mark_read(m)))
                count += 1
    return count
