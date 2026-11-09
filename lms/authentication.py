"""Moodle session checks. Login is always done manually by the student in the
opened browser window: this code never handles passwords, MFA or CAPTCHA."""

from __future__ import annotations

import logging

from lms.browser import BrowserSession

log = logging.getLogger(__name__)


class SessionExpired(RuntimeError):
    """The saved Moodle login is no longer valid; the student must sign in again."""


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
