"""Save your Moodle username/password so HARRY can log in again by itself.

    python save_login.py          (or double-click SAVE_LOGIN.bat)

The password is typed hidden, tested once on the real Moodle login page, and saved
only if Moodle accepts it, in .env on this PC (and on your HARRY server via
SAVE_LOGIN.bat). Run it again whenever you change your LMS password.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from getpass import getpass
from pathlib import Path

from dotenv import set_key

from config import ROOT, resolve_path, settings
from lms.authentication import LoginRejected, ManualLoginRequired, auto_login
from lms.browser import BrowserWorker
from logging_setup import force_utf8_console


def test_login(username: str, password: str, *, headless: bool = True) -> list[dict]:
    """Log in with a fresh, temporary browser profile. Returns the Moodle cookies on success."""
    temp = Path(tempfile.mkdtemp(prefix="harry-login-"))
    worker = BrowserWorker(temp / "profile", settings.moodle_url, headless=headless,
                           timeout_ms=settings.page_timeout_ms, executable_path=settings.browser_executable,
                           channel=settings.browser_channel, cookie_file=temp / "none.json")
    try:
        worker.start()
        worker.call(lambda s: auto_login(s, username, password), timeout=180)
        return worker.call(lambda s: s.context.cookies([settings.moodle_url]))
    finally:
        worker.stop()
        shutil.rmtree(temp, ignore_errors=True)


def main(username: str | None = None, password: str | None = None, *, env_path: Path | None = None,
         session_out: str = "storage/moodle_session.json", headless: bool = True) -> int:
    force_utf8_console()
    env_path = env_path or ROOT / ".env"
    print("Save your Moodle login for HARRY (used only to sign in to the LMS).\n")
    if username is None:
        current = settings.moodle_username
        typed = input(f"Moodle username{f' [{current}]' if current else ''}: ").strip()
        username = typed or current
    if password is None:
        password = getpass("Moodle password (hidden while typing): ")
    if not username or not password:
        print("Username and password are both needed.")
        return 1

    print("\nChecking the login on Moodle (about 20 seconds)...")
    try:
        cookies = test_login(username, password, headless=headless)
    except LoginRejected as error:
        print(f"\n❌ Moodle did not accept this username/password: {error}\nNothing was saved.")
        return 1
    except ManualLoginRequired as error:
        print(f"\n⚠️  {error}\nAutomatic login can't work on this LMS, so nothing was saved.\n"
              "Use LOGIN_AND_SEND.bat (or sign in in HARRY's browser window) instead.")
        return 1
    except Exception as error:  # noqa: BLE001 - e.g. no internet
        print(f"\n⚠️  Could not reach Moodle to check the login: {error}")
        if input("Save it anyway? (y/n): ").strip().lower() != "y":
            return 1
        cookies = []

    env_path.touch(exist_ok=True)
    set_key(str(env_path), "MOODLE_USERNAME", username)
    set_key(str(env_path), "MOODLE_PASSWORD", password)
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    if cookies:
        out = resolve_path(session_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cookies), encoding="utf-8")
    print("\n✅ Login checked and saved. HARRY will now log in again by itself whenever Moodle asks.")
    print("Restart HARRY so it picks up the saved login (SAVE_LOGIN.bat does this on your server).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
