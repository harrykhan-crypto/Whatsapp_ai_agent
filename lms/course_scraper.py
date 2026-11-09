"""Discover enrolled courses from the Moodle dashboard / My courses page."""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from playwright.sync_api import Error as PlaywrightError

from lms.authentication import require_session
from lms.browser import BrowserSession
from lms.parsing import course_id_from_url

log = logging.getLogger(__name__)

_GENERIC = {"", "course", "view", "view course", "go to course", "enter course", "my courses", "courses", "more"}
_NOISE = re.compile(
    r"^(?:course name|course is starred|starred|course image)\s*:?\s*",
    re.I,
)
_SPLIT = re.compile(r"\s{2,}|\n|\b(?:Course category|Course overview|Last accessed|Hidden from students|\d+% complete)\b", re.I)


def clean_course_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name or " ").strip()
    previous = None
    while previous != name:
        previous = name
        name = _NOISE.sub("", name).strip()
    name = _SPLIT.split(name, maxsplit=1)[0].strip(" -|")
    return name[:160]


def _wait_for_course_links(session: BrowserSession) -> None:
    try:
        session.page.locator("a[href*='/course/view.php']").first.wait_for(state="attached", timeout=12_000)
    except PlaywrightError:
        pass
    session.page.wait_for_timeout(500)


def get_courses(session: BrowserSession) -> list[dict]:
    """Return [{id, name, url}] for every enrolled course visible to the student."""
    names: dict[int, list[str]] = defaultdict(list)
    for path in ("/my/courses.php", "/my/"):
        session.open(f"{session.base_url}{path}")
        require_session(session)
        _wait_for_course_links(session)
        for link in session.links():
            course_id = course_id_from_url(link["url"])
            if not course_id or course_id == 1:  # id=1 is the site front page
                continue
            for candidate in (link.get("name", ""), link.get("context", "")):
                cleaned = clean_course_name(candidate)
                if cleaned.lower() not in _GENERIC:
                    names[course_id].append(cleaned)
                    break
            else:
                names.setdefault(course_id, [])
        if names:
            break

    courses = []
    for course_id, candidates in names.items():
        # Prefer a real name ("Automata Theory (CB)") over a code ("FL-26-CS310-CB").
        name = max(candidates, key=lambda n: (" " in n, candidates.count(n), len(n))) if candidates else f"Course {course_id}"
        courses.append({
            "id": course_id,
            "name": name,
            "url": f"{session.base_url}/course/view.php?id={course_id}",
        })
    courses.sort(key=lambda c: c["name"].casefold())
    log.info("Discovered %s courses", len(courses))
    return courses
