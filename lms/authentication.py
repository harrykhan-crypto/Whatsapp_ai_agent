"""Moodle session checks and (optional) automatic login with the student's own credentials.

Automatic login only fills in the normal Moodle username/password form, exactly as
the student would. It never tries to get past a CAPTCHA, a verification code (MFA) or
a single-sign-on page: in those cases it stops and asks the student to sign in.
"""

from __future__ import annotations

import logging

from playwright.sync_api import Error as PlaywrightError

from lms.browser import BrowserSession

log = logging.getLogger(__name__)


class SessionExpired(RuntimeError):
    """The saved Moodle login is no longer valid; the student must sign in again."""


class LoginRejected(RuntimeError):
    """Moodle said the saved username/password is wrong (e.g. the password was changed)."""


class ManualLoginRequired(RuntimeError):
    """The login page needs a human (CAPTCHA, verification code, unknown/SSO form)."""


_CHALLENGE_SELECTORS = (
    "iframe[src*='recaptcha'], .g-recaptcha, iframe[src*='hcaptcha'], .h-captcha, "
    "input[name*='otp' i], input[name*='totp' i], input[autocomplete='one-time-code']"
)
_ERROR_SELECTORS = "#loginerrormessage, .loginerrors, .alert-danger, .error, [role='alert']"


def check_session(session: BrowserSession) -> dict:
    """Open the dashboard and report whether we are signed in (no secrets returned)."""
    session.open(f"{session.base_url}/my/")
    signed_in = not session.is_login_page()
    user = ""
    if signed_in:
        user = session.text_of(".usertext, .usermenu .login, #user-menu-toggle .userinitials")[:80]
    return {"signed_in": signed_in, "url": session.url.split("?", 1)[0], "user": user}


def require_session(session: BrowserSession) -> None:
    if session.is_login_page():
        raise SessionExpired(
            "Your Moodle session has expired. Sign in again in the browser window opened by HARRY."
        )


def _has_challenge(session: BrowserSession) -> bool:
    try:
        return session.page.locator(_CHALLENGE_SELECTORS).count() > 0
    except PlaywrightError:
        return False


def auto_login(session: BrowserSession, username: str, password: str) -> None:
    """Sign in through Moodle's normal login form. Raises LoginRejected / ManualLoginRequired."""
    session.open(f"{session.base_url}/login/index.php")
    if not session.is_login_page():
        return  # already signed in
    if _has_challenge(session):
        raise ManualLoginRequired("The Moodle login page shows a CAPTCHA or verification step.")
    page = session.page
    user_box = page.locator("input[name='username'], #username").first
    pass_box = page.locator("input[name='password'], #password").first
    if not user_box.count() or not pass_box.count():
        raise ManualLoginRequired("The Moodle login form was not recognised (single sign-on?).")
    user_box.fill(username)
    pass_box.fill(password)
    button = page.locator("#loginbtn, button[type='submit'], input[type='submit']").first
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=session.timeout_ms):
            if button.count():
                button.click()
            else:
                pass_box.press("Enter")
    except PlaywrightError as error:
        log.warning("Login navigation did not finish cleanly: %s", error)
    page.wait_for_timeout(300)

    if not session.is_login_page():
        log.info("Signed in to Moodle automatically")
        return
    if _has_challenge(session):
        raise ManualLoginRequired("Moodle asked for a CAPTCHA or verification step after the password.")
    message = ""
    try:
        errors = page.locator(_ERROR_SELECTORS)
        if errors.count():
            message = " ".join(errors.all_inner_texts()).strip()[:200]
    except PlaywrightError:
        pass
    raise LoginRejected(message or "Moodle did not accept the saved username/password.")
