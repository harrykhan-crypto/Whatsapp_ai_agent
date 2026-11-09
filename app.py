"""HARRY — Abasyn WhatsApp LMS Intelligence Agent.

One process runs everything:
  * the persistent Moodle browser (Playwright, existing logged-in profile)
  * the LMS sync engine + SQLite database
  * the scheduler (30-min checks, 8 AM summary, 8 PM reminder, 24h/3h reminders)
  * WhatsApp: Agent Platform polling (default) or Cloud API webhook
  * the local HTTP API (kept compatible: /, /health, POST /ask, POST /files/search)

Run:  python app.py
"""

from __future__ import annotations

import ipaddress
import logging
import signal
import sys
import threading
from dataclasses import dataclass
from functools import wraps

from flask import Flask, abort, jsonify, request

from config import Settings, settings as default_settings
from database.db import Database
from database.models import Repository
from lms.browser import BrowserWorker
from logging_setup import setup_logging
from services.command_handler import CommandHandler
from services.monitoring import LmsService
from services.notification import NotificationService
from services.report_generator import ReportGenerator
from services.scheduler import Scheduler
from whatsapp.client import Messenger, build_messenger
from whatsapp.message_handler import MessageHandler
from whatsapp.webhook import create_webhook_blueprint

log = logging.getLogger("harry")


@dataclass
class Container:
    settings: Settings
    db: Database
    repo: Repository
    worker: BrowserWorker | None
    lms: LmsService | None
    reports: ReportGenerator
    commands: CommandHandler
    messenger: Messenger
    notifier: NotificationService
    messages: MessageHandler
    scheduler: Scheduler


def build(settings: Settings = default_settings, *, messenger: Messenger | None = None,
          with_browser: bool = True) -> Container:
    settings.ensure_dirs()
    db = Database(settings.database_path)
    repo = Repository(db)
    worker = lms = None
    if with_browser:
        worker = BrowserWorker(settings.profile_dir, settings.moodle_url, headless=settings.headless,
                               timeout_ms=settings.page_timeout_ms, executable_path=settings.browser_executable,
                               channel=settings.browser_channel)
        lms = LmsService(worker, repo, download_dir=settings.download_dir,
                         max_download_bytes=settings.max_download_bytes)
    reports = ReportGenerator(repo)
    commands = CommandHandler(repo, lms, reports, stale_minutes=settings.stale_minutes,
                              student_name=settings.student_name)
    messenger = messenger or build_messenger(settings, repo)
    notifier = NotificationService(repo, messenger, reports, settings.reminder_hours)
    if lms is not None:
        lms.on_session_expired = notifier.session_expired
        lms.on_session_restored = notifier.session_restored
    # The allow-list only applies to the Cloud API (phone numbers). The Agent Platform
    # identifies you as "user:<id>" and only ever delivers messages from you anyway.
    allowed: list[str] = []
    if settings.whatsapp_provider == "cloud":
        allowed = settings.allowed_numbers or ([settings.recipient_number] if settings.recipient_number else [])
    messages = MessageHandler(commands, messenger, allowed_senders=allowed)
    scheduler = Scheduler(settings, db, repo, lms, notifier)
    return Container(settings, db, repo, worker, lms, reports, commands, messenger, notifier, messages, scheduler)


def _is_local_request() -> bool:
    if request.headers.get("X-Forwarded-For") or request.headers.get("Cf-Connecting-Ip") or request.headers.get("Forwarded"):
        return False  # came through a tunnel (ngrok/cloudflared): treat as internet traffic
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def create_app(c: Container) -> Flask:
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    def local_only(view):
        """Local API: loopback without a tunnel, or the LOCAL_API_KEY header."""
        @wraps(view)
        def wrapper(*args, **kwargs):
            key = c.settings.api_key
            if key and request.headers.get("X-API-Key") == key:
                return view(*args, **kwargs)
            if _is_local_request():
                return view(*args, **kwargs)
            abort(403)
        return wrapper

    @app.get("/")
    def home():
        return ("HARRY Moodle reader is running. Use /health, POST /ask, POST /files/search, "
                "POST /sync, GET /report. WhatsApp Cloud API webhook: /webhook")

    @app.get("/health")
    def health():
        last = c.repo.last_sync(ok_only=False)
        return jsonify({
            "ok": True,
            "service": "HARRY Moodle reader",
            "moodle_url": c.settings.moodle_url,
            "browser_started": bool(c.worker and c.worker.alive),
            "browser_restarts": c.worker.restarts if c.worker else 0,
            "session_state": c.repo.get_pref("session_state") or "unknown",
            "syncing": bool(c.lms and c.lms.syncing),
            "last_sync": {k: last[k] for k in ("started_at", "finished_at", "ok", "courses", "activities", "error")} if last else None,
            "courses": len(c.repo.courses()),
            "whatsapp_provider": c.messenger.name,
        })

    @app.post("/ask")
    @local_only
    def ask():
        payload = request.get_json(silent=True) or {}
        question = str(payload.get("question", "")).strip()
        if not question:
            return jsonify({"answer": "Please send a question in the 'question' field."}), 400
        result = c.commands.handle(question)
        files = [{"filename": r.filename, "path": r.path} for r in result.replies if r.kind == "document"]
        answer = "\n\n".join(r.text for r in result.replies if r.kind in ("text", "menu"))
        return jsonify({"answer": answer, "files": files})

    @app.post("/files/search")
    @local_only
    def search_files():
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query", "")).strip()
        if not query:
            return jsonify({"files": [], "error": "Enter a course name or file type."}), 400
        result = c.commands.handle(f"send files {query}")
        files = [{"filename": r.filename, "path": r.path, "caption": r.caption} for r in result.replies if r.kind == "document"]
        if not files:
            return jsonify({"files": [], "error": result.combined_text})
        return jsonify({"files": files})

    @app.post("/sync")
    @local_only
    def sync():
        if c.lms is None:
            return jsonify({"ok": False, "error": "Browser is disabled"}), 503
        result = c.lms.sync(force_details=bool((request.get_json(silent=True) or {}).get("force")))
        if result.ok:
            c.notifier.send_event_digest()
        return jsonify(result.__dict__)

    @app.get("/report")
    @local_only
    def report():
        return jsonify({"report": c.reports.full_report()})

    @app.get("/activities")
    @local_only
    def activities():
        return jsonify([a.to_dict() for a in c.repo.activities(with_files=True)])

    if c.settings.whatsapp_provider == "cloud":
        app.register_blueprint(create_webhook_blueprint(c.settings, c.messages))
    return app


def start_agent_poller(c: Container, stop: threading.Event) -> threading.Thread | None:
    if c.messenger.name != "agent":
        return None
    from whatsapp_agent import run_poller

    thread = threading.Thread(target=run_poller, args=(c, stop), name="whatsapp-poller", daemon=True)
    thread.start()
    return thread


def main() -> None:
    s = default_settings
    setup_logging(s.log_dir, secrets=[s.agent_api_key, s.cloud_token, s.cloud_app_secret, s.cloud_verify_token, s.api_key])
    log.info("Starting HARRY — Abasyn WhatsApp LMS Intelligence Agent")
    c = build(s)
    stop = threading.Event()

    def shutdown(*_args):
        stop.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    try:
        # WhatsApp first: HARRY must answer even if the browser has a problem.
        if c.messenger.name == "console":
            print("\n⚠️  WHATSAPP_PROVIDER=console: replies are printed here, NOT sent to WhatsApp. "
                  "Set WHATSAPP_PROVIDER=agent in .env to use WhatsApp.")
        elif start_agent_poller(c, stop):
            print("\n📱 WhatsApp: listening for your messages (send 'menu' to your HARRY agent).")
        elif c.messenger.name == "cloud":
            print("\n📱 WhatsApp Cloud API: waiting for webhook calls on /webhook.")

        try:
            c.worker.start()
            print("HARRY opened Moodle in a browser window.")
            print("If Moodle asks you to log in, sign in yourself in that window and keep it open.")
            status = c.lms.session_status()
            if status["signed_in"]:
                print("✅ Moodle session is active.")
            else:
                print("⚠️  Moodle is showing the login page. Sign in in the browser window; HARRY will retry automatically.")
        except Exception as error:  # noqa: BLE001 - keep WhatsApp running; the browser retries on the next job
            log.error("Browser problem at startup: %s", error)
            print(f"⚠️  The Moodle browser could not start yet ({error}). HARRY keeps running and will retry.")
        if s.enable_scheduler:
            c.scheduler.start()
        app = create_app(c)
        print(f"Local API: http://{s.host}:{s.port}  (WhatsApp provider: {c.messenger.name})")
        print("Keep this window open. Press Ctrl+C to stop.\n")
        # threaded=True is safe: every browser action runs on the single browser thread.
        app.run(host=s.host, port=s.port, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\nStopping HARRY…")
    finally:
        stop.set()
        c.scheduler.stop()
        if c.worker:
            c.worker.stop()
        c.db.close()


if __name__ == "__main__":
    sys.exit(main())
