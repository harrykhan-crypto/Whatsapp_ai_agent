"""Discover every activity in a course and classify it (assignment/quiz/lab/task/...)."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

from lms.authentication import require_session
from lms.browser import BrowserSession
from lms.parsing import (
    canonical_module_url,
    classify_activity,
    module_from_url,
    parse_labeled_dates,
)

log = logging.getLogger(__name__)

# Modules worth tracking. Labels/subsections are skipped.
TRACKED_MODNAMES = {
    "assign", "quiz", "forum", "resource", "folder", "url", "page", "book",
    "workshop", "lesson", "h5pactivity", "lti", "scorm", "choice", "feedback",
    "data", "glossary", "turnitintooltwo", "hvp",
}

_MODNAME_SUFFIXES = re.compile(
    r"\s+(?:Assignment|Quiz|Forum|File|Folder|URL|Page|Book|Workshop|Lesson|H5P|"
    r"External tool|SCORM package|Choice|Feedback|Database|Glossary)$",
    re.I,
)


def clean_activity_name(name: str, context: str = "") -> str:
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or name.lower() in {"view", "file", "link", "more", "open"}:
        name = (context or "").split("\n", 1)[0].strip()
    name = _MODNAME_SUFFIXES.sub("", name).strip()
    return name[:200] or "Unnamed activity"


def _collect_modules(session: BrowserSession, course: dict, found: dict[str, dict]) -> None:
    for link in session.links("#region-main, [role='main'], main, body"):
        modname, cmid = module_from_url(link["url"])
        if not modname or not cmid or modname not in TRACKED_MODNAMES:
            continue
        url = canonical_module_url(session.base_url, modname, cmid)
        name = clean_activity_name(link.get("name", ""), link.get("context", ""))
        existing = found.get(url)
        if existing and existing["name"] != "Unnamed activity":
            continue
        dates = parse_labeled_dates(link.get("context", ""))
        found[url] = {
            "cmid": cmid,
            "modname": modname,
            "url": url,
            "name": name,
            "section": (link.get("section") or "")[:120],
            "course_id": course["id"],
            "course_name": course["name"],
            "category": classify_activity(modname, name, link.get("section", "")),
            "course_page_dates": {k: v for k, v in dates.items() if v},
        }


def read_course_title(session: BrowserSession) -> str:
    """Full course name from the course page (the course list may show only codes).

    Checks the page heading and the browser tab title ("Course: Automata Theory (CB)")
    and prefers whichever looks like a real name rather than a code.
    """
    candidates = []
    heading = session.text_of(".page-header-headings h1, #page-header h1, #region-main h1")
    if heading:
        candidates.append(heading.split("\n", 1)[0])
    try:
        raw = session.page.title() or ""
    except Exception:  # noqa: BLE001
        raw = ""
    raw = re.sub(r"^\s*Course:\s*", "", raw, flags=re.I)
    candidates.append(raw.split(" | ", 1)[0])
    cleaned = [re.sub(r"\s+", " ", c).strip()[:160] for c in candidates]
    cleaned = [c for c in cleaned if c and c.lower() not in {"course", "dashboard", "my courses", "home"}]
    if not cleaned:
        return ""
    return max(cleaned, key=lambda c: (" " in c, len(c)))


def discover_course_activities(session: BrowserSession, course: dict) -> list[dict]:
    """Open a course page and return every tracked activity on it."""
    session.open(course["url"])
    require_session(session)
    course["page_title"] = read_course_title(session)
    found: dict[str, dict] = {}
    _collect_modules(session, course, found)

    # Courses using "one section per page" only list section links on the main page.
    section_links = []
    for link in session.links("#region-main, [role='main'], main, body"):
        parsed = urlparse(link["url"])
        query = parse_qs(parsed.query)
        if "/course/view.php" in parsed.path and query.get("id") == [str(course["id"])] and "section" in query:
            section_links.append(link["url"])
        if "/course/section.php" in parsed.path:
            section_links.append(link["url"])
    if section_links and len(found) < 3:
        for url in sorted(set(section_links))[:40]:
            try:
                session.open(url)
                _collect_modules(session, course, found)
            except Exception as error:  # noqa: BLE001
                log.warning("Could not read section %s: %s", url, error)

    activities = list(found.values())
    log.info("Course %s: %s activities", course["name"], len(activities))
    return activities
