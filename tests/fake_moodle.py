"""A small Moodle 4.x look-alike used ONLY by the automated tests.

It reproduces the HTML structures the scrapers rely on (course cards, activity
lists, activity dates, submission status tables, quiz info, forums, pluginfile
downloads and the login redirect) so the real Playwright browser code can be
exercised end-to-end without touching the real LMS.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, Response, make_response, redirect, request
from werkzeug.serving import make_server

TZ = ZoneInfo("Asia/Karachi")
SESSION_COOKIE = "MoodleSession"
VALID_SESSION = "test-session-123"


def moodle_date(dt: datetime) -> str:
    # Moodle style: "Monday, 10 November 2026, 11:59 PM"
    return dt.strftime("%A, %d %B %Y, %I:%M %p").replace(", 0", ", ").replace(" 0", " ")


class FakeMoodle:
    def __init__(self):
        now = datetime.now(TZ).replace(second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59)
        self.session_valid = True
        self.credentials = ("student01", "Secret#123")
        self.captcha = False
        self.login_attempts = 0
        self.courses = {
            101: "Automata Theory (CB)",
            102: "Operating System Concepts (CB)",
            103: "Human Computer Interaction (CB)",
            104: "Software Construction and Development (CA)",
            105: "Software Quality Engineering (CA)",
            106: "Introduction to Psychology (CB)",
        }
        # cmid -> activity
        self.activities: dict[int, dict] = {
            1001: dict(course=101, mod="assign", name="Assignment 2", opens=now - timedelta(days=5), due=today_end,
                       status="No attempt", files=["Automata_Assignment_2.pdf"]),
            1002: dict(course=101, mod="quiz", name="Quiz 1", opens=now - timedelta(days=10), close=now - timedelta(days=8),
                       attempted=True, grade="8.00 / 10.00"),
            2001: dict(course=102, mod="quiz", name="Quiz 2", opens=(now + timedelta(days=1)).replace(hour=10, minute=0),
                       close=(now + timedelta(days=2)).replace(hour=10, minute=0), attempted=False),
            2002: dict(course=102, mod="assign", name="Lab Task 3", opens=now - timedelta(days=3),
                       due=now + timedelta(days=3), status="No attempt", files=["OS_Lab_3.docx"]),
            3001: dict(course=103, mod="assign", name="Assignment 2", opens=now - timedelta(days=7),
                       due=(now + timedelta(days=1)).replace(hour=17, minute=0), status="No attempt",
                       files=["HCI_Assignment_2.pdf", "HCI_Template.docx"]),
            3002: dict(course=103, mod="assign", name="Assignment 1", opens=now - timedelta(days=20),
                       due=now - timedelta(days=10), status="Submitted for grading", grade="18.00 / 20.00", files=[]),
            4001: dict(course=104, mod="assign", name="Lab Report 4", opens=now - timedelta(days=4),
                       due=now - timedelta(days=1), status="No attempt", files=["SCD_Lab_4.zip"]),
            4002: dict(course=104, mod="assign", name="Weekly Task 5", opens=now - timedelta(days=1),
                       due=now + timedelta(days=5), status="Draft (not submitted)", files=[]),
            5001: dict(course=105, mod="resource", name="Lecture 3 Slides", files=["SQE_Lecture_3.pptx"]),
            6001: dict(course=106, mod="assign", name="Psychology Assignment 1", opens=now - timedelta(days=2),
                       due=now + timedelta(days=9), status="No attempt", files=["Psychology_Assignment_1.pdf"]),
            6002: dict(course=106, mod="forum", name="Announcements"),
        }
        self.discussions = {
            9001: dict(forum=6002, subject="Midterm schedule", body="The midterm exam will be held next Monday in Room 12.",
                       posted=now - timedelta(days=1)),
        }
        self.app = self._make_app()
        self._server = None
        self.port = None

    # ------------------------------------------------------------------ server
    def start(self) -> str:
        self._server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self._server.server_port
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    # -------------------------------------------------------------------- html
    def _page(self, body: str, title: str = "Moodle") -> str:
        return (f"<!doctype html><html><head><title>{title}</title></head><body>"
                f"<nav><a href='/my/courses.php'>My courses</a> <span class='usertext'>Test Student</span></nav>"
                f"<div id='region-main' role='main'>{body}</div></body></html>")

    def _make_app(self) -> Flask:
        app = Flask("fake_moodle")
        fake = self

        @app.before_request
        def require_login():
            if request.path.startswith("/login"):
                return None
            if not fake.session_valid or request.cookies.get(SESSION_COOKIE) != VALID_SESSION:
                return redirect("/login/index.php")
            return None

        def login_form(error: str = ""):
            captcha = "<div class='g-recaptcha' data-sitekey='x'></div>" if fake.captcha else ""
            return fake._page(
                "<h2>Log in to Learning Management System, Fall 2026</h2>"
                + (f"<div class='alert alert-danger' id='loginerrormessage'>{error}</div>" if error else "")
                + "<form method='post' action='/login/index.php' id='login'>"
                "<input type='hidden' name='logintoken' value='tok'>"
                "<input id='username' name='username'><input id='password' type='password' name='password'>"
                + captcha + "<button type='submit' id='loginbtn'>Log in</button></form>",
                "Log in")

        @app.get("/login/index.php")
        def login():
            return login_form()

        @app.post("/login/index.php")
        def login_post():
            fake.login_attempts += 1
            if fake.captcha:
                return login_form("Please complete the CAPTCHA")
            if (request.form.get("username"), request.form.get("password")) != fake.credentials:
                return login_form("Invalid login, please try again")
            fake.session_valid = True
            response = redirect("/my/")
            response.set_cookie(SESSION_COOKIE, VALID_SESSION)
            return response

        @app.get("/my/")
        def dashboard():
            return fake._page("<section class='block_myoverview'>Loading…</section>", "Dashboard")

        @app.get("/my/courses.php")
        def my_courses():
            cards = "".join(
                f"<div class='card course-card'><a href='/course/view.php?id={cid}'><img alt=''></a>"
                f"<a class='aalink coursename' href='/course/view.php?id={cid}'>"
                f"<span class='sr-only'>Course name</span> FL-26-CS{300 + cid}-CB</a>"
                f"<div class='text-muted'>Course category: BSCS</div></div>"
                for cid, name in fake.courses.items()
            )
            return fake._page(f"<div data-region='course-content'>{cards}</div>", "My courses")

        @app.get("/course/view.php")
        def course():
            cid = int(request.args["id"])
            items = []
            for cmid, a in fake.activities.items():
                if a["course"] != cid:
                    continue
                label = {"assign": "Assignment", "quiz": "Quiz", "resource": "File", "forum": "Forum"}[a["mod"]]
                dates = ""
                if a.get("due"):
                    dates = f"<div class='activity-dates'><div><strong>Due:</strong> {moodle_date(a['due'])}</div></div>"
                items.append(
                    f"<li class='activity activity-wrapper modtype_{a['mod']}' id='module-{cmid}'>"
                    f"<div class='activity-item'><a href='/mod/{a['mod']}/view.php?id={cmid}' class='aalink'>"
                    f"<span class='instancename'>{a['name']} <span class='accesshide'> {label}</span></span></a>{dates}</div></li>"
                )
            body = (f"<div class='page-header-headings'><h1>{fake.courses[cid]}</h1></div><ul class='topics'><li class='section course-section'>"
                    f"<h3 class='sectionname'>Week 1</h3><ul class='section'>{''.join(items)}</ul></li></ul>")
            return fake._page(body, fake.courses[cid])

        def files_html(cmid: int, area: str) -> str:
            a = fake.activities[cmid]
            return "".join(
                f"<div class='fileuploadsubmission'><a href='/pluginfile.php/55/{area}/{cmid}/{name}?forcedownload=1'>{name}</a></div>"
                for name in a.get("files", [])
            )

        @app.get("/mod/assign/view.php")
        def assign():
            cmid = int(request.args["id"])
            a = fake.activities[cmid]
            rows = [("Submission status", a["status"]), ("Grading status", "Graded" if a.get("grade") else "Not graded"),
                    ("Time remaining", "2 days 3 hours"), ("Last modified", "-")]
            if a.get("grade"):
                rows.append(("Grade", a["grade"]))
            table = "".join(f"<tr><th class='cell c0'>{k}</th><td class='cell c1'>{v}</td></tr>" for k, v in rows)
            body = (
                f"<h2>{a['name']}</h2>"
                f"<div class='activity-dates' data-region='activity-dates'>"
                f"<div><strong>Opened:</strong> {moodle_date(a['opens'])}</div>"
                f"<div><strong>Due:</strong> {moodle_date(a['due'])}</div></div>"
                f"<div class='activity-description' id='intro'><p>Solve all questions and upload a PDF.</p>"
                f"{files_html(cmid, 'mod_assign/introattachment/0')}</div>"
                f"<h3>Submission status</h3><table class='generaltable submissionstatustable'>{table}</table>"
                f"<div class='fileuploadsubmission'><a href='/pluginfile.php/55/assignsubmission_file/submission_files/1/mine.pdf'>mine.pdf</a></div>"
            )
            return fake._page(body, a["name"])

        @app.get("/mod/quiz/view.php")
        def quiz():
            cmid = int(request.args["id"])
            a = fake.activities[cmid]
            verb_open = "Opened" if a["opens"] <= datetime.now(TZ) else "Opens"
            verb_close = "Closed" if a["close"] <= datetime.now(TZ) else "Closes"
            body = (
                f"<h2>{a['name']}</h2><div class='activity-dates'>"
                f"<div><strong>{verb_open}:</strong> {moodle_date(a['opens'])}</div>"
                f"<div><strong>{verb_close}:</strong> {moodle_date(a['close'])}</div></div>"
                f"<div class='box quizinfo'><p>Attempts allowed: 1</p><p>Time limit: 20 mins</p><p>Grading method: Highest grade</p></div>"
            )
            if a.get("attempted"):
                body += (f"<h3>Summary of your previous attempts</h3><table><tr><th>State</th><td>Finished Submitted</td></tr></table>"
                         f"<p>Your final grade for this quiz is {a['grade']}.</p><p>No more attempts are allowed</p>")
            else:
                body += "<button>Attempt quiz</button>"
            return fake._page(body, a["name"])

        @app.get("/mod/resource/view.php")
        def resource():
            cmid = int(request.args["id"])
            name = fake.activities[cmid]["files"][0]
            return redirect(f"/pluginfile.php/77/mod_resource/content/1/{name}")

        @app.get("/mod/forum/view.php")
        def forum():
            cmid = int(request.args["id"])
            rows = "".join(
                f"<tr class='discussion'><th><a href='/mod/forum/discuss.php?d={did}'>{d['subject']}</a></th>"
                f"<td>Teacher</td><td>{moodle_date(d['posted'])}</td></tr>"
                for did, d in fake.discussions.items() if d["forum"] == cmid
            )
            return fake._page(f"<h2>Announcements</h2><table>{rows}</table>", "Announcements")

        @app.get("/mod/forum/discuss.php")
        def discuss():
            d = fake.discussions[int(request.args["d"])]
            return fake._page(f"<h3>{d['subject']}</h3><div class='author'><a href='/user/view.php?id=2'>Dr. Teacher</a></div>"
                              f"<div class='post-content-container'>{d['body']}</div>")

        @app.get("/pluginfile.php/<path:rest>")
        def pluginfile(rest: str):
            name = rest.rsplit("/", 1)[-1]
            content = f"%PDF-1.4 fake content of {name}\n".encode() + b"0" * 2048
            ctype = {
                "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "zip": "application/zip", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            }.get(name.rsplit(".", 1)[-1], "application/octet-stream")
            response: Response = make_response(content)
            response.headers["Content-Type"] = ctype
            response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
            return response

        return app
