from datetime import datetime

from lms.parsing import (
    classify_activity,
    course_id_from_url,
    filename_from_content_disposition,
    module_from_url,
    normalize_assignment_status,
    normalize_quiz_status,
    parse_date,
    parse_labeled_dates,
    safe_filename,
)
from lms.course_scraper import clean_course_name
from lms.activity_detector import clean_activity_name


def test_parse_moodle4_dates():
    assert parse_date("Due: Monday, 10 November 2026, 11:59 PM") == (datetime(2026, 11, 10, 23, 59), True)
    assert parse_date("Opens: Wed, 5 Nov 2026, 9:00 AM") == (datetime(2026, 11, 5, 9, 0), True)
    assert parse_date("10 November 2026") == (datetime(2026, 11, 10, 23, 59), False)
    assert parse_date("November 10, 2026, 5:30 PM") == (datetime(2026, 11, 10, 17, 30), True)
    assert parse_date("2026-11-10 14:00") == (datetime(2026, 11, 10, 14, 0), True)
    assert parse_date("10/11/2026 2:00 PM") == (datetime(2026, 11, 10, 14, 0), True)  # day/month/year
    assert parse_date("Due 12:00 AM nothing") == (None, False)


def test_labeled_dates_same_line_and_table():
    text = "Opened: Monday, 3 November 2026, 12:00 AM\nDue: Monday, 10 November 2026, 11:59 PM\nCut-off date\nWednesday, 12 November 2026, 11:59 PM"
    dates = parse_labeled_dates(text)
    assert dates["opens_at"] == datetime(2026, 11, 3, 0, 0)
    assert dates["due_at"] == datetime(2026, 11, 10, 23, 59)
    assert dates["cutoff_at"] == datetime(2026, 11, 12, 23, 59)
    quiz = parse_labeled_dates("Opens: Thursday, 13 November 2026, 9:00 AM\nCloses: Friday, 14 November 2026, 9:00 AM")
    assert quiz["opens_at"].day == 13 and quiz["closes_at"].day == 14
    legacy = parse_labeled_dates("This quiz will close on Friday, 14 November 2026, 9:00 AM")
    assert legacy["closes_at"] == datetime(2026, 11, 14, 9, 0)


def test_status_normalisation():
    assert normalize_assignment_status("No attempt") == "not_submitted"
    assert normalize_assignment_status("Submitted for grading") == "submitted"
    assert normalize_assignment_status("Draft (not submitted)") == "draft"
    assert normalize_assignment_status("Nothing to be submitted for this assignment") == "not_required"
    assert normalize_assignment_status(None, "No submissions have been made yet") == "not_submitted"
    assert normalize_assignment_status("Something else") == "unknown"
    assert normalize_quiz_status("Summary of your previous attempts ... Your final grade for this quiz is 8") == "attempted"
    assert normalize_quiz_status("Attempts allowed: 1\nAttempt quiz") == "not_attempted"
    assert normalize_quiz_status("Continue your attempt") == "in_progress"


def test_classification():
    assert classify_activity("assign", "Assignment 2") == "assignment"
    assert classify_activity("assign", "Lab Task 3") == "lab"
    assert classify_activity("assign", "Lab Report 4") == "lab"
    assert classify_activity("assign", "Practical 2 submission") == "lab"
    assert classify_activity("assign", "Weekly Task 5") == "task"
    assert classify_activity("quiz", "Mid quiz") == "quiz"
    assert classify_activity("workshop", "Peer review") == "task"
    assert classify_activity("resource", "Slides") == "resource"
    assert classify_activity("forum", "Announcements") == "forum"


def test_urls_and_filenames():
    assert module_from_url("https://x/mod/assign/view.php?id=55") == ("assign", 55)
    assert module_from_url("https://x/course/view.php?id=5") == (None, None)
    assert course_id_from_url("https://x/course/view.php?id=7&section=2") == 7
    assert filename_from_content_disposition('attachment; filename="A 2.pdf"') == "A 2.pdf"
    assert filename_from_content_disposition("attachment; filename*=UTF-8''HCI%20Lab%201.docx") == "HCI Lab 1.docx"
    assert safe_filename('bad:name?.pdf') == "bad_name_.pdf"
    assert safe_filename("CON.txt") == "_CON.txt"


def test_name_cleaning():
    assert clean_course_name("Course name Automata Theory (CB)") == "Automata Theory (CB)"
    assert clean_course_name("Automata Theory (CB)\nCourse category: BSCS") == "Automata Theory (CB)"
    assert clean_activity_name("Lab Task 3 Assignment") == "Lab Task 3"
    assert clean_activity_name("view", "Quiz 2\nOpens: ...") == "Quiz 2"
