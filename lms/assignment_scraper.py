"""Read one Moodle assignment page: dates, submission status, grade, files, requirements."""

from __future__ import annotations

import logging
import re

from lms.authentication import require_session
from lms.browser import BrowserSession
from lms.file_downloader import discover_files
from lms.parsing import (
    clean_text,
    field_from_lines,
    lookup,
    normalize_assignment_status,
    parse_date,
    parse_labeled_dates,
)

log = logging.getLogger(__name__)


def _description(session: BrowserSession) -> str:
    text = session.text_of(".activity-description, #intro, .box.generalbox.boxaligncenter")
    return clean_text(text, 800)


def scrape_assignment(session: BrowserSession, url: str) -> dict:
    session.open(url)
    require_session(session)
    text = session.main_text()
    table = session.tables()
    dates_text = session.text_of(".activity-dates, [data-region='activity-dates']") or text

    dates = parse_labeled_dates(dates_text)
    # Moodle 3.x shows dates in the status table instead.
    for field, labels in (
        ("opens_at", ("allow submissions from", "opened", "opens")),
        ("due_at", ("due date", "due")),
        ("cutoff_at", ("cut-off date",)),
    ):
        if dates.get(field) is None:
            value = lookup(table, *labels)
            if value:
                dates[field] = parse_date(value)[0]
    if dates.get("due_at") is None:
        fallback = parse_labeled_dates(text)
        for key, value in fallback.items():
            dates[key] = dates.get(key) or value

    status_text = lookup(table, "submission status") or field_from_lines(text, ("submission status",))
    grading_status = lookup(table, "grading status") or field_from_lines(text, ("grading status",))
    time_remaining = lookup(table, "time remaining") or field_from_lines(text, ("time remaining",))
    grade = lookup(table, "grade") or None
    if grade and len(grade) > 120:
        grade = None
    if grade and re.search(r"^\s*-\s*$", grade):
        grade = None

    requirements = []
    description = _description(session)
    if description:
        requirements.append(description)
    for label in ("submission types", "file types", "maximum number of uploaded files", "word limit"):
        value = lookup(table, label)
        if value:
            requirements.append(f"{label.title()}: {value}")

    return {
        **dates,
        "status": normalize_assignment_status(status_text, "" if status_text else text),
        "status_text": status_text,
        "grading_status": grading_status,
        "time_remaining": time_remaining,
        "grade": grade,
        "requirements": "\n".join(requirements)[:1500] or None,
        "files": discover_files(session),
    }
