"""Regression: pages must keep loading while HARRY's browser thread is idle.

When the student logs in by hand (or Moodle redirects after a login), the page
navigates while no HARRY job is running. Nothing may depend on Python handling
requests in that moment, or the page hangs forever ("still not logged in").
"""

import os
import time
from pathlib import Path

from lms.browser import BrowserWorker
from tests.fake_moodle import SESSION_COOKIE, VALID_SESSION, FakeMoodle

EXE = os.getenv("TEST_BROWSER_EXECUTABLE") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else "")


def test_page_navigates_while_worker_is_idle(tmp_path):
    fake = FakeMoodle()
    base = fake.start()
    worker = BrowserWorker(tmp_path / "profile", base, headless=True, executable_path=EXE)
    try:
        worker.start()
        worker.call(lambda s: s.context.add_cookies([{"name": SESSION_COOKIE, "value": VALID_SESSION,
                                                      "domain": "127.0.0.1", "path": "/"}]))
        worker.call(lambda s: s.open(f"{base}/my/courses.php"))
        # Like a person clicking a link: the page navigates on its own, then HARRY stays idle.
        worker.call(lambda s: s.page.evaluate("setTimeout(() => { location.href = '/course/view.php?id=103'; }, 200)"))
        started = time.time()
        time.sleep(4)
        checked = time.time()
        # The browser itself must have fetched the new page while HARRY was idle.
        fetched = [t for t, path in fake.request_log if path.startswith("/course/view.php?id=103") and started < t < checked]
        assert fetched, "the page could not load while HARRY was idle"
    finally:
        worker.stop()
        fake.stop()
