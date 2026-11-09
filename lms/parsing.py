"""Pure parsing helpers for Moodle page text (no browser needed, fully unit-tested)."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlparse

# ----------------------------------------------------------------------------
# Dates (kept compatible with the original app.py regexes, extended for Moodle 4)
# ----------------------------------------------------------------------------

_MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_WEEKDAY_PATTERN = (
    r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun),?\s*)?"
)
DATE_TEXT_RE = re.compile(
    rf"\b{_WEEKDAY_PATTERN}(\d{{1,2}})\s+({_MONTH_PATTERN})\.?,?\s+(\d{{4}})"
    rf"(?:,?\s+(?:at\s+)?(\d{{1,2}}):(\d{{2}})\s*([AP]\.?M\.?)?)?",
    re.IGNORECASE,
)
# "September 8, 2026, 11:59 PM" (US-style locale)
DATE_TEXT_US_RE = re.compile(
    rf"\b{_WEEKDAY_PATTERN}({_MONTH_PATTERN})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})"
    rf"(?:,?\s+(?:at\s+)?(\d{{1,2}}):(\d{{2}})\s*([AP]\.?M\.?)?)?",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?\b")
NUMERIC_DATE_RE = re.compile(
    r"\b(\d{1,2})/(\d{1,2})/(\d{4})(?:,?\s+(\d{1,2}):(\d{2})\s*([AP]M)?)?\b",
    re.IGNORECASE,
)


def _hour(hour: str, meridiem: str | None) -> int:
    h = int(hour)
    if meridiem:
        pm = meridiem.upper().replace(".", "").startswith("P")
        h = h % 12 + (12 if pm else 0)
    return h


def _month(name: str) -> int:
    return datetime.strptime(name[:3].title(), "%b").month


def parse_date(text: str) -> tuple[datetime | None, bool]:
    """Parse the first date in ``text``. Returns (naive datetime, has_time).

    A date without a time is treated as the end of that day (23:59).
    """
    if not text:
        return None, False
    candidates = []
    for pattern in (DATE_TEXT_RE, DATE_TEXT_US_RE, ISO_DATE_RE, NUMERIC_DATE_RE):
        match = pattern.search(text)
        if match:
            candidates.append((match.start(), pattern, match))
    for _, pattern, match in sorted(candidates, key=lambda c: c[0]):
        try:
            g = match.groups()
            if pattern is DATE_TEXT_RE:
                day, month, year, hour, minute, mer = g
                base = datetime(int(year), _month(month), int(day))
            elif pattern is DATE_TEXT_US_RE:
                month, day, year, hour, minute, mer = g
                base = datetime(int(year), _month(month), int(day))
            elif pattern is ISO_DATE_RE:
                year, month, day, hour, minute = g
                mer = None
                base = datetime(int(year), int(month), int(day))
            else:
                a, b, year, hour, minute, mer = g
                # Moodle in Pakistan uses day/month/year; fall back if impossible.
                try:
                    base = datetime(int(year), int(b), int(a))
                except ValueError:
                    base = datetime(int(year), int(a), int(b))
            if hour is not None:
                return base.replace(hour=_hour(hour, mer), minute=int(minute)), True
            return base.replace(hour=23, minute=59), False
        except (ValueError, TypeError):
            continue
    return None, False


# Labels Moodle uses for activity dates, mapped to our fields.
_DATE_LABELS: list[tuple[str, re.Pattern[str]]] = [
    ("opens_at", re.compile(r"^(?:opens?|opened|allow submissions from|available from|this quiz (?:will )?open(?:ed|s)? (?:at|on))\b", re.I)),
    ("due_at", re.compile(r"^(?:due(?: date)?|deadline|submission deadline)\b", re.I)),
    ("cutoff_at", re.compile(r"^(?:cut-?off(?: date)?)\b", re.I)),
    ("closes_at", re.compile(r"^(?:closes?|closed|close date|available until|this quiz (?:will )?close(?:d|s)? (?:at|on))\b", re.I)),
]


def parse_labeled_dates(text: str) -> dict[str, datetime | None]:
    """Find 'Opens:', 'Due:', 'Cut-off date', 'Closes:' style dates in page text.

    Handles both 'Due: Monday, 8 September 2026, 11:59 PM' on one line and
    table layouts where the value is on the next line.
    """
    result: dict[str, datetime | None] = {"opens_at": None, "due_at": None, "cutoff_at": None, "closes_at": None}
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines):
        for field, pattern in _DATE_LABELS:
            if result[field] is not None or not pattern.search(line):
                continue
            dt, _ = parse_date(line)
            if dt is None and index + 1 < len(lines):
                dt, _ = parse_date(lines[index + 1])
            if dt is not None:
                result[field] = dt
            break
    return result


# ----------------------------------------------------------------------------
# Key/value tables (Moodle's submission status table, quiz info, etc.)
# ----------------------------------------------------------------------------

def field_from_lines(text: str, labels: tuple[str, ...]) -> str | None:
    """Value for the first line starting with one of ``labels`` (same line or next)."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        lower = line.lower()
        for label in labels:
            if lower.startswith(label):
                value = line[len(label):].strip(" :\t")
                if value:
                    return value[:200]
                for nxt in lines[index + 1:index + 3]:
                    if nxt:
                        return nxt[:200]
    return None


def lookup(table: dict[str, str], *labels: str) -> str | None:
    """Case-insensitive lookup in a {label: value} dict scraped from an HTML table."""
    lowered = {k.strip().lower().rstrip(":"): v for k, v in table.items()}
    for label in labels:
        if label in lowered and lowered[label].strip():
            return lowered[label].strip()
    return None


# ----------------------------------------------------------------------------
# Status normalisation
# ----------------------------------------------------------------------------

def _assignment_status_from(text: str) -> str:
    text = (text or "").lower()
    if "nothing to be submitted" in text or "nothing to submit" in text:
        return "not_required"
    if "draft (not submitted)" in text or text.strip() == "draft":
        return "draft"
    if (
        "no attempt" in text
        or "no submissions have been made yet" in text
        or "not submitted" in text
        or "new submission" in text
    ):
        return "not_submitted"
    if "submitted for grading" in text or re.search(r"\bsubmitted\b", text):
        return "submitted"
    return "unknown"


def normalize_assignment_status(raw: str | None, page_text: str = "") -> str:
    """Normalise Moodle's 'Submission status' value; fall back to page text."""
    status = _assignment_status_from(raw or "")
    if status == "unknown" and page_text:
        status = _assignment_status_from(page_text)
    return status


def normalize_quiz_status(page_text: str) -> str:
    text = (page_text or "").lower()
    if "continue your attempt" in text or "continue the last attempt" in text:
        return "in_progress"
    if (
        "your final grade for this quiz" in text
        or "summary of your previous attempts" in text
        or "no more attempts are allowed" in text
        or "re-attempt quiz" in text
    ):
        return "attempted"
    return "not_attempted"


# ----------------------------------------------------------------------------
# Activity classification
# ----------------------------------------------------------------------------

_LAB_RE = re.compile(r"\b(lab|labs|laboratory|practical|practicals|lab[- ]?report|lab[- ]?task|experiment)\b", re.I)
_TASK_RE = re.compile(r"\b(task|tasks|weekly|week\s*\d+|exercise|worksheet|activity|homework|hw|project|presentation|case study)\b", re.I)
_QUIZ_RE = re.compile(r"\b(quiz|quizzes|test|mcqs?)\b", re.I)

GRADED_MODNAMES = {"assign", "quiz", "workshop", "lesson", "h5pactivity", "lti", "scorm", "choice", "feedback", "data", "glossary"}


def classify_activity(modname: str, name: str, section: str = "") -> str:
    """Map a Moodle module to one of: assignment, quiz, lab, task, forum, resource, other."""
    modname = (modname or "").lower()
    label = f"{name} {section}"
    if modname == "quiz":
        return "quiz"
    if modname == "assign":
        if _LAB_RE.search(name):
            return "lab"
        if _QUIZ_RE.search(name):
            return "quiz"
        if _TASK_RE.search(name) and not re.search(r"\bassignment\b", name, re.I):
            return "task"
        return "assignment"
    if modname == "forum":
        return "forum"
    if modname in {"resource", "folder", "url", "page", "book"}:
        return "resource"
    if modname in GRADED_MODNAMES:
        return "lab" if _LAB_RE.search(label) else "task"
    return "other"


# ----------------------------------------------------------------------------
# URLs and filenames
# ----------------------------------------------------------------------------

MODULE_PATH_RE = re.compile(r"/mod/([a-z0-9_]+)/view\.php", re.I)


def module_from_url(url: str) -> tuple[str | None, int | None]:
    """('assign', 123) for .../mod/assign/view.php?id=123."""
    parsed = urlparse(url)
    match = MODULE_PATH_RE.search(parsed.path)
    if not match:
        return None, None
    ids = parse_qs(parsed.query).get("id")
    try:
        cmid = int(ids[0]) if ids else None
    except ValueError:
        cmid = None
    return match.group(1).lower(), cmid


def course_id_from_url(url: str) -> int | None:
    parsed = urlparse(url)
    if "/course/view.php" not in parsed.path:
        return None
    ids = parse_qs(parsed.query).get("id")
    try:
        return int(ids[0]) if ids else None
    except ValueError:
        return None


def canonical_module_url(base_url: str, modname: str, cmid: int) -> str:
    return f"{base_url}/mod/{modname}/view.php?id={cmid}"


def filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    match = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)?''([^;]+)", header)
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename\s*=\s*"([^"]+)"', header) or re.search(r"filename\s*=\s*([^;]+)", header)
    if match:
        return match.group(1).strip().strip('"')
    return None


def filename_from_url(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


_WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name: str, fallback: str = "moodle_file") -> str:
    """Keep the original name and extension, only removing characters Windows forbids."""
    name = _WINDOWS_BAD.sub("_", (name or "").strip()).strip(" .")
    if not name:
        name = fallback
    stem = name.rsplit(".", 1)[0].lower()
    if stem in _RESERVED:
        name = f"_{name}"
    if len(name) > 150:
        if "." in name:
            stem, ext = name.rsplit(".", 1)
            name = f"{stem[:140]}.{ext[:8]}"
        else:
            name = name[:150]
    return name


def slug(text: str, limit: int = 60) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return (value[:limit].strip("-") or "item")


def clean_text(text: str, limit: int | None = None) -> str:
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    return text[:limit] if limit else text
