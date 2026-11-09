"""Understands WhatsApp messages in English and Roman Urdu.

Rule-based on purpose: it is predictable, works offline, costs nothing and
never invents answers. Every reply is built from real LMS data afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- vocab
GREETINGS = {"assalam", "assalamualaikum", "asalam", "aoa", "salam", "salaam", "slam", "hi", "hello", "hey",
             "helo", "assalamu", "alaikum", "alaikom", "walaikum", "o", "aslam", "asalamualaikum", "salaam."}
TODAY = {"aaj", "aj", "today", "todays", "abhi", "tonight", "aaj-kal"}
TOMORROW = {"kal", "kl", "tomorrow", "tmrw", "tomorow", "tommorow", "tmr", "kall"}
DAY_AFTER = {"parson", "parso", "parsun"}
WEEK = {"week", "weekly", "hafta", "hafte", "haftay", "hafty", "hafte", "hafteh", "wk"}
NEXT = {"next", "agle", "aglay", "agla", "agli", "agly", "anay", "aane", "aanay", "coming", "upcoming"}
THIS = {"this", "is", "iss", "ye", "yeh", "current"}
MONTH = {"month", "mahina", "mahine", "mahinay", "maheene"}

ASSIGNMENT = {"assignment", "assignments", "asignment", "asignments", "assigment", "assigments", "assignmnt", "assign"}
QUIZ = {"quiz", "quizzes", "quizes", "quizez", "test", "tests", "mcq", "mcqs", "exam", "imtihan"}
LAB = {"lab", "labs", "practical", "practicals", "lab-report", "labreport", "experiment"}
TASK = {"task", "tasks", "activity", "activities", "exercise", "exercises", "homework", "hw", "worksheet"}
PENDING = {"pending", "baqi", "baki", "baaki", "remaining", "incomplete", "unsubmitted", "missing", "reh", "rehti",
           "rehta", "rahti", "rehte", "rehtay", "left", "outstanding", "adhoori", "adhura"}
OVERDUE = {"overdue", "late", "missed", "miss", "expired", "guzar", "guzri", "guzra", "nikal", "past"}
FILES = {"file", "files", "pdf", "pdfs", "doc", "docx", "ppt", "pptx", "slides", "slide", "attachment",
         "attachments", "material", "materials", "notes", "handout", "handouts", "document", "documents", "zip"}
SEND = {"send", "bhejo", "bhej", "bhejdo", "bhejo.", "bhejain", "bhejen", "bhejna", "bhijwao", "dedo", "share",
        "download", "forward", "mangwa", "chahiye", "chahye", "chaiye"}
COURSES = {"courses", "course", "subjects", "subject", "mazameen", "enrolled"}
REPORT = {"report", "summary", "khulasa", "overview", "overall", "complete", "full", "detail"}
ANNOUNCE = {"announcement", "announcements", "news", "notice", "notices", "elaan", "update", "updates"}
GRADES = {"grade", "grades", "marks", "result", "results", "score", "scores", "gpa", "nambar"}
ALL_WORK = {"kaam", "kam", "work", "deadline", "deadlines", "due", "submit", "submission", "submissions",
            "jama", "karna", "karni", "dena", "deni", "university", "uni"}

FILLER = {
    "ka", "ki", "ke", "ko", "se", "me", "mein", "main", "mujhe", "mujay", "mujhy", "muje", "kya", "kia", "hai", "hain",
    "he", "hay", "karo", "kar", "kardo", "kr", "krdo", "do", "dein", "batao", "bata", "btao", "dikhao", "dikha",
    "show", "please", "plz", "pls", "my", "mera", "meri", "mere", "all", "sab", "saari", "sari", "sare", "saray",
    "konsi", "konsa", "konse", "kon", "kaun", "kaunsi", "wala", "wali", "wale", "and", "the", "a", "an", "of", "for",
    "to", "in", "on", "what", "which", "is", "are", "i", "have", "need", "list", "give", "tell", "about", "any",
    "there", "do", "does", "with", "ap", "aap", "app", "hum", "humain", "liye", "lie", "or", "aur", "bhi", "b",
    "get", "find", "check", "kab", "when", "time", "date", "upar", "ab", "tak", "jo", "jis", "us", "un", "ye",
    "yeh", "wo", "woh", "thoda", "jaldi", "now", "me.", "dear", "sir", "bro", "harry", "bot", "lms", "moodle",
    "hy", "hain?", "bhai", "ji", "g", "k", "o", "alaikum", "assalam", "salam", "aoa", "hi", "hello", "hey",
    "available", "new", "latest", "nayi", "naya", "naye", "it", "them", "that", "this", "these", "those", "me,",
}

COMMANDS = {
    "/menu": "menu", "/start": "menu", "/help": "help", "/today": "today", "/tomorrow": "tomorrow",
    "/upcoming": "upcoming", "/assignments": "assignments", "/quizzes": "quizzes", "/quiz": "quizzes",
    "/labs": "labs", "/lab": "labs", "/tasks": "tasks", "/overdue": "overdue", "/pending": "pending",
    "/courses": "courses", "/files": "files", "/download": "download", "/report": "report", "/sync": "sync",
    "/refresh": "sync", "/status": "status", "/announcements": "announcements", "/news": "announcements",
    "/grades": "grades", "/week": "week", "/nextweek": "next_week", "/notify": "notify", "/history": "history",
}

MENU_NUMBERS = {
    "1": "today", "2": "tomorrow", "3": "upcoming", "4": "assignments", "5": "quizzes", "6": "labs",
    "7": "tasks", "8": "overdue", "9": "courses", "10": "files", "11": "report", "12": "announcements",
}


@dataclass
class Intent:
    action: str
    period: str | None = None             # today, tomorrow, day_after, week, next_week, month
    categories: list[str] = field(default_factory=list)  # assignment, quiz, lab, task
    pending_only: bool = False
    overdue_only: bool = False
    want_files: bool = False
    course_id: int | None = None
    course_name: str | None = None
    ambiguous_courses: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)       # leftover words for activity search
    numbers: list[str] = field(default_factory=list)
    greeting: bool = False
    argument: str = ""
    raw: str = ""


def normalize(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    text = re.sub(r"assalam\s*o\s*alaikum|assalam\s*u\s*alaikum|asalam\s*o\s*alaikum|salam\s*alaikum", " assalam ", text)
    text = re.sub(r"lab\s+reports?", " lab ", text)
    text = re.sub(r"past\s+due", " overdue ", text)
    text = re.sub(r"day\s+after\s+tomorrow", " parson ", text)
    text = re.sub(r"next\s+7\s+days", " week ", text)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"(?<=[a-z])(\d+)\b", r" \1", text)  # "assignment2" -> "assignment 2"
    text = re.sub(r"[^\w\s#/+-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- courses
COMMON_ALIASES = {
    "os": "operating system", "osc": "operating system", "hci": "human computer interaction",
    "sqe": "software quality engineering", "scd": "software construction", "toa": "automata",
    "tafl": "automata", "psych": "psychology", "psy": "psychology", "psycho": "psychology",
    "dsa": "data structures", "oop": "object oriented", "ai": "artificial intelligence",
    "dbms": "database", "db": "database", "cn": "computer networks", "se": "software engineering",
    "dld": "digital logic", "coal": "assembly language", "pf": "programming fundamentals",
}
_COURSE_STOP = {"and", "of", "the", "to", "in", "for", "introduction", "intro", "ca", "cb", "cc", "cd", "a", "b"}


def course_aliases(name: str) -> set[str]:
    base = re.sub(r"\(.*?\)", " ", name.lower())
    words = [w for w in re.findall(r"[a-z]+", base) if w not in _COURSE_STOP]
    aliases = set()
    if len(words) >= 2:
        initials = "".join(w[0] for w in words)
        if len(initials) >= 3:
            aliases.add(initials)
        if len(words) >= 3:
            aliases.add("".join(w[0] for w in words[:3]))
    for alias, target in COMMON_ALIASES.items():
        if all(part in base for part in target.split()):
            aliases.add(alias)
    return aliases


def match_course(tokens: list[str], courses: list[dict]) -> tuple[dict | None, list[str], set[str]]:
    """Best matching course for the message tokens -> (course, ambiguous names, tokens used)."""
    best: list[tuple[int, dict, set[str]]] = []
    for course in courses:
        base = re.sub(r"\(.*?\)", " ", course["name"].lower())
        words = [w for w in re.findall(r"[a-z]+", base) if w not in _COURSE_STOP and len(w) >= 3]
        aliases = course_aliases(course["name"])
        score, used = 0, set()
        for token in tokens:
            if token in aliases:
                score += 3
                used.add(token)
                continue
            if len(token) < 4:
                continue
            for word in words:
                if token == word or (len(token) >= 5 and (word.startswith(token) or token.startswith(word))):
                    score += 2 if token == word else 1
                    used.add(token)
                    break
        if score:
            best.append((score, course, used))
    if not best:
        return None, [], set()
    best.sort(key=lambda item: -item[0])
    top = best[0][0]
    leaders = [item for item in best if item[0] == top]
    if len(leaders) > 1:
        return None, [item[1]["name"] for item in leaders], set()
    return best[0][1], [], best[0][2]


# --------------------------------------------------------------------------- parsing
def parse(text: str, courses: list[dict] | None = None) -> Intent:
    raw = (text or "").strip()
    norm = normalize(raw)
    tokens = norm.split()
    intent = Intent(action="unknown", raw=raw)
    if not tokens:
        intent.action = "menu"
        return intent

    # Slash commands and menu numbers.
    first = tokens[0]
    if first.startswith("/"):
        intent.action = COMMANDS.get(first, "unknown_command")
        intent.argument = " ".join(raw.split()[1:]).strip()
        if intent.argument and courses:
            arg_tokens = normalize(intent.argument).split()
            course, ambiguous, used = match_course(arg_tokens, courses)
            if course:
                intent.course_id, intent.course_name = course["id"], course["name"]
            intent.ambiguous_courses = ambiguous
            _fill_search_terms(intent, arg_tokens, used)
        return intent
    if len(tokens) == 1 and first in MENU_NUMBERS:
        intent.action = MENU_NUMBERS[first]
        return intent
    if norm in {"menu", "help", "options", "start", "commands"}:
        intent.action = "menu" if norm != "help" else "help"
        return intent

    token_set = set(tokens)
    intent.greeting = bool(token_set & GREETINGS) or norm.startswith("assalam")

    # Period.
    if token_set & DAY_AFTER:
        intent.period = "day_after"
    elif token_set & TOMORROW:
        intent.period = "tomorrow"
    elif token_set & TODAY:
        intent.period = "today"
    if token_set & WEEK:
        intent.period = "next_week" if _next_before_week(tokens) else "week"
    if token_set & MONTH:
        intent.period = "month"
    if not intent.period and (token_set & {"upcoming", "coming", "aane", "aanay", "anay", "agle", "aglay", "future"} or "coming up" in norm):
        intent.period = "upcoming"
    if not intent.period and token_set & {"soon", "jald", "jaldi", "near"}:
        intent.period = "week"

    # Categories.
    if token_set & ASSIGNMENT:
        intent.categories.append("assignment")
    if token_set & QUIZ:
        intent.categories.append("quiz")
    if token_set & LAB:
        intent.categories.append("lab")
    if token_set & TASK:
        intent.categories.append("task")

    intent.pending_only = bool(token_set & PENDING) or "not submitted" in norm or "submit nahi" in norm
    intent.overdue_only = bool(token_set & OVERDUE)
    intent.want_files = bool(token_set & FILES) or (
        bool(token_set & SEND) and not intent.period and not (token_set & REPORT)
        and not (token_set & {"batao", "bata", "btao", "show", "dikhao", "list"})
    )

    # Course.
    if courses:
        course, ambiguous, used = match_course(tokens, courses)
        if course:
            intent.course_id, intent.course_name = course["id"], course["name"]
        intent.ambiguous_courses = ambiguous
    else:
        used = set()
    _fill_search_terms(intent, tokens, used)

    # Decide the action.
    if intent.want_files:
        intent.action = "download"
    elif token_set & GRADES and not intent.period:
        intent.action = "grades"
    elif token_set & ANNOUNCE and not intent.categories:
        intent.action = "announcements"
    elif token_set & REPORT and not intent.categories:
        intent.action = "report"
    elif intent.overdue_only:
        intent.action = "overdue"
    elif (token_set & COURSES) and not intent.categories and not intent.course_id and not intent.period:
        intent.action = "courses"
    elif intent.period or intent.categories or intent.pending_only or intent.course_id or token_set & ALL_WORK:
        intent.action = "query"
    elif intent.greeting:
        intent.action = "greeting"
    return intent


def _next_before_week(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token in WEEK:
            return any(t in NEXT for t in tokens[max(0, index - 2):index])
    return False


_VOCAB = (GREETINGS | TODAY | TOMORROW | DAY_AFTER | WEEK | NEXT | THIS | MONTH | ASSIGNMENT | QUIZ | LAB | TASK
          | PENDING | OVERDUE | FILES | SEND | COURSES | REPORT | ANNOUNCE | GRADES | ALL_WORK | FILLER)


def _fill_search_terms(intent: Intent, tokens: list[str], used: set[str]) -> None:
    for token in tokens:
        if token.isdigit():
            intent.numbers.append(str(int(token)))
        elif token not in _VOCAB and token not in used and len(token) >= 2 and not token.startswith("/"):
            intent.terms.append(token)
