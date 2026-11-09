"""Sends automatic WhatsApp notifications exactly once (deduplicated in SQLite)."""

from __future__ import annotations

import logging

from database.models import Repository
from services.report_generator import ReportGenerator
from timeutil import now, to_iso, today
from whatsapp.client import DeliveryError, Messenger

log = logging.getLogger(__name__)

SERVER_LOGIN_ALERT = (
    "🔐 *HARRY: Moodle login needed*\n\n"
    "Your Abasyn LMS session has expired, so I can't check your courses right now.\n"
    "On your laptop, double-click *LOGIN_AND_SEND.bat*, sign in to Moodle in the window that opens, "
    "and I'll continue automatically on the server (about 2 minutes)."
)

SESSION_EXPIRED_ALERT = (
    "🔐 *HARRY: Moodle login needed*\n\n"
    "Your Abasyn LMS session has expired, so I can't check your courses right now.\n"
    "Please sign in to Moodle in the HARRY browser window on your computer "
    "(I never ask for or store your password). I'll resume automatically once you're signed in."
)


class NotificationService:
    def __init__(self, repo: Repository, messenger: Messenger, reports: ReportGenerator,
                 reminder_hours: list[int] | None = None, server_mode: bool = False):
        self.repo = repo
        self.messenger = messenger
        self.reports = reports
        self.reminder_hours = sorted(reminder_hours or [24, 3])
        self.server_mode = server_mode

    @property
    def enabled(self) -> bool:
        return self.repo.get_pref("notifications", "on") == "on"

    def send(self, key: str, kind: str, body: str, *, respect_mute: bool = True) -> bool:
        """Send ``body`` once for ``key``. Returns True if it was sent now."""
        if respect_mute and not self.enabled:
            return False
        if not self.repo.claim_notification(key, kind, body):
            return False
        try:
            self.messenger.send_text(body)
        except (DeliveryError, Exception) as error:  # noqa: BLE001
            log.warning("Notification %s failed: %s", key, error)
            self.repo.finish_notification(key, False, str(error))
            return False
        self.repo.finish_notification(key, True)
        log.info("Notification sent: %s", key)
        return True

    # ------------------------------------------------------------------ kinds
    def send_event_digest(self) -> bool:
        events = self.repo.pending_events()
        if not events:
            return False
        if not self.enabled:
            self.repo.mark_events_notified(e["id"] for e in events)
            return False
        body = self.reports.events_digest(events)
        key = f"events:{events[0]['id']}-{events[-1]['id']}"
        sent = self.send(key, "lms_update", body)
        if sent or self.repo.notification_sent(key):
            self.repo.mark_events_notified(e["id"] for e in events)
        return sent

    def send_deadline_reminders(self) -> int:
        sent = 0
        current = now()
        for activity in self.repo.activities(with_files=True):
            deadline = activity.deadline
            if not deadline or activity.done or deadline <= current:
                continue
            hours_left = (deadline - current).total_seconds() / 3600
            window = next((h for h in self.reminder_hours if hours_left <= h), None)
            if window is None:
                continue
            key = f"reminder:{window}h:{activity.id}:{to_iso(deadline)}"
            if self.send(key, f"reminder_{window}h", self.reports.reminder(activity, max(1, round(hours_left)))):
                sent += 1
        return sent

    def send_morning_summary(self) -> bool:
        return self.send(f"daily:morning:{today()}", "morning_summary", self.reports.morning_summary())

    def send_evening_reminder(self) -> bool:
        return self.send(f"daily:evening:{today()}", "evening_reminder", self.reports.evening_reminder())

    def session_expired(self) -> None:
        stamp = to_iso(now())
        self.repo.set_pref("session_expired_at", stamp)
        alert = SERVER_LOGIN_ALERT if self.server_mode else SESSION_EXPIRED_ALERT
        self.send(f"session_expired:{stamp}", "session_expired", alert, respect_mute=False)

    def session_restored(self) -> None:
        stamp = self.repo.get_pref("session_expired_at") or to_iso(now())
        self.send(f"session_restored:{stamp}", "session_restored",
                  "✅ Moodle login restored — HARRY is monitoring your LMS again.", respect_mute=False)

    def initial_sync_done(self, courses: list[str], activities: int) -> None:
        names = "\n".join(f"• {n}" for n in courses)
        self.send("initial_sync", "initial_sync",
                  f"✅ *HARRY is connected to your LMS*\n\nFound {len(courses)} courses and {activities} activities:\n{names}\n\n"
                  "From now on I'll tell you about new work, changed deadlines, new files and announcements. Send /menu anytime.",
                  respect_mute=False)

    def retry_failed(self) -> int:
        resent = 0
        for row in self.repo.failed_notifications():
            if self.send(row["dedupe_key"], row["kind"], row["body"] or "", respect_mute=row["kind"] != "session_expired"):
                resent += 1
        return resent
