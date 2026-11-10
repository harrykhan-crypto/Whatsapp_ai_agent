"""Automatic Moodle login with the student's saved credentials (mock Moodle + real Chromium)."""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

import save_login
from app import build
from config import Settings, settings as global_settings
from tests.fake_moodle import FakeMoodle
from whatsapp.client import ConsoleMessenger

EXE = os.getenv("TEST_BROWSER_EXECUTABLE") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else "")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("autologin")
    fake = FakeMoodle()
    base = fake.start()
    s = Settings(moodle_url=base, database_path=tmp / "agent.db", download_dir=tmp / "dl", backup_dir=tmp / "bk",
                 log_dir=tmp / "logs", profile_dir=tmp / "profile", headless=True, browser_executable=EXE,
                 whatsapp_provider="console", enable_scheduler=False, page_timeout_ms=15_000,
                 moodle_username="student01", moodle_password="Secret#123", allowed_numbers=[], recipient_number="")
    messenger = ConsoleMessenger()
    c = build(s, messenger=messenger)
    c.worker.start()
    yield c, fake, messenger, base
    c.worker.stop()
    fake.stop()


def allow_new_attempt(c):
    c.lms._last_login_attempt = 0
    c.lms._last_login_ok = 0


def texts(messenger, start):
    return "\n".join(m.get("body", "") for m in messenger.sent[start:])


def test_first_start_logs_in_by_itself(env):
    c, fake, messenger, _ = env
    assert c.lms.has_credentials
    start = len(messenger.sent)
    status = c.lms.session_status()                 # no cookie yet: shows login page -> automatic login
    assert status["signed_in"] and fake.login_attempts == 1
    assert c.lms.sync().ok
    assert "login needed" not in texts(messenger, start).lower()


def test_expired_session_is_renewed_silently(env):
    c, fake, messenger, _ = env
    allow_new_attempt(c)
    fake.session_valid = False                      # Moodle ended the session
    start, attempts = len(messenger.sent), fake.login_attempts
    result = c.lms.sync()
    assert result.ok and fake.login_attempts == attempts + 1
    assert "login" not in texts(messenger, start).lower()


def test_changed_password_tried_once_then_never_again(env):
    c, fake, messenger, _ = env
    allow_new_attempt(c)
    fake.credentials = ("student01", "NewPass!456")   # the student changed the password on the LMS
    fake.session_valid = False
    start, attempts = len(messenger.sent), fake.login_attempts
    assert not c.lms.sync().ok
    assert fake.login_attempts == attempts + 1        # exactly one try: no account lockout
    alert = texts(messenger, start)
    assert "rejected your saved password" in alert and "SAVE_LOGIN.bat" in alert
    assert alert.count("Moodle login needed") == 0    # only the specific message
    allow_new_attempt(c)
    c.lms.sync()
    c.lms.sync()
    assert fake.login_attempts == attempts + 1        # the rejected password is never retried
    assert texts(messenger, start).count("rejected your saved password") == 1

    c.lms.set_credentials("student01", "NewPass!456")  # what SAVE_LOGIN.bat + restart does
    allow_new_attempt(c)
    assert c.lms.sync().ok and fake.login_attempts == attempts + 2


def test_captcha_is_never_bypassed(env):
    c, fake, messenger, _ = env
    allow_new_attempt(c)
    fake.captcha = True
    fake.session_valid = False
    attempts = fake.login_attempts
    for _ in range(4):
        allow_new_attempt(c)
        assert not c.lms.sync().ok
    assert fake.login_attempts == attempts            # the form was never submitted
    assert "Moodle login needed" in texts(messenger, 0)
    fake.captcha = False


def test_save_login_checks_before_saving(env, tmp_path, monkeypatch):
    c, fake, _, base = env
    fake.credentials = ("student01", "Right#1")
    monkeypatch.setattr(global_settings, "moodle_url", base)
    monkeypatch.setattr(global_settings, "browser_executable", EXE)
    env_file = tmp_path / ".env"
    env_file.write_text("WHATSAPP_AGENT_API_KEY=keep-me\n")
    assert save_login.main("student01", "wrong", env_path=env_file, session_out=str(tmp_path / "s.json")) == 1
    assert "MOODLE_PASSWORD" not in env_file.read_text()          # nothing saved
    assert save_login.main("student01", "Right#1", env_path=env_file, session_out=str(tmp_path / "s.json")) == 0
    values = dotenv_values(env_file)
    assert values["MOODLE_USERNAME"] == "student01" and values["MOODLE_PASSWORD"] == "Right#1"
    assert values["WHATSAPP_AGENT_API_KEY"] == "keep-me"
    assert (tmp_path / "s.json").exists()
