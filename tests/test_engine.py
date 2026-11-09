"""Database, change detection, reports, notifications and scheduler (frozen clock)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import timeutil
from config import Settings
from services.notification import NotificationService
from services.report_generator import ReportGenerator
from services.scheduler import Scheduler
from whatsapp.client import ConsoleMessenger, DeliveryError

TZ = ZoneInfo("Asia/Karachi")
NOW = datetime(2026, 11, 10, 10, 0, tzinfo=TZ)   # Tuesday 10 AM


@pytest.fixture
def clock():
    state = {"now": NOW}
    timeutil.set_now_provider(lambda: state["now"])
    return state


def seed(repo):
    repo.upsert_course({"id": 1, "name": "Automata Theory (CB)", "url": "u1"})
    repo.upsert_course({"id": 2, "name": "Human Computer Interaction (CB)", "url": "u2"})
    rows = [
        dict(url="a1", course_id=1, modname="assign", category="assignment", name="Assignment 2",
             due_at=NOW.replace(hour=23, minute=59), status="not_submitted"),
        dict(url="a2", course_id=2, modname="assign", category="assignment", name="Assignment 3",
             due_at=NOW + timedelta(days=1), status="not_submitted"),
        dict(url="q1", course_id=2, modname="quiz", category="quiz", name="Quiz 2",
             opens_at=NOW + timedelta(days=1), closes_at=NOW + timedelta(days=2), status="not_attempted"),
        dict(url="l1", course_id=1, modname="assign", category="lab", name="Lab Task 4",
             due_at=NOW + timedelta(days=3), status="not_submitted"),
        dict(url="o1", course_id=1, modname="assign", category="assignment", name="Assignment 1",
             due_at=NOW - timedelta(days=2), status="not_submitted"),
        dict(url="d1", course_id=2, modname="assign", category="assignment", name="Assignment 1",
             due_at=NOW - timedelta(days=5), status="submitted", grade="18 / 20"),
    ]
    ids = {}
    for row in rows:
        ids[row["url"]], _ = repo.upsert_activity(row, details_checked=True)
    return ids


def test_change_detection(repo, clock):
    ids = seed(repo)
    _, changes = repo.upsert_activity(dict(url="a2", course_id=2, modname="assign", category="assignment",
                                           name="Assignment 3", due_at=NOW + timedelta(days=2),
                                           status="not_submitted"), details_checked=True)
    assert [c["kind"] for c in changes] == ["deadline_changed"]
    history = repo.db.query("SELECT * FROM deadline_history WHERE activity_id = ?", (ids["a2"],))
    assert len(history) == 1 and history[0]["field"] == "due_at"  # one change -> one history row
    # Unchanged data produces no events.
    _, again = repo.upsert_activity(dict(url="a2", course_id=2, modname="assign", category="assignment",
                                         name="Assignment 3", due_at=NOW + timedelta(days=2),
                                         status="not_submitted"), details_checked=True)
    assert again == []
    # A date missing from one page render does not wipe the stored deadline.
    _, missing = repo.upsert_activity(dict(url="a2", course_id=2, modname="assign", category="assignment",
                                           name="Assignment 3", status="not_submitted"), details_checked=True)
    assert missing == [] and repo.activity(ids["a2"]).due_at == NOW + timedelta(days=2)
    _, graded = repo.upsert_activity(dict(url="a2", course_id=2, modname="assign", category="assignment",
                                          name="Assignment 3", status="submitted", grade="9/10"), details_checked=True)
    assert [c["kind"] for c in graded] == ["grade_posted"]


def test_report_counts_and_sections(repo, clock):
    seed(repo)
    reports = ReportGenerator(repo)
    counts = reports.counts()
    assert counts == {"pending": 4, "upcoming": 4, "overdue": 1, "completed": 1, "total": 6}
    text = reports.full_report()
    assert "🔴 *DUE TODAY*" in text and "Automata Theory — Assignment 2" in text
    assert "⚠️ *OVERDUE / MISSING*" in text and "Automata Theory — Assignment 1" in text
    assert "📝 *QUIZZES*" in text and "🧪 *LAB TASKS*" in text
    assert "Pending: 4" in text and "Overdue: 1" in text and "Completed: 1" in text
    tomorrow = reports.select(period="tomorrow")
    assert {a.name for a in tomorrow} == {"Assignment 3", "Quiz 2"}  # quiz opens tomorrow
    next_week = reports.select(period="next_week")
    assert next_week == []
    pending = reports.select(pending_only=True, categories=["assignment"])
    assert {a.name for a in pending} == {"Assignment 2", "Assignment 3", "Assignment 1"}


def test_notification_dedupe(repo, clock):
    seed(repo)
    messenger = ConsoleMessenger()
    notifier = NotificationService(repo, messenger, ReportGenerator(repo), [24, 3])
    # 10 AM: Assignment 2 (due 23:59, ~14h) and Assignment 3 (due in 24h) are in the 24h window.
    assert notifier.send_deadline_reminders() == 2
    assert notifier.send_deadline_reminders() == 0          # no duplicates
    clock["now"] = NOW.replace(hour=21, minute=30)            # 2.5h before Assignment 2
    assert notifier.send_deadline_reminders() == 1          # the 3h final reminder
    assert "FINAL REMINDER" in messenger.sent[-1]["body"]
    assert notifier.send_deadline_reminders() == 0
    # Changed deadline -> a new reminder is allowed.
    repo.upsert_activity(dict(url="a1", course_id=1, modname="assign", category="assignment", name="Assignment 2",
                              due_at=NOW.replace(hour=23, minute=0), status="not_submitted"), details_checked=True)
    assert notifier.send_deadline_reminders() == 1
    # Submitted work gets no reminder.
    repo.upsert_activity(dict(url="a1", course_id=1, modname="assign", category="assignment", name="Assignment 2",
                              status="submitted"), details_checked=True)
    clock["now"] = NOW.replace(hour=22, minute=30)
    before = len(messenger.sent)
    notifier.send_deadline_reminders()
    assert all("Assignment 2" not in m["body"] for m in messenger.sent[before:])


def test_events_digest_sent_once(repo, clock):
    seed(repo)
    repo.add_event("new_activity", "HCI — Assignment 4", "Deadline: Friday")
    repo.add_event("deadline_changed", "OS — Quiz 2", "Closes: Wed ➜ Thu")
    messenger = ConsoleMessenger()
    notifier = NotificationService(repo, messenger, ReportGenerator(repo))
    assert notifier.send_event_digest() is True
    assert notifier.send_event_digest() is False
    body = messenger.sent[0]["body"]
    assert "NEW ON LMS" in body and "DEADLINE CHANGED" in body


class FailingMessenger(ConsoleMessenger):
    def __init__(self):
        super().__init__()
        self.fail = True

    def _send_text(self, text, to):
        if self.fail:
            raise ConnectionError("network down")
        return super()._send_text(text, to)


def test_failed_delivery_is_retried_later(repo, clock):
    seed(repo)
    messenger = FailingMessenger()
    notifier = NotificationService(repo, messenger, ReportGenerator(repo))
    assert notifier.send_morning_summary() is False
    assert repo.notification_history(1)[0]["status"] == "failed"
    messenger.fail = False
    assert notifier.retry_failed() == 1
    assert repo.notification_sent(f"daily:morning:{NOW.date()}")
    assert notifier.send_morning_summary() is False   # already sent today


def test_mute(repo, clock):
    seed(repo)
    messenger = ConsoleMessenger()
    notifier = NotificationService(repo, messenger, ReportGenerator(repo))
    repo.set_pref("notifications", "off")
    assert notifier.send_deadline_reminders() == 0
    notifier.session_expired()                     # critical alerts ignore mute
    assert "login needed" in messenger.sent[-1]["body"]


def test_scheduler_daily_jobs(tmp_path, repo, clock):
    seed(repo)
    settings = Settings(backup_dir=tmp_path / "b", morning_summary_time="08:00", evening_reminder_time="20:00")
    messenger = ConsoleMessenger()
    notifier = NotificationService(repo, messenger, ReportGenerator(repo))
    scheduler = Scheduler(settings, repo.db, repo, None, notifier)
    clock["now"] = NOW.replace(hour=7, minute=59)
    scheduler.job_morning()
    assert not messenger.sent
    clock["now"] = NOW.replace(hour=8, minute=0)
    scheduler.job_morning()
    scheduler.job_morning()
    assert len(messenger.sent) == 1 and "GOOD MORNING" in messenger.sent[0]["body"]
    clock["now"] = NOW.replace(hour=20, minute=5)
    scheduler.job_evening()
    assert "TOMORROW'S DEADLINES" in messenger.sent[-1]["body"] and "Assignment 3" in messenger.sent[-1]["body"]
    scheduler.job_backup()
    scheduler.job_backup()
    assert len(list((tmp_path / "b").glob("agent-*.db"))) == 1
    scheduler.tick()   # full tick must not raise without a browser


def test_console_split_and_errors():
    m = ConsoleMessenger()
    m.send_text("x" * 9000)
    assert len(m.sent) == 3 and "(1/3)" in m.sent[0]["body"]
    with pytest.raises(DeliveryError):
        m.send_document("does-not-exist.pdf")
