"""Receives a WhatsApp message (from any provider), runs it and delivers the replies."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from services.command_handler import CommandHandler, HandlerResult
from whatsapp.client import DeliveryError, Messenger

log = logging.getLogger(__name__)


class MessageHandler:
    def __init__(self, commands: CommandHandler, messenger: Messenger, *, allowed_senders: list[str] | None = None):
        self.commands = commands
        self.messenger = messenger
        self.allowed = {s.replace("+", "") for s in (allowed_senders or []) if s}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def _duplicate(self, message_id: str | None) -> bool:
        if not message_id:
            return False
        with self._lock:
            if message_id in self._seen:
                return True
            self._seen[message_id] = None
            while len(self._seen) > 2000:
                self._seen.popitem(last=False)
        return False

    def is_allowed(self, sender: str) -> bool:
        return not self.allowed or sender.replace("+", "") in self.allowed

    def handle_inbound(self, sender: str, text: str, message_id: str | None = None) -> HandlerResult | None:
        if self._duplicate(message_id):
            log.info("Ignoring duplicate message %s", message_id)
            return None
        if not self.is_allowed(sender):
            log.warning("Ignoring a WhatsApp message from %s: not in WHATSAPP_ALLOWED_NUMBERS", sender)
            return None
        log.info("📩 WhatsApp message received: %r", (text or "")[:80])

        def progress(message: str) -> None:
            try:
                self.messenger.send_text(message, to=sender)
            except DeliveryError as error:
                log.warning("Progress message failed: %s", error)

        result = self.commands.handle(text or "", progress=progress)
        self.deliver(result, sender)
        log.info("📤 Replied with %s message(s)", len(result.replies))
        return result

    def deliver(self, result: HandlerResult, to: str | None) -> None:
        for reply in result.replies:
            try:
                if reply.kind == "menu":
                    self.messenger.send_menu(reply.text, to=to)
                elif reply.kind == "document":
                    self.messenger.send_document(reply.path, filename=reply.filename, caption=reply.caption,
                                                 mime_type=reply.mime_type, to=to)
                else:
                    self.messenger.send_text(reply.text, to=to)
            except DeliveryError as error:
                log.error("Delivery failed: %s", error)
                if reply.kind == "document":
                    try:
                        self.messenger.send_text(f"⚠️ Couldn't send *{reply.filename}*: {error}", to=to)
                    except DeliveryError:
                        pass
