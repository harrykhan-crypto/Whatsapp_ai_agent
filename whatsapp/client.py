"""WhatsApp senders.

* ``AgentPlatformMessenger`` – the project's existing integration (``whagent`` /
  WhatsApp Agent Platform). Polls for messages, so no public URL is required.
* ``CloudApiMessenger`` – the official WhatsApp Cloud API (Graph API), with
  webhook receiving, interactive list menus and media upload.
* ``ConsoleMessenger`` – prints to the terminal; used for testing.

All of them chunk long texts, retry transient failures and raise
``DeliveryError`` when a message finally cannot be delivered.
"""

from __future__ import annotations

import logging
import mimetypes
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

TEXT_LIMIT = 3800


class DeliveryError(RuntimeError):
    pass


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    if len(chunks) > 1:
        chunks = [f"{chunk}\n\n_({i}/{len(chunks)})_" for i, chunk in enumerate(chunks, 1)]
    return chunks


def guess_mime(path: str | Path, declared: str | None = None) -> str:
    if declared and declared != "application/octet-stream":
        return declared.split(";", 1)[0].strip()
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


class Messenger(ABC):
    name = "base"
    max_document_bytes = 16 * 1024 * 1024

    def __init__(self, retries: int = 3, backoff: float = 2.0):
        self.retries = retries
        self.backoff = backoff
        self._lock = threading.Lock()

    # -- public API ---------------------------------------------------------
    def send_text(self, text: str, to: str | None = None) -> list[str]:
        ids = []
        for chunk in split_text(text):
            ids.append(self._with_retry(lambda c=chunk: self._send_text(c, to), "text"))
        return ids

    def send_document(self, path: str | Path, *, filename: str | None = None, caption: str | None = None,
                      mime_type: str | None = None, to: str | None = None) -> str:
        path = Path(path)
        if not path.exists():
            raise DeliveryError(f"File not found: {path.name}")
        size = path.stat().st_size
        if size > self.max_document_bytes:
            raise DeliveryError(
                f"{path.name} is {size / 1048576:.1f} MB; WhatsApp allows up to {self.max_document_bytes // 1048576} MB here."
            )
        filename = filename or path.name
        caption = (caption or "")[:1000] or None
        return self._with_retry(
            lambda: self._send_document(path, filename, caption, guess_mime(path, mime_type), to), "document"
        )

    def send_menu(self, text: str, to: str | None = None) -> list[str]:
        """Interactive list where supported; plain text otherwise."""
        return self.send_text(text, to)

    @property
    def recipient(self) -> str | None:
        return None

    # -- provider hooks -------------------------------------------------------
    @abstractmethod
    def _send_text(self, text: str, to: str | None) -> str: ...

    @abstractmethod
    def _send_document(self, path: Path, filename: str, caption: str | None, mime_type: str, to: str | None) -> str: ...

    def _retryable(self, error: Exception) -> bool:
        return True

    def _with_retry(self, action, what: str) -> str:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self._lock:
                    return action()
            except DeliveryError:
                raise
            except Exception as error:  # noqa: BLE001
                last = error
                if not self._retryable(error) or attempt >= self.retries:
                    break
                wait = self.backoff * (2 ** attempt)
                log.warning("WhatsApp %s send failed (%s); retrying in %.0fs", what, error, wait)
                time.sleep(wait)
        raise DeliveryError(f"WhatsApp {what} could not be delivered: {last}")


# ---------------------------------------------------------------------------- console
class ConsoleMessenger(Messenger):
    name = "console"

    def __init__(self):
        super().__init__(retries=0)
        self.sent: list[dict[str, Any]] = []

    def _send_text(self, text: str, to: str | None) -> str:
        self.sent.append({"type": "text", "to": to, "body": text})
        print(f"\n----- WhatsApp → {to or 'me'} -----\n{text}\n")
        return f"console-{len(self.sent)}"

    def _send_document(self, path: Path, filename: str, caption: str | None, mime_type: str, to: str | None) -> str:
        self.sent.append({"type": "document", "to": to, "path": str(path), "filename": filename,
                          "caption": caption, "mime_type": mime_type})
        print(f"\n----- WhatsApp document → {to or 'me'}: {filename} ({mime_type}) -----\n")
        return f"console-{len(self.sent)}"


# ---------------------------------------------------------------- WhatsApp Agent Platform
class AgentPlatformMessenger(Messenger):
    """Uses the ``whagent`` library already used by whatsapp_agent.py."""

    name = "agent"

    def __init__(self, token: str, *, base_url: str = "", repo=None):
        super().__init__()
        from whagent import Client
        from whagent.limits import ACCEPTED_MIME_TYPES, SIZE_LIMITS

        kwargs = {"base_url": base_url} if base_url else {}
        # auto_discover is disabled: a discovery poll would interrupt the running poll loop.
        self.client = Client(token, auto_discover=False, **kwargs)
        self.accepted = ACCEPTED_MIME_TYPES
        self.max_document_bytes = SIZE_LIMITS["document"]
        self.repo = repo

    @property
    def recipient(self) -> str | None:
        found = self.client.recipient
        if found:
            if self.repo is not None and self.repo.get_pref("agent_recipient") != found:
                self.repo.set_pref("agent_recipient", found)
            return found
        return self.repo.get_pref("agent_recipient") if self.repo is not None else None

    def _target(self, to: str | None) -> str:
        target = to or self.recipient
        if not target:
            raise DeliveryError(
                "The WhatsApp recipient is not known yet. Send any message (e.g. 'menu') to your agent once."
            )
        return target

    def _retryable(self, error: Exception) -> bool:
        from whagent import errors

        if isinstance(error, (errors.AuthenticationError, errors.ForbiddenError, errors.ValidationError,
                              errors.InvalidRequestError)):
            return False
        return True

    def _send_text(self, text: str, to: str | None) -> str:
        return self.client.send_text(self._target(to), text).message_id

    def _send_document(self, path: Path, filename: str, caption: str | None, mime_type: str, to: str | None) -> str:
        if mime_type not in self.accepted:
            mime_type = "application/octet-stream"  # e.g. ZIP/RAR: sent as a generic binary document
        target = self._target(to)
        media_id = self.client.upload_media(path, mime_type=mime_type, filename=filename)
        return self.client.send_document(target, media_id=media_id, filename=filename, caption=caption).message_id


# ------------------------------------------------------------------- WhatsApp Cloud API
class CloudApiError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class CloudApiMessenger(Messenger):
    name = "cloud"
    max_document_bytes = 100 * 1024 * 1024

    def __init__(self, token: str, phone_number_id: str, *, recipient: str, api_version: str = "v21.0",
                 api_base: str = "https://graph.facebook.com", template_name: str = "", template_language: str = "en_US",
                 session: requests.Session | None = None):
        super().__init__()
        if not token or not phone_number_id:
            raise ValueError("WHATSAPP_CLOUD_TOKEN and WHATSAPP_PHONE_NUMBER_ID are required for the Cloud API.")
        self.token = token
        self.phone_number_id = phone_number_id
        self.default_recipient = recipient
        self.base = f"{api_base.rstrip('/')}/{api_version}/{phone_number_id}"
        self.template_name = template_name
        self.template_language = template_language
        self.http = session or requests.Session()

    @property
    def recipient(self) -> str | None:
        return self.default_recipient or None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _post(self, path: str, **kwargs) -> dict:
        response = self.http.post(f"{self.base}/{path}", headers=self._headers(), timeout=60, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise CloudApiError(err.get("message") or f"HTTP {response.status_code}", err.get("code"), response.status_code)
        return data

    def _retryable(self, error: Exception) -> bool:
        if isinstance(error, CloudApiError):
            # 4/80007/130429/131056 = rate limits; 5xx/131000/131016 = temporary errors
            return error.status is None or error.status >= 500 or error.code in {4, 80007, 130429, 131000, 131016, 131056}
        return isinstance(error, requests.RequestException)

    def _target(self, to: str | None) -> str:
        target = (to or self.default_recipient or "").replace("+", "")
        if not target:
            raise DeliveryError("Set WHATSAPP_RECIPIENT (your number with country code, e.g. 923001234567).")
        return target

    def _message(self, to: str | None, payload: dict) -> str:
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": self._target(to), **payload}
        try:
            data = self._post("messages", json=body)
        except CloudApiError as error:
            # 131047: more than 24h since the user's last message -> only templates are allowed.
            if error.code == 131047 and self.template_name and payload.get("type") != "template":
                log.warning("Outside the 24h window; sending template '%s' to reopen the conversation", self.template_name)
                self._send_template(to)
                data = self._post("messages", json=body)
            else:
                raise
        messages = data.get("messages") or [{}]
        return messages[0].get("id", "")

    def _send_template(self, to: str | None) -> None:
        self._post("messages", json={
            "messaging_product": "whatsapp", "to": self._target(to), "type": "template",
            "template": {"name": self.template_name, "language": {"code": self.template_language}},
        })

    def _send_text(self, text: str, to: str | None) -> str:
        return self._message(to, {"type": "text", "text": {"preview_url": False, "body": text}})

    def upload(self, path: Path, mime_type: str) -> str:
        with open(path, "rb") as handle:
            data = self._post("media", data={"messaging_product": "whatsapp", "type": mime_type},
                              files={"file": (path.name, handle, mime_type)})
        media_id = data.get("id")
        if not media_id:
            raise CloudApiError("Media upload returned no id")
        return media_id

    def _send_document(self, path: Path, filename: str, caption: str | None, mime_type: str, to: str | None) -> str:
        media_id = self.upload(path, mime_type)
        document = {"id": media_id, "filename": filename}
        if caption:
            document["caption"] = caption
        return self._message(to, {"type": "document", "document": document})

    def send_menu(self, text: str, to: str | None = None) -> list[str]:
        rows = [
            ("today", "Due today", "Everything due today"), ("tomorrow", "Due tomorrow", "Tomorrow's deadlines"),
            ("upcoming", "Upcoming", "All upcoming activities"), ("assignments", "Assignments", "All assignments"),
            ("quizzes", "Quizzes", "Upcoming quizzes"), ("labs", "Labs", "Lab tasks & reports"),
            ("tasks", "Tasks", "Weekly tasks & activities"), ("overdue", "Overdue", "Missing & overdue work"),
            ("files", "Files", "Available LMS files"), ("report", "Full report", "Complete academic report"),
        ]
        payload = {
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": "HARRY — Abasyn LMS"},
                "body": {"text": "Choose what you want to see, or just type your question in English or Roman Urdu."},
                "footer": {"text": "Type /help for all commands"},
                "action": {"button": "Open menu", "sections": [{
                    "title": "Academic info",
                    "rows": [{"id": f"/{rid}", "title": title, "description": desc} for rid, title, desc in rows],
                }]},
            },
        }
        try:
            return [self._with_retry(lambda: self._message(to, payload), "menu")]
        except DeliveryError:
            log.warning("Interactive menu failed; sending text menu instead")
            return self.send_text(text, to)


def build_messenger(settings, repo=None) -> Messenger:
    provider = settings.whatsapp_provider
    if provider == "console" or not settings.enable_whatsapp:
        return ConsoleMessenger()
    if provider == "cloud":
        import os

        return CloudApiMessenger(
            settings.cloud_token, settings.cloud_phone_number_id, recipient=settings.recipient_number,
            api_version=settings.cloud_api_version, api_base=settings.cloud_api_base,
            template_name=os.getenv("WHATSAPP_TEMPLATE_NAME", ""),
            template_language=os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US"),
        )
    if not settings.agent_api_key:
        raise SystemExit("WHATSAPP_AGENT_API_KEY is missing from .env (or set WHATSAPP_PROVIDER=cloud/console).")
    return AgentPlatformMessenger(settings.agent_api_key, base_url=settings.agent_base_url, repo=repo)
