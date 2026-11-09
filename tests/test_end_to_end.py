"""End-to-end: real Chromium + the real scrapers against the Moodle look-alike,
then WhatsApp commands, file delivery, change notifications, session expiry and
browser-crash recovery. WhatsApp output goes to ConsoleMessenger."""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import build, create_app
from tests.fake_moodle import SESSION_COOKIE, TZ, VALID_SESSION, FakeMoodle
from whatsapp.client import ConsoleMessenger

EXECUTABLE = os.getenv("TEST_BROWSER_EXECUTABLE") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else "")


@pytest.fixture(scope="module")
def system(tmp_path_factory):
    from config import Settings

    tmp = tmp_path_factory.mktemp("e2e")
    fake = FakeMoodle()
    base = fake.start()
    settings = Settings(
        moodle_url=base, database_path=tmp / "agent.db", download_dir=tmp / "downloads", backup_dir=tmp / "backups",
        log_dir=tmp / "logs", profile_dir=tmp / "profile", headless=True, browser_executable=EXECUTABLE,
        whatsapp_provider="console", enable_scheduler=False, page_timeout_ms=15_000, student_name="Harry",
        allowed_numbers=[], recipient_number="",
    )
    messenger = ConsoleMessenger()
    c = build(settings, messenger=messenger)
    c.worker.start()
    yield c, fake, messenger
    c.worker.stop()
    fake.stop()


def login(c, fake):
    c.worker.call(lambda s: s.context.add_cookies([{
        "name": SESSION_COOKIE, "value": VALID_SESSION, "domain": "127.0.0.1", "path": "/"}]))


def bodies(messenger, start=0):
    return "\n".join(m.get("body", m.get("filename", "")) for m in messenger.sent[start:])


def test_01_login_detection_and_initial_sync(system):
    c, fake, messenger = system
    assert c.lms.session_status()["signed_in"] is False      # no cookie yet -> login page
    login(c, fake)
    status = c.lms.session_status()
    assert status["signed_in"] is True and "Test Student" in status["user"]

    result = c.lms.sync()
    assert result.ok and result.baseline and result.courses == 6
    names = {course["name"] for course in c.repo.courses()}
    assert names == set(fake.courses.values())
    assert set(c.settings.expected_courses) <= names        # the 6 courses from the brief


def test_02_activities_dates_status_files(system):
    c, fake, _ = system
    by_name = {(a.course_id, a.name): a for a in c.repo.activities(categories=None, with_files=True)}
    hci = by_name[(103, "Assignment 2")]
    assert hci.category == "assignment" and hci.status == "not_submitted"
    assert hci.due_at == fake.activities[3001]["due"]
    assert sorted(f["filename"] for f in hci.files) == ["HCI_Assignment_2.pdf", "HCI_Template.docx"]  # own submission excluded
    assert "upload a PDF" in (hci.requirements or "")
    assert by_name[(103, "Assignment 1")].status == "submitted" and by_name[(103, "Assignment 1")].grade == "18.00 / 20.00"
    assert by_name[(102, "Lab Task 3")].category == "lab"
    assert by_name[(104, "Lab Report 4")].category == "lab" and by_name[(104, "Lab Report 4")].overdue
    assert by_name[(104, "Weekly Task 5")].category == "task" and by_name[(104, "Weekly Task 5")].status == "draft"
    quiz = by_name[(102, "Quiz 2")]
    assert quiz.category == "quiz" and quiz.status == "not_attempted"
    assert quiz.opens_at == fake.activities[2001]["opens"] and quiz.closes_at == fake.activities[2001]["close"]
    assert quiz.attempts_allowed == "1" and quiz.time_limit == "20 mins"
    done_quiz = by_name[(101, "Quiz 1")]
    assert done_quiz.status == "attempted" and done_quiz.grade == "8.00 / 10.00"
    slides = by_name[(105, "Lecture 3 Slides")]
    assert [f["filename"] for f in slides.files] == ["SQE_Lecture_3.pptx"]
    assert c.repo.announcements()[0]["subject"] == "Midterm schedule"
    assert "Room 12" in c.repo.announcements()[0]["excerpt"]


def test_03_whatsapp_questions(system):
    c, fake, messenger = system
    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "Assalam o Alaikum, kal university ka kya kaam hai?", "m1")
    reply = bodies(messenger, start)
    assert "Wa Alaikum Assalam Harry" in reply
    assert "Human Computer Interaction — Assignment 2" in reply and "Operating System Concepts — Quiz 2" in reply
    assert "Automata" not in reply   # due today, not tomorrow

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "/today", "m2")
    assert "Automata Theory — Assignment 2" in bodies(messenger, start)

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "Show overdue assignments", "m3")
    assert "Lab Report 4" in bodies(messenger, start)

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "/report", "m4")
    report = bodies(messenger, start)
    assert "ABASYN UNIVERSITY" in report and "OVERALL SUMMARY" in report and "Overdue: 1" in report

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "/report", "m4")   # same message id redelivered
    assert len(messenger.sent) == start

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "Next week ke quizzes batao", "m5")
    c.messages.handle_inbound("user:1", "/courses", "m6")
    c.messages.handle_inbound("user:1", "/announcements", "m7")
    out = bodies(messenger, start)
    assert "Introduction to Psychology" in out and "Midterm schedule" in out


def documents(messenger, start):
    return [m for m in messenger.sent[start:] if m["type"] == "document"]


def test_04_file_delivery(system):
    c, fake, messenger = system
    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "Send me HCI Assignment 2.", "f1")
    docs = documents(messenger, start)
    assert sorted(d["filename"] for d in docs) == ["HCI_Assignment_2.pdf", "HCI_Template.docx"]
    assert "Found *Human Computer Interaction — Assignment 2*" in bodies(messenger, start)
    for d in docs:
        data = Path(d["path"]).read_bytes()
        assert data.startswith(b"%PDF-1.4 fake content of " + d["filename"].encode())
    assert docs[0]["mime_type"] in {"application/pdf",
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "Send me all pending assignment files.", "f2")
    names = sorted(d["filename"] for d in documents(messenger, start))
    assert names == ["Automata_Assignment_2.pdf", "HCI_Assignment_2.pdf", "HCI_Template.docx", "Psychology_Assignment_1.pdf"]

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "Psychology ki file download kar ke bhejo.", "f3")
    assert [d["filename"] for d in documents(messenger, start)] == ["Psychology_Assignment_1.pdf"]

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "Automata ka assignment bhejo", "f4")
    assert [d["filename"] for d in documents(messenger, start)] == ["Automata_Assignment_2.pdf"]

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "SQE lecture 3 slides chahiye", "f5")
    assert [d["filename"] for d in documents(messenger, start)] == ["SQE_Lecture_3.pptx"]

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "/files", "f6")
    listing = bodies(messenger, start)
    zip_id = next(f["id"] for f in c.repo.all_files() if f["filename"] == "SCD_Lab_4.zip")
    assert f"F{zip_id} · SCD_Lab_4.zip" in listing
    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", f"/download F{zip_id}", "f7")
    assert [d["filename"] for d in documents(messenger, start)] == ["SCD_Lab_4.zip"]

    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "Send me HCI assignment 9", "f8")
    assert not documents(messenger, start) and "couldn't find" in bodies(messenger, start)


def test_05_change_detection_and_notifications(system):
    c, fake, messenger = system
    now = datetime.now(TZ).replace(second=0, microsecond=0)
    fake.activities[2002]["due"] = fake.activities[2002]["due"] + timedelta(days=2)       # deadline moved
    fake.activities[3003] = dict(course=103, mod="assign", name="Assignment 3", opens=now,
                                 due=now + timedelta(days=6), status="No attempt", files=["HCI_Assignment_3.pdf"])
    fake.activities[6001]["files"].append("Psychology_Rubric.pdf")                        # new file
    fake.discussions[9002] = dict(forum=6002, subject="Class cancelled", body="No class on Friday.", posted=now)

    result = c.lms.sync(force_details=True)
    assert result.ok and not result.baseline
    start = len(messenger.sent)
    assert c.notifier.send_event_digest() is True
    digest = bodies(messenger, start)
    assert "NEW ON LMS" in digest and "Human Computer Interaction — Assignment 3" in digest
    assert "DEADLINE CHANGED" in digest and "Lab Task 3" in digest
    assert "NEW FILES UPLOADED" in digest and "Psychology_Rubric.pdf" in digest
    assert "NEW ANNOUNCEMENTS" in digest and "Class cancelled" in digest

    # Nothing changed -> nothing new is sent.
    c.lms.sync(force_details=True)
    start = len(messenger.sent)
    assert c.notifier.send_event_digest() is False
    assert len(messenger.sent) == start


def test_06_session_expiry_and_restore(system):
    c, fake, messenger = system
    fake.session_valid = False
    start = len(messenger.sent)
    result = c.lms.sync()
    assert result.session_expired and not result.ok
    assert "Moodle login needed" in bodies(messenger, start)
    c.lms.sync()                                      # still expired: no second alert
    assert bodies(messenger, start).count("Moodle login needed") == 1
    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "/today", "s1")
    out = bodies(messenger, start)
    assert "login has expired" in out and "Automata Theory — Assignment 2" in out   # instant answer from saved data
    deadline = time.time() + 60
    while c.lms.syncing and time.time() < deadline:   # background re-check started by the message
        time.sleep(0.2)
    fake.session_valid = True
    start = len(messenger.sent)
    assert c.lms.sync().ok
    assert "login restored" in bodies(messenger, start)


def test_07_browser_crash_recovery(system):
    c, fake, _ = system
    restarts = c.worker.restarts
    c.worker.call(lambda s: s.context.close())          # simulate Chromium crashing / being closed
    status = c.lms.session_status()                    # next job transparently restarts the browser
    assert status["signed_in"] is True                 # the persistent profile kept the login
    assert c.worker.restarts == restarts + 1


def test_08_http_api(system):
    c, fake, _ = system
    client = create_app(c).test_client()
    health = client.get("/health").get_json()
    assert health["ok"] and health["browser_started"] and health["courses"] == 6
    answer = client.post("/ask", json={"question": "kal kya submit karna hai"}).get_json()
    assert "Assignment 2" in answer["answer"]
    files = client.post("/files/search", json={"query": "HCI assignment 2"}).get_json()
    assert {f["filename"] for f in files["files"]} == {"HCI_Assignment_2.pdf", "HCI_Template.docx"}
    # Requests arriving through a tunnel are refused without the API key.
    assert client.post("/ask", json={"question": "x"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403


def test_09_full_course_names_survive_resync(system):
    """Abasyn's course list shows codes (FL-26-CS310-CB); full names come from course pages and must stick."""
    c, fake, _ = system
    assert c.lms.sync().ok
    rows = {r["id"]: r for r in c.repo.courses()}
    assert rows[101]["name"] == "Automata Theory (CB)" and rows[101]["short_name"] == "FL-26-CS401-CB"
    assert {r["name"] for r in rows.values()} == set(fake.courses.values())


def test_10_fast_answers_and_files_while_syncing(system):
    """Replies must not wait for a running LMS sync, and vague file requests still send real files."""
    import threading

    c, fake, messenger = system
    # Assignment/lab attachments were downloaded during sync, ahead of any request.
    pre = {f["filename"]: f for f in c.repo.all_files() if f.get("category") in ("assignment", "lab")}
    assert pre["HCI_Assignment_2.pdf"]["local_path"] and Path(pre["HCI_Assignment_2.pdf"]["local_path"]).exists()

    sync_thread = threading.Thread(target=lambda: c.lms.sync(force_details=True))
    sync_thread.start()
    deadline = time.time() + 10
    while not c.lms.syncing and time.time() < deadline:
        time.sleep(0.05)
    assert c.lms.syncing

    start, t0 = len(messenger.sent), time.time()
    c.messages.handle_inbound("user:1", "show me assignments upcoming", "p1")
    assert time.time() - t0 < 5, "answer waited for the sync"
    answer = bodies(messenger, start)
    assert "ASSIGNMENTS" in answer and "Human Computer Interaction — Assignment 2" in answer
    assert "Lab Report 4" not in answer          # overdue lab is not an upcoming assignment

    start, t0 = len(messenger.sent), time.time()
    c.messages.handle_inbound("user:1", "give me the file", "p2")
    docs = documents(messenger, start)
    assert docs, bodies(messenger, start)
    assert time.time() - t0 < 30
    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "assignment ki file bhejo", "p3")
    assert documents(messenger, start)
    start = len(messenger.sent)
    c.messages.handle_inbound("user:1", "HCI file", "p4")
    sent = {d["filename"] for d in documents(messenger, start)}
    assert {"HCI_Assignment_2.pdf", "HCI_Template.docx"} <= sent and all(n.startswith("HCI_") for n in sent)
    sync_thread.join(timeout=300)
