"""HARRY Moodle reader: browser-based Moodle access plus WhatsApp API endpoints.

Uses the already logged-in Moodle session in a persistent Playwright browser.
Run with: python app.py
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from flask import Flask, jsonify, request
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# Change MOODLE_URL in .env only if your university's Moodle URL changes.
BASE_URL = os.getenv("MOODLE_URL", "https://fall2026.auplmscs.com").rstrip("/")
MY_URL = f"{BASE_URL}/my/"
BASE_HOST = (urlparse(BASE_URL).hostname or "").lower()
PROFILE_DIR = Path(__file__).with_name("moodle_browser_profile")
FILE_DIR = Path(__file__).with_name("moodle_downloads")
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Avoid unexpectedly large WhatsApp uploads.
MAX_ASSIGNMENTS_TO_READ = 50
MAX_MODULES_TO_READ = 12

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xls", ".txt", ".zip", ".csv",
}

STOP_WORDS = {
    "a", "an", "and", "are", "am", "as", "at", "be", "can", "could",
    "do", "does", "for", "from", "get", "give", "have", "hello", "hey",
    "hi", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "please", "show", "tell", "send", "the", "to", "want", "what", "when",
    "where", "which", "who", "would", "you", "your", "find", "search",
    "all", "course", "courses", "assignment", "assignments", "quiz", "quizzes",
    "file", "files", "document", "documents", "download", "material", "materials",
    "notes", "note", "slides", "lecture", "lectures", "pdf", "pdfs", "doc",
    "docx", "ppt", "pptx", "xlsx", "xls", "zip", "csv", "myself", "please",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)
_playwright = None
_browser_context = None
_page = None


def _safe_moodle_url(url: str) -> str:
    """Resolve a URL and prevent browser navigation outside the Moodle host."""
    absolute = urljoin(BASE_URL + "/", url)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != BASE_HOST:
        raise ValueError("Refusing to open a URL outside the configured Moodle site.")
    return absolute


def open_page(url: str) -> None:
    """Open a Moodle page in the persistent browser, preserving the login session."""
    global _page
    target = _safe_moodle_url(url)
    try:
        _page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    except PlaywrightTimeoutError:
        # Moodle pages may continue loading analytics or embedded resources.
        logging.warning("Page load timed out; continuing with visible page: %s", target)
    try:
        _page.locator("#region-main, [role='main'], main").first.wait_for(
            state="attached", timeout=5_000
        )
    except PlaywrightTimeoutError:
        pass
    try:
        _page.wait_for_timeout(350)
    except Exception:
        pass


def _page_text() -> str:
    """Get the most relevant visible page text rather than Moodle's full navigation."""
    selectors = ["#region-main", "[role='main']", "main", "body"]
    for selector in selectors:
        try:
            locator = _page.locator(selector).first
            if locator.count():
                text = locator.inner_text(timeout=3_000).strip()
                if text:
                    return re.sub(r"\n{3,}", "\n\n", text)
        except Exception:
            continue
    return ""


def collect_links() -> list[dict[str, str]]:
    """Collect unique visible anchor links from the current page."""
    try:
        rows = _page.locator("a[href]").evaluate_all(
            """anchors => anchors.map(a => {
                const raw = (a.innerText || a.getAttribute('aria-label') || '').trim();
                const parent = a.closest('.activity-item, .activity, .coursebox, .course-card, li');
                const parentText = parent ? (parent.innerText || '').trim() : '';
                return {
                    name: raw.replace(/\\s+/g, ' '),
                    context: parentText.replace(/\\s+/g, ' '),
                    url: a.href
                };
            })"""
        )
    except Exception as error:
        logging.warning("Could not collect page links: %s", error)
        return []

    output = []
    seen = set()
    for row in rows:
        url = str(row.get("url", "")).strip()
        if not url:
            continue
        try:
            url = _safe_moodle_url(url)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        name = str(row.get("name", "")).strip()
        context = str(row.get("context", "")).strip()
        if not name or name.lower() in {"view", "go", "click here", "file"}:
            if context:
                name = context[:180]
        name = re.sub(r"\s+", " ", name).strip()
        output.append({"name": name or "Moodle item", "context": context, "url": url})
    return output


def _is_login_page() -> bool:
    current = (_page.url or "").lower()
    if "/login/" in current or "login.php" in current:
        return True
    try:
        if _page.locator('input[type="password"]').count() > 0:
            return True
    except Exception:
        pass
    return False


def signed_in() -> bool:
    """Check login by opening My Moodle and checking for a login form/redirect."""
    try:
        open_page(MY_URL)
        return not _is_login_page()
    except Exception as error:
        logging.warning("Could not check Moodle login: %s", error)
        return False


def _course_link_name(link: dict[str, str]) -> str:
    name = link.get("name", "").strip()
    if name.lower() in {"course", "view course", "go to course", "enter course"}:
        name = link.get("context", "").strip()
    # Parent/card text may include progress and action labels; retain the first useful line.
    name = re.split(r"\s{2,}|\b(?:Course overview|Last accessed)\b", name, maxsplit=1)[0]
    name = re.sub(r"\s+", " ", name).strip()
    return name[:160]


def get_courses() -> list[dict[str, str]]:
    """Return enrolled courses visible from Moodle's My page."""
    open_page(MY_URL)
    if _is_login_page():
        return []

    courses = []
    seen = set()
    for link in collect_links():
        parsed = urlparse(link["url"])
        if "/course/view.php" not in parsed.path or not parsed.query:
            continue
        course_id_match = re.search(r"(?:^|&)id=(\d+)(?:&|$)", parsed.query)
        key = course_id_match.group(1) if course_id_match else link["url"]
        if key in seen:
            continue
        seen.add(key)
        name = _course_link_name(link)
        if not name or name.lower() in {"course", "view", "view course"}:
            continue
        courses.append({"name": name, "url": link["url"]})

    # Some Moodle themes put course names in the card but expose several links.
    courses.sort(key=lambda item: item["name"].casefold())
    return courses


def _module_type(url: str) -> str | None:
    path = urlparse(url).path.lower()
    if "/mod/assign/view.php" in path:
        return "assignment"
    if "/mod/quiz/view.php" in path:
        return "quiz"
    if "/mod/forum/view.php" in path:
        return "forum"
    if "/mod/resource/view.php" in path:
        return "resource"
    if "/mod/folder/view.php" in path:
        return "folder"
    if "/mod/url/view.php" in path:
        return "url"
    if "/mod/page/view.php" in path:
        return "page"
    if "/mod/book/view.php" in path:
        return "book"
    return None


def _name_for_module(link: dict[str, str]) -> str:
    name = re.sub(r"\s+", " ", link.get("name", "")).strip()
    if name.lower() in {"view", "view assignment", "view quiz", "file", "link", "more"}:
        context = re.sub(r"\s+", " ", link.get("context", "")).strip()
        if context:
            name = context
    return name[:180] or "Unnamed Moodle activity"


def get_course_modules(
    module_type: str | None = None,
    course_filter: str | None = None,
) -> list[dict[str, str]]:
    """Collect assignment/quiz/forum/resource links from each selected course page."""
    courses = get_courses()
    if not courses:
        return []

    if course_filter:
        words = [w for w in re.findall(r"[a-z0-9]+", course_filter.lower()) if len(w) > 2]
        filtered = [
            c for c in courses
            if all(word in c["name"].lower() for word in words)
        ]
        if not filtered and words:
            filtered = [
                c for c in courses
                if any(word in c["name"].lower() for word in words)
            ]
        if filtered:
            courses = filtered

    modules = []
    seen = set()
    for course in courses:
        try:
            open_page(course["url"])
            for link in collect_links():
                kind = _module_type(link["url"])
                if not kind or (module_type and kind != module_type):
                    continue
                key = (kind, link["url"])
                if key in seen:
                    continue
                seen.add(key)
                modules.append({
                    "name": _name_for_module(link),
                    "url": link["url"],
                    "course": course["name"],
                    "type": kind,
                })
        except Exception as error:
            logging.warning("Skipping course %s: %s", course["name"], error)
    return modules


_MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_WEEKDAY_PATTERN = r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*)?"
_DATE_TEXT_RE = re.compile(
    rf"\b{_WEEKDAY_PATTERN}(\d{{1,2}})\s+({_MONTH_PATTERN})\.?\s+(\d{{4}})"
    rf"(?:,?\s+(\d{{1,2}}):(\d{{2}})\s*(AM|PM)?)?\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(
    r"\b(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?\b"
)
_NUMERIC_DATE_RE = re.compile(
    r"\b(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2})\s*(AM|PM)?)?\b",
    re.IGNORECASE,
)


def parse_due_info(text: str) -> tuple[datetime | None, bool, str | None]:
    """Extract the first likely Moodle due/deadline date from module text."""
    lines = text.splitlines()
    preferred_lines = []
    for index, line in enumerate(lines):
        lower = line.lower()
        if any(label in lower for label in ("due", "deadline", "closes", "close date", "available until")):
            preferred_lines.append(line)
            if index + 1 < len(lines):
                preferred_lines.append(lines[index + 1])
    search_text = "\n".join(preferred_lines) if preferred_lines else text
    patterns = (_DATE_TEXT_RE, _ISO_DATE_RE, _NUMERIC_DATE_RE)

    for pattern in patterns:
        for match in pattern.finditer(search_text):
            try:
                groups = match.groups()
                has_time = False
                if pattern is _DATE_TEXT_RE:
                    day, month, year, hour, minute, meridiem = groups
                    month_num = datetime.strptime(month[:3].title(), "%b").month
                    if hour is not None:
                        has_time = True
                        h = int(hour)
                        if meridiem:
                            h = h % 12 + (12 if meridiem.upper() == "PM" else 0)
                        dt = datetime(int(year), month_num, int(day), h, int(minute))
                    else:
                        dt = datetime(int(year), month_num, int(day), 23, 59)
                elif pattern is _ISO_DATE_RE:
                    year, month, day, hour, minute = groups
                    has_time = hour is not None
                    dt = datetime(
                        int(year), int(month), int(day),
                        int(hour) if hour is not None else 23,
                        int(minute) if minute is not None else 59,
                    )
                else:
                    month_or_day, day_or_month, year, hour, minute, meridiem = groups
                    # Moodle locale is commonly day/month/year; fall back to month/day/year if needed.
                    a, b = int(month_or_day), int(day_or_month)
                    try:
                        dt = datetime(int(year), b, a)
                    except ValueError:
                        dt = datetime(int(year), a, b)
                    has_time = hour is not None
                    if has_time:
                        h = int(hour)
                        if meridiem:
                            h = h % 12 + (12 if meridiem.upper() == "PM" else 0)
                        dt = dt.replace(hour=h, minute=int(minute))
                    else:
                        dt = dt.replace(hour=23, minute=59)
                return dt, has_time, match.group(0).strip()
            except (ValueError, TypeError):
                continue
    return None, False, None


def parse_due_date(text: str) -> datetime | None:
    """Compatibility helper: return a datetime or None."""
    return parse_due_info(text)[0]


def _field_from_lines(text: str, labels: tuple[str, ...]) -> str | None:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        lower = line.lower().strip(" :")
        for label in labels:
            if lower.startswith(label):
                value = re.sub(r"^.*?\b" + re.escape(label) + r"\b\s*:?[\s]*", "", line, flags=re.I).strip()
                if value and value.lower() != label:
                    return value[:180]
                if index + 1 < len(lines) and lines[index + 1]:
                    return lines[index + 1][:180]
    return None


def extract_assignment(item: dict[str, str]) -> dict[str, object]:
    """Read one assignment page and extract due date, submission state, and grade."""
    result: dict[str, object] = dict(item)
    result.update({"due": None, "due_has_time": False, "due_raw": None, "status": "Status not shown", "grade": None})
    try:
        open_page(item["url"])
        text = _page_text()
        due, has_time, raw = parse_due_info(text)
        result["due"] = due
        result["due_has_time"] = has_time
        result["due_raw"] = raw

        status = _field_from_lines(text, ("submission status", "attempt status", "status"))
        if not status:
            lower = text.lower()
            if "no submissions have been made yet" in lower or "no attempt" in lower:
                status = "Not submitted"
            elif "submitted for grading" in lower:
                status = "Submitted for grading"
            elif "draft (not submitted)" in lower:
                status = "Draft (not submitted)"
            elif "nothing to submit" in lower:
                status = "Nothing to submit"
        result["status"] = status or "Status not shown"

        grade = _field_from_lines(text, ("grade", "received grade"))
        if grade and len(grade) < 180:
            result["grade"] = grade
        else:
            grade_match = re.search(
                r"\bGrade\s*\n\s*([^\n]{1,100})",
                text,
                re.IGNORECASE,
            )
            if grade_match:
                result["grade"] = grade_match.group(1).strip()
    except Exception as error:
        logging.warning("Could not read assignment %s: %s", item.get("name"), error)
        result["error"] = "Could not read this Moodle page"
    return result


def _date_label(item: dict[str, object]) -> str:
    due = item.get("due")
    if not isinstance(due, datetime):
        return "No due date found"
    if item.get("due_has_time"):
        return due.strftime("%a, %d %b %Y at %I:%M %p").replace(" 0", " ")
    return due.strftime("%a, %d %b %Y")


def _course_filter_from_question(question: str, courses: list[dict[str, str]]) -> str | None:
    q = question.casefold()
    matches = [c for c in courses if c["name"].casefold() in q]
    if matches:
        return max(matches, key=lambda c: len(c["name"]))["name"]

    # Handles questions that mention only part of a long course name.
    q_words = [w for w in re.findall(r"[a-z0-9]+", q) if len(w) > 3 and w not in STOP_WORDS]
    best_course = None
    best_score = 0
    for course in courses:
        words = [w for w in re.findall(r"[a-z0-9]+", course["name"].casefold()) if len(w) > 3]
        score = sum(1 for w in words if w in q_words)
        if score > best_score:
            best_score = score
            best_course = course["name"]
    return best_course if best_score else None


def _read_module_text(item: dict[str, str]) -> str:
    try:
        open_page(item["url"])
        return _page_text()
    except Exception as error:
        logging.warning("Could not read module %s: %s", item.get("name"), error)
        return ""


def _format_assignment(item: dict[str, object]) -> str:
    return (
        f"• {item.get('name', 'Assignment')}\n"
        f"  Course: {item.get('course', 'Unknown course')}\n"
        f"  Due: {_date_label(item)}\n"
        f"  Status: {item.get('status', 'Status not shown')}"
        + (f"\n  Grade: {item['grade']}" if item.get("grade") else "")
    )


def _answer_assignments(question: str, courses: list[dict[str, str]]) -> str:
    course_filter = _course_filter_from_question(question, courses)
    modules = get_course_modules("assignment", course_filter)
    if not modules:
        return "I couldn't find any assignment links in the course pages I can access."

    modules = modules[:MAX_ASSIGNMENTS_TO_READ]
    assignments = [extract_assignment(item) for item in modules]
    today = date.today()
    q = question.casefold()

    if "tomorrow" in q:
        target_start = today + timedelta(days=1)
        target_end = target_start
        heading = f"Assignments due tomorrow ({target_start.strftime('%d %b %Y')})"
    elif "overdue" in q or "past due" in q:
        target_start = None
        target_end = today - timedelta(days=1)
        heading = "Overdue assignments"
    elif "today" in q:
        target_start = today
        target_end = today
        heading = f"Assignments due today ({today.strftime('%d %b %Y')})"
    elif "this week" in q or "weekly" in q or "next 7 days" in q:
        target_start = today
        target_end = today + timedelta(days=6)
        heading = "Assignments due in the next 7 days"
    elif "next week" in q:
        # Calendar week beginning next Monday.
        next_monday = today + timedelta(days=(7 - today.weekday()))
        target_start = next_monday
        target_end = next_monday + timedelta(days=6)
        heading = f"Assignments due next week ({target_start:%d %b}–{target_end:%d %b})"
    else:
        target_start = None
        target_end = None
        heading = "Your Moodle assignments"

    selected = []
    for item in assignments:
        due = item.get("due")
        if not isinstance(due, datetime):
            if target_start is None and target_end is None:
                selected.append(item)
            continue
        due_day = due.date()
        if target_start is not None and due_day < target_start:
            continue
        if target_end is not None and due_day > target_end:
            continue
        if target_start is None and target_end is not None and due_day > target_end:
            continue
        selected.append(item)

    if "overdue" in q or "past due" in q:
        selected = [
            item for item in selected
            if str(item.get("status", "")).lower() in {
                "not submitted", "draft (not submitted)", "no attempt",
                "status not shown", "not attempted",
            }
        ]

    selected.sort(key=lambda item: (item.get("due") or datetime.max, str(item.get("name", "")).casefold()))
    if not selected:
        if target_start == target_end and target_start is not None:
            return f"{heading}\n\nNo assignments with a matching due date were found in the Moodle pages I checked."
        return f"{heading}\n\nNo matching assignments were found in the Moodle pages I checked."

    limit = 18 if target_start is not None or target_end is not None else 12
    body = "\n\n".join(_format_assignment(item) for item in selected[:limit])
    note = "" if len(selected) <= limit else f"\n\nShowing {limit} of {len(selected)} matching assignments."
    return f"{heading}\n\n{body}{note}"


def _answer_quizzes(question: str, courses: list[dict[str, str]]) -> str:
    course_filter = _course_filter_from_question(question, courses)
    modules = get_course_modules("quiz", course_filter)
    if not modules:
        return "I couldn't find any quiz links in the course pages I can access."

    rows = []
    for item in modules[:MAX_MODULES_TO_READ]:
        text = _read_module_text(item)
        due, has_time, raw = parse_due_info(text)
        due_label = (
            due.strftime("%a, %d %b %Y at %I:%M %p").replace(" 0", " ")
            if due and has_time else due.strftime("%a, %d %b %Y") if due else "No opening/deadline date found"
        )
        attempts = _field_from_lines(text, ("attempts allowed", "attempts permitted"))
        time_limit = _field_from_lines(text, ("time limit",))
        row = f"• {item['name']}\n  Course: {item['course']}\n  Date: {due_label}"
        if attempts:
            row += f"\n  Attempts: {attempts}"
        if time_limit:
            row += f"\n  Time limit: {time_limit}"
        rows.append(row)

    return "Your Moodle quizzes\n\n" + "\n\n".join(rows)


def _answer_announcements(question: str, courses: list[dict[str, str]]) -> str:
    course_filter = _course_filter_from_question(question, courses)
    forums = get_course_modules("forum", course_filter)
    if not forums:
        return "I couldn't find any forum or announcement links in the course pages I can access."

    preferred = [item for item in forums if "announcement" in item["name"].casefold()]
    chosen = preferred or forums
    results = []
    for item in chosen[:5]:
        text = _read_module_text(item)
        if not text:
            continue
        # Keep the excerpt short and avoid returning enormous forum pages.
        excerpt = re.sub(r"\n{3,}", "\n\n", text).strip()[:900]
        results.append(f"• {item['name']} — {item['course']}\n{excerpt}")
    if not results:
        return "I found forum links but couldn't read announcement content from them."
    return "Moodle announcements / forums\n\n" + "\n\n---\n\n".join(results)


def _answer_grades(courses: list[dict[str, str]]) -> str:
    # Prefer Moodle's user grade report if the current account can access it.
    try:
        open_page(urljoin(BASE_URL + "/", "grade/report/user/index.php"))
        text = _page_text()
        if text and not _is_login_page() and not re.search(r"access denied|not permitted", text, re.I):
            # Moodle grade tables generally have Course/Grade labels. Avoid returning page navigation.
            if re.search(r"\bgrade\b|\bfeedback\b|\bpercentage\b", text, re.I):
                return "Your Moodle grade report\n\n" + text[:5000]
    except Exception as error:
        logging.info("Grade report unavailable, checking assignment pages: %s", error)

    modules = get_course_modules("assignment")[:MAX_ASSIGNMENTS_TO_READ]
    graded = []
    for item in modules:
        row = extract_assignment(item)
        if row.get("grade"):
            graded.append(
                f"• {row['name']} ({row['course']})\n  Grade: {row['grade']}\n  Status: {row['status']}"
            )
    if graded:
        return "Grades visible on your Moodle assignments\n\n" + "\n\n".join(graded[:20])
    return "I couldn't read a grade report or find visible assignment grades. Check whether your Moodle gradebook is available."


def answer_question(question: str) -> str:
    question = str(question or "").strip()
    if not question:
        return "Please ask a Moodle question, such as 'What assignments are due tomorrow?'"

    courses = get_courses()
    if not courses:
        return (
            "I can't see your enrolled Moodle courses yet. Sign in to Moodle in the browser "
            "window opened by app.py, keep it open, and send your question again."
        )

    q = question.casefold()
    if q in {"menu", "help", "options"}:
        return (
            "HARRY Moodle assistant\n\n"
            "• My courses\n• Assignments due today/tomorrow\n"
            "• Weekly assignment summary\n• Quizzes\n• Announcements\n• Grades\n"
            "• Course files are available through POST /files/search"
        )

    if any(phrase in q for phrase in ("announcement", "announcements", "forum post", "course news")):
        return _answer_announcements(question, courses)

    if "grade" in q or "marks" in q or "result" in q:
        return _answer_grades(courses)

    if any(word in q for word in ("quiz", "quizzes", "test", "exam")):
        return _answer_quizzes(question, courses)

    if any(word in q for word in ("assignment", "assignments", "deadline", "due", "overdue", "past due", "weekly")):
        return _answer_assignments(question, courses)

    if any(phrase in q for phrase in ("my courses", "list courses", "which courses", "all courses", "course list")) or q == "courses":
        return "Your Moodle courses\n\n" + "\n".join(f"• {course['name']}" for course in courses)

    # General question: find matching course activities, then show their page text.
    course_filter = _course_filter_from_question(question, courses)
    modules = get_course_modules(course_filter=course_filter)
    query_words = [w for w in re.findall(r"[a-z0-9]+", q) if len(w) > 2 and w not in STOP_WORDS]
    scored = []
    for item in modules:
        haystack = f"{item['name']} {item['course']}".casefold()
        score = sum(1 for word in query_words if word in haystack)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"].casefold()))
    excerpts = []
    for _, item in scored[:4]:
        text = _read_module_text(item)
        if text:
            excerpts.append(f"• {item['name']} — {item['course']}\n{text[:650]}")
    if excerpts:
        return "Relevant Moodle pages\n\n" + "\n\n---\n\n".join(excerpts)

    return (
        "I couldn't match that question to a specific Moodle page. Try asking for your courses, "
        "assignments due tomorrow, quizzes, announcements, or grades. Include a course name for a narrower search."
    )


def find_course_files(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Find and download matching course files through the logged-in Moodle session."""
    FILE_DIR.mkdir(parents=True, exist_ok=True)
    query = str(query or "").strip().casefold()
    if not query:
        return []

    words = [w for w in re.findall(r"[a-z0-9]+", query) if w not in STOP_WORDS and len(w) > 1]
    extension_map = {
        "pdf": ".pdf", "pdfs": ".pdf", "doc": ".doc", "docx": ".docx",
        "ppt": ".ppt", "pptx": ".pptx", "xls": ".xls", "xlsx": ".xlsx",
        "zip": ".zip", "csv": ".csv", "txt": ".txt",
    }
    wanted_extensions = {extension_map[w] for w in words if w in extension_map}
    keywords = [w for w in words if w not in extension_map]

    courses = get_courses()
    if not courses:
        raise RuntimeError("Moodle is not signed in. Sign in through the opened browser first.")

    course_matches = []
    if keywords:
        for course in courses:
            course_words = set(re.findall(r"[a-z0-9]+", course["name"].casefold()))
            if sum(1 for word in keywords if word in course_words) > 0:
                course_matches.append(course)
    scan_courses = course_matches or courses

    candidates: list[dict[str, str]] = []
    seen_urls = set()

    def add_candidate(name: str, url: str, course_name: str) -> None:
        try:
            url = _safe_moodle_url(url)
        except ValueError:
            return
        path = unquote(urlparse(url).path)
        extension = Path(path).suffix.casefold()
        is_plugin_file = "/pluginfile.php/" in path.casefold()
        if extension not in SUPPORTED_EXTENSIONS and not is_plugin_file:
            return
        if url in seen_urls:
            return
        seen_urls.add(url)
        derived_name = unquote(Path(path).name).strip()
        if not derived_name or derived_name.casefold() == "pluginfile.php":
            derived_name = name.strip() or "moodle_file"
        # The link text is often a more useful filename than a generic 'File' label.
        if name and name.strip().lower() not in {"file", "download", "view", "resource"}:
            if Path(name.strip()).suffix.casefold() in SUPPORTED_EXTENSIONS:
                derived_name = name.strip()
        candidates.append({"name": derived_name, "url": url, "course": course_name})

    resource_pages = []
    for course in scan_courses:
        try:
            open_page(course["url"])
            for link in collect_links():
                kind = _module_type(link["url"])
                path = urlparse(link["url"]).path.casefold()
                if Path(unquote(path)).suffix.casefold() in SUPPORTED_EXTENSIONS or "/pluginfile.php/" in path:
                    add_candidate(link["name"], link["url"], course["name"])
                elif kind in {"resource", "folder"}:
                    resource_pages.append({
                        "name": link["name"], "url": link["url"], "course": course["name"]
                    })
        except Exception as error:
            logging.warning("File search skipped course %s: %s", course["name"], error)

    for resource in resource_pages[:60]:
        try:
            open_page(resource["url"])
            current_url = _page.url
            current_path = unquote(urlparse(current_url).path).casefold()
            if Path(current_path).suffix.casefold() in SUPPORTED_EXTENSIONS or "/pluginfile.php/" in current_path:
                add_candidate(resource["name"], current_url, resource["course"])
            for link in collect_links():
                path = unquote(urlparse(link["url"]).path).casefold()
                if Path(path).suffix.casefold() in SUPPORTED_EXTENSIONS or "/pluginfile.php/" in path:
                    add_candidate(link["name"], link["url"], resource["course"])
        except Exception as error:
            logging.info("Could not inspect resource %s: %s", resource["name"], error)

    def score_file(item: dict[str, str]) -> int:
        combined = f"{item['name']} {item['course']}".casefold()
        return sum(1 for word in keywords if word in combined)

    candidates = [
        item for item in candidates
        if (not wanted_extensions or Path(item["name"]).suffix.casefold() in wanted_extensions)
        and (not keywords or score_file(item) > 0)
    ]
    candidates.sort(key=lambda item: (-score_file(item), item["name"].casefold()))

    results = []
    for item in candidates:
        if len(results) >= limit:
            break
        try:
            response = _browser_context.request.get(item["url"], timeout=45_000)
            if not response.ok:
                logging.warning("File request failed: %s (HTTP %s)", item["name"], response.status)
                continue
            content = response.body()
            if not content:
                continue
            if len(content) > MAX_DOWNLOAD_BYTES:
                logging.warning("Skipping large file %s (%s bytes)", item["name"], len(content))
                continue
            content_type = response.headers.get("content-type", "").casefold()
            if "text/html" in content_type and re.search(rb"<\s*(?:!doctype\s+html|html|form)\b", content[:1000], re.I):
                logging.warning("Moodle returned an HTML page instead of file bytes: %s", item["name"])
                continue

            filename = Path(item["name"]).name
            filename = re.sub(r"[^A-Za-z0-9._ -]", "_", filename).strip(" .")
            if not filename:
                filename = "moodle_file"
            if Path(filename).suffix.casefold() not in SUPPORTED_EXTENSIONS:
                # Infer extension from response type where possible.
                inferred = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) if content_type else None
                if inferred in SUPPORTED_EXTENSIONS:
                    filename += inferred
                else:
                    continue

            unique = hashlib.sha256(item["url"].encode("utf-8")).hexdigest()[:10]
            destination = FILE_DIR / f"{unique}_{filename}"
            destination.write_bytes(content)
            results.append({
                "filename": filename,
                "path": str(destination.resolve()),
                "course": item["course"],
                "url": item["url"],
                "size_bytes": str(len(content)),
            })
        except Exception as error:
            logging.warning("Could not download Moodle file %s: %s", item["name"], error)

    return results


@app.get("/")
def home():
    return (
        "HARRY Moodle reader is running. Sign in to Moodle in the opened browser, "
        "then use /health, POST /ask, or POST /files/search."
    )


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "HARRY Moodle reader",
        "moodle_url": BASE_URL,
        "browser_started": _browser_context is not None,
    })


@app.post("/ask")
def ask():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()
    if not question:
        return jsonify({"answer": "Please send a question in the 'question' field."}), 400
    try:
        answer = answer_question(question)
        return jsonify({"answer": answer})
    except Exception as error:
        logging.exception("Question handling failed")
        return jsonify({"answer": "Moodle could not be read. Check that the browser is signed in and inspect the app.py terminal for details."}), 500


@app.post("/files/search")
def search_files():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query", "")).strip()
    if not query:
        return jsonify({"files": [], "error": "Enter a course name or file type."}), 400
    try:
        results = find_course_files(query)
        if not results:
            return jsonify({
                "files": [],
                "error": "No matching downloadable files were found in the Moodle course pages checked."
            })
        return jsonify({"files": results})
    except RuntimeError as error:
        return jsonify({"files": [], "error": str(error)}), 401
    except Exception:
        logging.exception("File search failed")
        return jsonify({"files": [], "error": "Moodle file search failed. Check the app.py terminal."}), 500


def start_browser() -> None:
    global _playwright, _browser_context, _page
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    FILE_DIR.mkdir(parents=True, exist_ok=True)

    _playwright = sync_playwright().start()
    _browser_context = _playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        accept_downloads=True,
        viewport={"width": 1365, "height": 900},
    )
    _page = _browser_context.pages[0] if _browser_context.pages else _browser_context.new_page()
    try:
        open_page(MY_URL)
    except Exception as error:
        logging.warning("Moodle page did not open automatically: %s", error)
    print("\nHARRY opened Moodle in a browser window.")
    print("Sign in to your Moodle account if prompted, then keep this window open.")
    print("Local API: http://127.0.0.1:5000")


def stop_browser() -> None:
    global _playwright, _browser_context
    try:
        if _browser_context is not None:
            _browser_context.close()
    except Exception:
        pass
    try:
        if _playwright is not None:
            _playwright.stop()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        start_browser()
        # Keep Flask single-threaded because Playwright's synchronous API must stay
        # on the same thread where the browser context was created.
        app.run(host="127.0.0.1", port=5000, debug=False, threaded=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nStopping HARRY Moodle reader...")
    finally:
        stop_browser()
