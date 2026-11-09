"""Sign in to Moodle on this PC and save the login for the HARRY server.

    python login_helper.py                      # writes storage/moodle_session.json

Used by LOGIN_AND_SEND.bat. You type your password yourself in the browser
window; it is never read or stored by HARRY. Only the resulting Moodle login
cookies (for the Moodle site only) are saved, so the server can continue.
Close HARRY on this PC first (only one program can use the browser profile).
"""

from __future__ import annotations

import json
import os
import sys
import time

from config import resolve_path, settings
from lms.browser import BrowserWorker
from logging_setup import force_utf8_console

WAIT_SECONDS = 600


def main(out_path: str | None = None, *, headless: bool = False, wait_seconds: int = WAIT_SECONDS) -> int:
    force_utf8_console()
    out = resolve_path(out_path or (sys.argv[1] if len(sys.argv) > 1 else "storage/moodle_session.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    worker = BrowserWorker(settings.profile_dir, settings.moodle_url, headless=headless,
                           timeout_ms=settings.page_timeout_ms, executable_path=settings.browser_executable,
                           channel=settings.browser_channel)
    try:
        worker.start()
    except Exception as error:  # noqa: BLE001
        print(f"Could not open the browser: {error}")
        print("Is HARRY still running on this PC? Close it and try again.")
        return 1
    try:
        def signed_in(session) -> bool:
            if "/login/" not in session.url.lower() and not session.is_login_page():
                return True
            return False

        worker.call(lambda s: s.open(f"{settings.moodle_url}/my/"))
        if not worker.call(signed_in):
            print("\nPlease sign in to Moodle in the browser window that just opened.")
            print("Waiting up to 10 minutes...")
        deadline = time.time() + wait_seconds
        while not worker.call(signed_in):
            if time.time() > deadline:
                print("Timed out waiting for the Moodle login.")
                return 1
            time.sleep(3)
        # Load the dashboard once more so Moodle issues a fresh session cookie.
        worker.call(lambda s: s.open(f"{settings.moodle_url}/my/"))
        cookies = worker.call(lambda s: s.context.cookies([settings.moodle_url]))
        if not cookies:
            print("No Moodle login cookies were found.")
            return 1
        temp = out.with_suffix(".tmp")
        temp.write_text(json.dumps(cookies), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(out)
        print(f"\n✅ Signed in. Login saved to {out}")
        return 0
    finally:
        worker.stop()


if __name__ == "__main__":
    sys.exit(main())
