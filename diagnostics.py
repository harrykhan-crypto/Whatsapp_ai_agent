"""Live checks against YOUR real LMS and WhatsApp. Run on your own computer.

    python diagnostics.py lms        # browser, login, courses, assignments, quizzes, files, download
    python diagnostics.py whatsapp   # sends a test message + test document to you
    python diagnostics.py all

Stop app.py first: only one program can use the browser profile / WhatsApp key at a time.
A report is written to storage/diagnostics_report.txt (no passwords or cookies are included).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from app import build
from config import settings
from logging_setup import setup_logging
from timeutil import human_when

REPORT = settings.database_path.parent / "diagnostics_report.txt"
results: list[tuple[str, bool, str]] = []
lines: list[str] = []


def out(text: str = "") -> None:
    print(text)
    lines.append(text)


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    out(f"[{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")
    return ok


def lms_checks(c) -> None:
    out("\n=== LMS ===")
    c.worker.start()
    check("Browser starts with the saved profile", c.worker.alive, str(settings.profile_dir))
    status = c.lms.session_status()
    if not status["signed_in"]:
        out("\nMoodle shows the login page. Sign in yourself in the opened browser window,")
        input("then press ENTER here to continue… ")
        status = c.lms.session_status()
    if not check("Moodle session is signed in", status["signed_in"], status["url"]):
        return

    result = c.lms.sync(force_details=True)
    check("Full LMS sync", result.ok, result.error or f"{result.courses} courses, {result.activities} activities")
    courses = c.repo.courses()
    names = [x["name"] for x in courses]
    out("\nCourses found:\n" + "\n".join(f"  • {n} (id {x['id']})" for n, x in zip(names, courses)))
    for expected in settings.expected_courses:
        found = any(expected.lower() in n.lower() or n.lower() in expected.lower() for n in names)
        check(f"Course present: {expected}", found)

    activities = c.repo.activities(categories=None, with_files=True)
    counts = Counter(a.category for a in activities)
    out("\nActivities by type: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    tracked = [a for a in activities if a.category in ("assignment", "quiz", "lab", "task")]
    check("Assignments/quizzes/labs/tasks discovered", bool(tracked), f"{len(tracked)} tracked")
    with_deadline = [a for a in tracked if a.deadline]
    check("Deadlines extracted", bool(with_deadline), f"{len(with_deadline)}/{len(tracked)} have a deadline")
    with_status = [a for a in tracked if a.status and a.status != "unknown"]
    check("Submission/attempt status extracted", bool(with_status), f"{len(with_status)}/{len(tracked)} have a status")

    out("\nSample (first 15 by deadline):")
    for a in sorted(tracked, key=lambda x: (x.deadline is None, x.deadline or 0))[:15]:
        extra = f" | attempts {a.attempts_allowed}" if a.attempts_allowed else ""
        out(f"  [{a.category}] {a.short_course} — {a.name} | due {human_when(a.deadline)} | {a.status}"
            f"{' | grade ' + a.grade if a.grade else ''}{extra} | files {len(a.files)}")
    missing = [a for a in tracked if not a.deadline]
    if missing:
        out("\nNo deadline found for (check these pages manually):")
        for a in missing[:10]:
            out(f"  • {a.short_course} — {a.name}: {a.url}")

    files = c.repo.all_files()
    check("Downloadable files discovered", bool(files), f"{len(files)} files")
    if files:
        smallest = sorted(files, key=lambda f: (not f["filename"].lower().endswith(".pdf"), f["id"]))[0]
        try:
            info = c.lms.download_file(smallest["id"])
            data = Path(info["path"]).read_bytes()[:200]
            check("Real file download", not data.lstrip().lower().startswith(b"<!doctype html"),
                  f"{info['filename']} → {info['path']} ({info['size_bytes']} bytes)")
        except Exception as error:  # noqa: BLE001
            check("Real file download", False, str(error))
    check("Announcements read", True, f"{len(c.repo.announcements(50))} stored")
    out("\n" + c.reports.full_report())


def whatsapp_checks(c) -> None:
    out(f"\n=== WhatsApp ({c.messenger.name}) ===")
    m = c.messenger
    if m.name == "agent" and not m.recipient:
        try:
            m.client.discover_recipient()
            if m.client.recipient:
                c.repo.set_pref("agent_recipient", m.client.recipient)
        except Exception as error:  # noqa: BLE001
            check("Find WhatsApp recipient", False, str(error))
            return
    try:
        m.send_text("✅ HARRY diagnostics: WhatsApp text delivery works.")
        check("WhatsApp text message", True)
    except Exception as error:  # noqa: BLE001
        check("WhatsApp text message", False, str(error))
        if m.name == "agent":
            out("Tip: send any message (e.g. 'menu') to your HARRY agent in WhatsApp once, then rerun.")
        return
    files = [f for f in c.repo.all_files() if f.get("local_path") and Path(f["local_path"]).exists()]
    if files:
        path, name = Path(files[0]["local_path"]), files[0]["filename"]
    else:
        path = settings.database_path.parent / "harry_test_document.txt"
        path.write_text("HARRY diagnostics test document.\n", encoding="utf-8")
        name = path.name
    try:
        m.send_document(path, filename=name, caption="📎 HARRY diagnostics: document delivery works.")
        check("WhatsApp document message", True, name)
    except Exception as error:  # noqa: BLE001
        check("WhatsApp document message", False, str(error))
    try:
        m.send_menu(__import__("whatsapp.formatter", fromlist=["MENU"]).MENU)
        check("WhatsApp menu", True)
    except Exception as error:  # noqa: BLE001
        check("WhatsApp menu", False, str(error))


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    setup_logging(settings.log_dir, secrets=[settings.agent_api_key, settings.cloud_token, settings.cloud_app_secret,
                                                     settings.moodle_password])
    c = build(settings, with_browser=mode in ("lms", "all"))
    try:
        if mode in ("lms", "all"):
            lms_checks(c)
        if mode in ("whatsapp", "all"):
            whatsapp_checks(c)
    finally:
        if c.worker:
            c.worker.stop()
    passed = sum(1 for _, ok, _ in results if ok)
    out(f"\n=== {passed}/{len(results)} checks passed ===")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report saved to {REPORT}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
