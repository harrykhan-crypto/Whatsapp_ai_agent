import pytest

from services.intent import parse

COURSES = [
    {"id": 101, "name": "Automata Theory (CB)"},
    {"id": 102, "name": "Operating System Concepts (CB)"},
    {"id": 103, "name": "Human Computer Interaction (CB)"},
    {"id": 104, "name": "Software Construction and Development (CA)"},
    {"id": 105, "name": "Software Quality Engineering (CA)"},
    {"id": 106, "name": "Introduction to Psychology (CB)"},
]


def p(text):
    return parse(text, COURSES)


def test_slash_commands():
    assert p("/today").action == "today"
    assert p("/menu").action == "menu"
    i = p("/download F12")
    assert i.action == "download" and i.argument == "F12"
    i = p("/assignments HCI")
    assert i.action == "assignments" and i.course_id == 103
    assert p("/foo").action == "unknown_command"
    assert p("3").action == "upcoming"


@pytest.mark.parametrize("text, period, cats, course, action", [
    ("Kal kya submit karna hai?", "tomorrow", [], None, "query"),
    ("Assalam o Alaikum, kal university ka kya kaam hai?", "tomorrow", [], None, "query"),
    ("Next week ke quizzes batao.", "next_week", ["quiz"], None, "query"),
    ("Is week ke labs show karo.", "week", ["lab"], None, "query"),
    ("What assignments are due tomorrow?", "tomorrow", ["assignment"], None, "query"),
    ("aaj ka kaam", "today", [], None, "query"),
    ("Show my Automata Theory assignments", None, ["assignment"], 101, "query"),
    ("OS ke quizzes", None, ["quiz"], 102, "query"),
    ("sqe assignment kab hai", None, ["assignment"], 105, "query"),
])
def test_natural_language_queries(text, period, cats, course, action):
    i = p(text)
    assert i.action == action, i
    assert i.period == period
    assert i.categories == cats
    assert i.course_id == course


def test_greeting_detected():
    i = p("Assalam o Alaikum, kal university ka kya kaam hai?")
    assert i.greeting
    assert p("Assalam o alaikum").action == "greeting"


def test_pending_and_overdue():
    i = p("Konsi assignments pending hain?")
    assert i.action == "query" and i.pending_only and i.categories == ["assignment"]
    assert p("Show overdue assignments").action == "overdue"
    assert p("kaunsa kaam reh gaya hai").pending_only


def test_file_requests():
    i = p("Automata ka assignment bhejo.")
    assert i.action == "download" and i.course_id == 101 and i.categories == ["assignment"]
    i = p("Psychology ki file download kar ke bhejo.")
    assert i.action == "download" and i.course_id == 106
    i = p("Send me HCI Assignment 2.")
    assert i.action == "download" and i.course_id == 103 and i.numbers == ["2"]
    i = p("Send me all pending assignment files.")
    assert i.action == "download" and i.pending_only and i.categories == ["assignment"]
    i = p("SQE lecture 3 slides chahiye")
    assert i.action == "download" and i.course_id == 105 and "lecture" in i.terms


def test_other_actions():
    assert p("my courses").action == "courses"
    assert p("full report do").action == "report"
    assert p("koi announcement aayi?").action == "announcements"
    assert p("mere grades").action == "grades"


def test_ambiguous_course():
    i = p("software assignments")
    assert i.course_id is None and len(i.ambiguous_courses) == 2
    assert p("software quality assignments").course_id == 105


@pytest.mark.parametrize("text, action, checks", [
    ("overdue files bhejo sirf", "download", dict(overdue_only=True, categories=[])),
    ("sirf overdue assignments ki files bhejo", "download", dict(overdue_only=True, categories=["assignment"])),
    ("late assignments ki files", "download", dict(overdue_only=True)),
    ("jo assignments miss ho gayi unki files bhejo", "download", dict(overdue_only=True, terms=[])),
    ("kon si assignments late hain", "overdue", dict(terms=[])),
    ("pending files bhejo", "download", dict(pending_only=True)),
    ("sirf quizzes batao", "query", dict(categories=["quiz"], terms=[])),
    ("only labs", "query", dict(categories=["lab"], terms=[])),
    ("aaj ki files bhejo", "download", dict(period="today")),
    ("kal wali assignment ki file", "download", dict(period="tomorrow", categories=["assignment"])),
    ("is hafte ki files", "download", dict(period="week")),
    ("upcoming quizzes ki files", "download", dict(period="upcoming", categories=["quiz"])),
    ("HCI ka overdue kaam", "overdue", dict(course_id=103)),
    ("OS ki sari files", "download", dict(course_id=102, all_items=True)),
    ("latest file bhejo", "download", dict(latest=True)),
    ("last assignment ki file", "download", dict(latest=True, categories=["assignment"], terms=[])),
    ("submitted assignments dikhao", "query", dict(done_only=True, terms=[])),
    ("kal kitne quiz hain", "query", dict(period="tomorrow", categories=["quiz"], terms=[])),
    ("what is due this week", "query", dict(period="week")),
    ("show me assignments upcoming", "query", dict(period="upcoming", categories=["assignment"])),
    ("aane wale quizzes", "query", dict(period="upcoming", categories=["quiz"])),
    ("koi nayi announcement", "announcements", dict(terms=[])),
])
def test_many_phrasings(text, action, checks):
    i = p(text)
    assert i.action == action, (text, i)
    for key, value in checks.items():
        assert getattr(i, key) == value, (text, key, getattr(i, key))
