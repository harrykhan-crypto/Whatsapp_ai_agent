"""Logging to console + rotating file, with secrets redacted."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I),
    re.compile(r"((?:access_token|token|password|passwd|secret|key|wstoken)=)[^&\s\"']+", re.I),
    re.compile(r"(MoodleSession\w*=)[^;\s]+", re.I),
    re.compile(r"(\"?(?:password|token|secret)\"?\s*:\s*\")[^\"]+", re.I),
]


class RedactFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        self.secrets = [s for s in (secrets or []) if s and len(s) >= 6]

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        for pattern in _PATTERNS:
            text = pattern.sub(r"\1***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = self.redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        if record.exc_info and record.exc_info[1] is not None:
            # Make sure exception text is redacted too.
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(log_dir: Path, secrets: list[str] | None = None, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    redact = RedactFilter(secrets)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = logging.StreamHandler()
    file_handler = RotatingFileHandler(log_dir / "agent.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    for handler in (console, file_handler):
        handler.setFormatter(fmt)
        handler.addFilter(redact)
        root.addHandler(handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
