"""Central configuration, loaded from environment variables / .env.

Nothing secret is hard-coded here. Copy .env.example to .env and fill it in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    path = Path(value) if value else default
    return path if path.is_absolute() else ROOT / path


@dataclass
class Settings:
    # --- LMS ---------------------------------------------------------------
    moodle_url: str = field(default_factory=lambda: os.getenv("MOODLE_URL", "https://fall2026.auplmscs.com").rstrip("/"))
    profile_dir: Path = field(default_factory=lambda: _path("BROWSER_PROFILE_DIR", ROOT / "moodle_browser_profile"))
    headless: bool = field(default_factory=lambda: _bool("BROWSER_HEADLESS", False))
    browser_executable: str = field(default_factory=lambda: os.getenv("BROWSER_EXECUTABLE_PATH", ""))
    browser_channel: str = field(default_factory=lambda: os.getenv("BROWSER_CHANNEL", ""))  # e.g. "chrome" or "msedge"
    page_timeout_ms: int = field(default_factory=lambda: _int("PAGE_TIMEOUT_MS", 45_000))
    expected_courses: list[str] = field(default_factory=lambda: _list(
        "EXPECTED_COURSES",
        "Automata Theory (CB),Operating System Concepts (CB),Human Computer Interaction (CB),"
        "Software Construction and Development (CA),Software Quality Engineering (CA),"
        "Introduction to Psychology (CB)",
    ))

    # --- Storage -----------------------------------------------------------
    database_path: Path = field(default_factory=lambda: _path("DATABASE_PATH", ROOT / "storage" / "agent.db"))
    download_dir: Path = field(default_factory=lambda: _path("DOWNLOAD_DIR", ROOT / "storage" / "downloads"))
    backup_dir: Path = field(default_factory=lambda: _path("BACKUP_DIR", ROOT / "storage" / "backups"))
    log_dir: Path = field(default_factory=lambda: _path("LOG_DIR", ROOT / "logs"))
    max_download_mb: int = field(default_factory=lambda: _int("MAX_DOWNLOAD_MB", 16))
    backups_to_keep: int = field(default_factory=lambda: _int("BACKUPS_TO_KEEP", 7))

    # --- Time & schedule ---------------------------------------------------
    timezone_name: str = field(default_factory=lambda: os.getenv("TIMEZONE", "Asia/Karachi"))
    sync_interval_minutes: int = field(default_factory=lambda: _int("SYNC_INTERVAL_MINUTES", 30))
    morning_summary_time: str = field(default_factory=lambda: os.getenv("MORNING_SUMMARY_TIME", "08:00"))
    evening_reminder_time: str = field(default_factory=lambda: os.getenv("EVENING_REMINDER_TIME", "20:00"))
    reminder_hours: list[int] = field(default_factory=lambda: [int(h) for h in _list("REMINDER_HOURS", "24,3")])
    stale_minutes: int = field(default_factory=lambda: _int("DATA_STALE_MINUTES", 45))

    # --- WhatsApp ----------------------------------------------------------
    # "agent"  = WhatsApp Agent Platform via the whagent library (existing integration, no public URL needed)
    # "cloud"  = official WhatsApp Cloud API (needs a public HTTPS webhook)
    # "console" = print messages to the terminal (testing without WhatsApp)
    whatsapp_provider: str = field(default_factory=lambda: os.getenv("WHATSAPP_PROVIDER", "agent").strip().lower())
    agent_api_key: str = field(default_factory=lambda: os.getenv("WHATSAPP_AGENT_API_KEY", ""))
    agent_base_url: str = field(default_factory=lambda: os.getenv("WHATSAPP_AGENT_BASE_URL", ""))
    cloud_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_CLOUD_TOKEN", ""))
    cloud_phone_number_id: str = field(default_factory=lambda: os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""))
    cloud_api_version: str = field(default_factory=lambda: os.getenv("WHATSAPP_API_VERSION", "v21.0"))
    cloud_api_base: str = field(default_factory=lambda: os.getenv("WHATSAPP_API_BASE", "https://graph.facebook.com").rstrip("/"))
    cloud_verify_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_VERIFY_TOKEN", ""))
    cloud_app_secret: str = field(default_factory=lambda: os.getenv("WHATSAPP_APP_SECRET", ""))
    recipient_number: str = field(default_factory=lambda: os.getenv("WHATSAPP_RECIPIENT", "").replace("+", "").replace(" ", ""))
    allowed_numbers: list[str] = field(default_factory=lambda: [n.replace("+", "") for n in _list("WHATSAPP_ALLOWED_NUMBERS")])
    student_name: str = field(default_factory=lambda: os.getenv("STUDENT_NAME", ""))

    # --- Free local AI (Ollama) -------------------------------------------
    # auto = use Ollama when it is running on this PC; off = never
    ai_mode: str = field(default_factory=lambda: os.getenv("AI_MODE", "auto").strip().lower())
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:3b"))
    ai_timeout: int = field(default_factory=lambda: _int("AI_TIMEOUT", 60))

    # --- Web server --------------------------------------------------------
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("PORT", 5000))
    api_key: str = field(default_factory=lambda: os.getenv("LOCAL_API_KEY", ""))

    # --- Feature switches --------------------------------------------------
    enable_scheduler: bool = field(default_factory=lambda: _bool("ENABLE_SCHEDULER", True))
    enable_whatsapp: bool = field(default_factory=lambda: _bool("ENABLE_WHATSAPP", True))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for path in (self.profile_dir, self.download_dir, self.backup_dir, self.log_dir,
                     self.database_path.parent):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
