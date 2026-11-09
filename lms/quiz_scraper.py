"""Read one Moodle quiz page: open/close dates, attempts, time limit, grade, state."""

from __future__ import annotations

import logging
import re

from lms.authentication import require_session
from lms.browser import BrowserSession
from lms.file_downloader import discover_files
from lms.parsing import clean_text, field_from_lines, normalize_quiz_status, parse_labeled_dates

log = logging.getLogger(__name__)


def scrape_quiz(session: BrowserSession, url: str) -> dict:
    session.open(url)
    require_session(session)
    text = session.main_text()
    dates_text = session.text_of(".activity-dates, [data-region='activity-dates'], .quizinfo") or text
    dates = parse_labeled_dates(dates_text)
    if not any(dates.values()):
        dates = parse_labeled_dates(text)

    grade = None
    for pattern in (
        r"your final grade for this quiz is\s*:?\s*(\d+(?:\.\d+)?\s*(?:/|out of)\s*\d+(?:\.\d+)?)",
        r"(?:highest|final) grade\s*:\s*(\d+(?:\.\d+)?\s*(?:/|out of)\s*\d+(?:\.\d+)?)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            grade = re.sub(r"\s+", " ", match.group(1)).strip()
            break

    description = clean_text(session.text_of(".activity-description, #intro"), 600)
    return {
        **dates,
        "status": normalize_quiz_status(text),
        "status_text": None,
        "attempts_allowed": field_from_lines(text, ("attempts allowed", "attempts permitted")),
        "time_limit": field_from_lines(text, ("time limit",)),
        "grade": grade,
        "requirements": description or None,
        "files": discover_files(session),
    }


def scrape_generic(session: BrowserSession, url: str) -> dict:
    """Workshops, lessons, H5P, external tools, etc.: dates + attached files."""
    session.open(url)
    require_session(session)
    text = session.main_text()
    dates_text = session.text_of(".activity-dates, [data-region='activity-dates']") or text
    dates = parse_labeled_dates(dates_text)
    description = clean_text(session.text_of(".activity-description, #intro"), 600)
    return {
        **dates,
        "status": "unknown",
        "requirements": description or None,
        "files": discover_files(session),
    }
