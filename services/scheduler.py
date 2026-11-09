"""Background scheduler (Asia/Karachi time): LMS sync, daily summaries, reminders, backups."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time as dtime, timedelta
from typing import Callable

from config import Settings
from database.db import Database
from database.models import Repository
from services.monitoring import LmsService
from services.notification import NotificationService
from timeutil import from_iso, now, today

log = logging.getLogger(__name__)

TICK_SECONDS = 30
REMINDER_EVERY = timedelta(minutes=5)
RETRY_EVERY = timedelta(minutes=10)
DAILY_GRACE = timedelta(hours=4)       # send a missed 8 AM summary if HARRY starts before noon


def _parse_clock(value: str) -> dtime:
    hour, minute = (int(x) for x in value.split(":", 1))
    return dtime(hour, minute)


class Scheduler:
    def __init__(self, settings: Settings, db: Database, repo: Repository, lms: LmsService | None,
                 notifier: NotificationService):
        self.settings = settings
        self.db = db
        self.repo = repo
        self.lms = lms
        self.notifier = notifier
        self.sync_every = timedelta(minutes=max(5, settings.sync_interval_minutes))
        self.morning = _parse_clock(settings.morning_summary_time)
        self.evening = _parse_clock(settings.evening_reminder_time)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run: dict[str, datetime] = {}
        self.jobs: list[tuple[str, Callable[[], None]]] = [
            ("sync", self.job_sync),
            ("reminders", self.job_reminders),
            ("morning", self.job_morning),
            ("evening", self.job_evening),
            ("retry", self.job_retry),
            ("backup", self.job_backup),
        ]

    # ---------------------------------------------------------------- control
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        log.info("Scheduler started (sync every %s, summaries %s / %s %s)", self.sync_every,
                 self.settings.morning_summary_time, self.settings.evening_reminder_time, self.settings.timezone_name)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(TICK_SECONDS)

    def tick(self) -> None:
        for name, job in self.jobs:
            if self._stop.is_set():
                return
            try:
                job()
            except Exception:  # noqa: BLE001 - one failing job must never stop the scheduler
                log.exception("Scheduled job %s failed", name)

    def _due(self, name: str, every: timedelta) -> bool:
        last = self._last_run.get(name)
        if last is None or now() - last >= every:
            self._last_run[name] = now()
            return True
        return False

    # ------------------------------------------------------------------- jobs
    def _sync_due(self) -> bool:
        last = self.repo.last_sync(ok_only=False)
        if not last:
            return True
        started = from_iso(last["started_at"])
        return started is None or now() - started >= self.sync_every

    def job_sync(self) -> None:
        if self.lms is None or not self._sync_due():
            return
        result = self.lms.sync()
        if result.ok and result.baseline:
            self.notifier.initial_sync_done(result.course_names, result.activities)
        if result.ok:
            self.notifier.send_event_digest()

    def job_reminders(self) -> None:
        if self._due("reminders", REMINDER_EVERY):
            self.notifier.send_deadline_reminders()

    def _daily(self, clock: dtime, key: str, send: Callable[[], bool]) -> None:
        current = now()
        target = datetime.combine(today(), clock, tzinfo=current.tzinfo)
        if not (target <= current < target + DAILY_GRACE):
            return
        if self.repo.notification_sent(key):
            return
        # Make sure the summary is built from fresh LMS data.
        if self.lms is not None:
            age = self.lms.data_age_minutes()
            if age is None or age > 20:
                self.lms.sync()
        send()

    def job_morning(self) -> None:
        self._daily(self.morning, f"daily:morning:{today()}", self.notifier.send_morning_summary)

    def job_evening(self) -> None:
        self._daily(self.evening, f"daily:evening:{today()}", self.notifier.send_evening_reminder)

    def job_retry(self) -> None:
        if self._due("retry", RETRY_EVERY):
            self.notifier.retry_failed()

    def job_backup(self) -> None:
        stamp = str(today())
        if self.repo.get_pref("last_backup") == stamp:
            return
        self.db.backup(self.settings.backup_dir, keep=self.settings.backups_to_keep)
        self.repo.set_pref("last_backup", stamp)
