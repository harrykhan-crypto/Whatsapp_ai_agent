"""WhatsApp Cloud API webhook (verification + signed message delivery)."""

from __future__ import annotations

import hashlib
import hmac
import logging
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, abort, request

log = logging.getLogger(__name__)


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def extract_messages(payload: dict) -> list[dict]:
    """[{sender, text, id}] for text, button and interactive replies in a webhook payload."""
    found = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for status in value.get("statuses") or []:
                if status.get("status") == "failed":
                    errors = status.get("errors") or [{}]
                    log.warning("WhatsApp reported a failed delivery: %s (%s)", errors[0].get("title"), errors[0].get("code"))
            for message in value.get("messages") or []:
                kind = message.get("type")
                text = ""
                if kind == "text":
                    text = (message.get("text") or {}).get("body", "")
                elif kind == "interactive":
                    interactive = message.get("interactive") or {}
                    reply = interactive.get("list_reply") or interactive.get("button_reply") or {}
                    text = reply.get("id") or reply.get("title") or ""
                elif kind == "button":
                    text = (message.get("button") or {}).get("payload") or (message.get("button") or {}).get("text", "")
                elif kind in {"image", "document", "video"}:
                    text = (message.get(kind) or {}).get("caption", "")
                if text:
                    found.append({"sender": message.get("from", ""), "text": text, "id": message.get("id")})
    return found


def create_webhook_blueprint(settings, message_handler) -> Blueprint:
    bp = Blueprint("whatsapp_webhook", __name__)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wa-handler")

    @bp.get("/webhook")
    def verify():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token", "")
        challenge = request.args.get("hub.challenge", "")
        if mode == "subscribe" and settings.cloud_verify_token and hmac.compare_digest(token, settings.cloud_verify_token):
            log.info("WhatsApp webhook verified")
            return challenge, 200
        log.warning("Webhook verification rejected")
        abort(403)

    @bp.post("/webhook")
    def receive():
        body = request.get_data()
        if not verify_signature(settings.cloud_app_secret, body, request.headers.get("X-Hub-Signature-256")):
            log.warning("Rejected webhook call with a missing/invalid signature")
            abort(403)
        payload = request.get_json(silent=True) or {}
        for message in extract_messages(payload):
            executor.submit(_safe_handle, message_handler, message)
        return "OK", 200  # acknowledge fast; Meta retries slow webhooks

    return bp


def _safe_handle(handler, message: dict) -> None:
    try:
        handler.handle_inbound(message["sender"], message["text"], message.get("id"))
    except Exception:  # noqa: BLE001
        log.exception("Webhook message handling failed")
