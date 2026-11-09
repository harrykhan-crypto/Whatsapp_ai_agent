"""login_helper saves the Moodle login for the server; a server restores it without a manual login."""

import json
import os
from pathlib import Path

import login_helper
from config import settings
from lms.browser import BrowserWorker
from tests.fake_moodle import SESSION_COOKIE, VALID_SESSION, FakeMoodle

EXE = os.getenv("TEST_BROWSER_EXECUTABLE") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else "")


def test_login_file_moves_session_to_a_fresh_server_profile(tmp_path, monkeypatch):
    fake = FakeMoodle()
    base = fake.start()
    try:
        monkeypatch.setattr(settings, "moodle_url", base)
        monkeypatch.setattr(settings, "profile_dir", tmp_path / "laptop_profile")
        monkeypatch.setattr(settings, "browser_executable", EXE)
        # The "laptop" profile is already signed in (as after a manual login).
        laptop_cookie = tmp_path / ".laptop_profile_session.json"
        laptop_cookie.write_text(json.dumps([{"name": SESSION_COOKIE, "value": VALID_SESSION, "domain": "127.0.0.1",
                                              "path": "/", "expires": -1}]))
        out = tmp_path / "moodle_session.json"
        assert login_helper.main(str(out), headless=True, wait_seconds=5) == 0
        saved = json.loads(out.read_text())
        assert any(c["name"] == SESSION_COOKIE for c in saved)

        # A brand-new server profile becomes signed in from that file alone.
        server = BrowserWorker(tmp_path / "server_profile", base, headless=True, executable_path=EXE, cookie_file=out)
        server.start()
        try:
            server.call(lambda s: s.open(f"{base}/my/courses.php"))
            assert server.call(lambda s: not s.is_login_page())
        finally:
            server.stop()

        # Not signed in -> helper gives up cleanly after waiting.
        laptop_cookie.unlink()
        (tmp_path / "laptop_profile").rename(tmp_path / "old_profile")
        assert login_helper.main(str(tmp_path / "x.json"), headless=True, wait_seconds=2) == 1
    finally:
        fake.stop()


def test_server_mode_login_instructions(repo):
    from services.command_handler import CommandHandler
    from services.notification import NotificationService
    from services.report_generator import ReportGenerator
    from whatsapp.client import ConsoleMessenger

    messenger = ConsoleMessenger()
    NotificationService(repo, messenger, ReportGenerator(repo), server_mode=True).session_expired()
    assert "LOGIN_AND_SEND.bat" in messenger.sent[-1]["body"]
    assert "LOGIN_AND_SEND.bat" in CommandHandler(repo, None, ReportGenerator(repo), server_mode=True)._login_text()
    assert "LOGIN_AND_SEND" not in CommandHandler(repo, None, ReportGenerator(repo))._login_text()
