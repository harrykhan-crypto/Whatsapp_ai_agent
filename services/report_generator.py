"""Builds WhatsApp answers and reports from the activities stored in SQLite."""

from __future__ import annotations

from datetime import datetime, timedelta

from database.models import Activity, Repository
from timeutil import day_bounds, human_remaining, human_when, now, today
from whatsapp.formatter import (
    CATEGORY_TITLES,
    UNIVERSITY,
    format_item,
    format_list,
)

PERIOD_TITLES = {
    "today": "🔴 *DUE TODAY*",
    "tomorrow": "🟠 *DUE TOMORROW*",
    "day_after": "🟡 *DUE DAY AFTER TOMORROW*",
    "week": "🗓️ *DUE IN THE NEXT 7 DAYS*",
    "next_week": "🗓️ *DUE NEXT WEEK*",
    "month": "🗓️ *DUE IN THE NEXT 30 DAYS*",
    "upcoming": "🟠 *UPCOMING*",
}


def period_bounds(period: str | None) -> tuple[datetime, datetime] | None:
    current = now()
    if period == "today":
        start, end = day_bounds(today())
        return start, end
    if period == "tomorrow":
        return day_bounds(today() + timedelta(days=1))
    if period == "day_after":
        return day_bounds(today() + timedelta(days=2))
    if period == "week":
        return current, day_bounds(today() + timedelta(days=6))[1]
    if period == "next_week":
        monday = today() + timedelta(days=7 - today().weekday())
        return day_bounds(monday)[0], day_bounds(monday + timedelta(days=6))[1]
    if period == "month":
        return current, current + timedelta(days=30)
    if period == "upcoming":
        return current, current + timedelta(days=3650)
    return None


def _sort_key(a: Activity):
    return (a.deadline is None, a.deadline or datetime.max.replace(tzinfo=now().tzinfo), a.short_course, a.name)


def _in_window(a: Activity, bounds: tuple[datetime, datetime]) -> bool:
    start, end = bounds
    for moment in (a.deadline, a.opens_at if a.category == "quiz" else None):
        if moment and start <= moment < end:
            return True
    return False


class ReportGenerator:
    def __init__(self, repo: Repository):
        self.repo = repo

    # ---------------------------------------------------------------- selection
    def select(self, *, period: str | None = None, categories: list[str] | None = None, course_id: int | None = None,
               pending_only: bool = False, overdue_only: bool = False, include_done: bool = True) -> list[Activity]:
        items = self.repo.activities(categories=categories or None, course_id=course_id, with_files=True)
        if not categories:
            items = [a for a in items if a.category in ("assignment", "quiz", "lab", "task")]
        bounds = period_bounds(period)
        if bounds:
            items = [a for a in items if _in_window(a, bounds)]
        if overdue_only:
            items = [a for a in items if a.overdue]
        elif pending_only:
            items = [a for a in items if not a.done and (a.overdue or a.upcoming or a.deadline is None)]
        if not include_done:
            items = [a for a in items if not a.done]
        return sorted(items, key=_sort_key)

    def counts(self, course_id: int | None = None) -> dict[str, int]:
        items = self.repo.activities(course_id=course_id)
        week_end = now() + timedelta(days=7)
        return {
            "pending": sum(1 for a in items if not a.done and not a.overdue and (a.deadline is None or a.deadline >= now())),
            "upcoming": sum(1 for a in items if not a.done and a.deadline and now() <= a.deadline <= week_end),
            "overdue": sum(1 for a in items if a.overdue),
            "completed": sum(1 for a in items if a.done),
            "total": len(items),
        }

    # ------------------------------------------------------------------ answers
    def answer(self, *, period: str | None, categories: list[str], course_id: int | None, course_name: str | None,
               pending_only: bool, greeting: str = "") -> str:
        items = self.select(period=period, categories=categories, course_id=course_id, pending_only=pending_only)
        what = " & ".join(CATEGORY_TITLES.get(c, c).upper() + ("ZES" if c == "quiz" else "S") for c in categories) or "ACADEMIC WORK"
        title = PERIOD_TITLES.get(period or "", "")
        if period:
            if categories:
                title = title.replace("DUE", f"{what} DUE") if "DUE" in title else title.replace("*UPCOMING*", f"*UPCOMING {what}*")
        else:
            title = f"📋 *{'PENDING ' if pending_only else ''}{what}*"
        if course_name:
            title += f"\n📘 {course_name}"
        if not period and not pending_only:
            # Show what matters first: open work, then finished work (briefly).
            open_items = [a for a in items if not a.done]
            done = [a for a in items if a.done]
            text = format_list(title, open_items, empty="Nothing pending 🎉")
            if done:
                text += f"\n\n✅ *Completed ({len(done)})*\n" + "\n".join(f"• {a.short_course} — {a.name}" for a in done[:15])
                if len(done) > 15:
                    text += f"\n…and {len(done) - 15} more"
        else:
            empty = {
                "today": "Nothing is due today on your LMS 🎉",
                "tomorrow": "Nothing is due tomorrow on your LMS 🎉",
            }.get(period or "", "Nothing matching was found on your LMS 🎉")
            text = format_list(title, items, empty=empty)
        if greeting:
            text = f"{greeting}\n\n{text}"
        return text + self._freshness_note()

    def overdue(self, course_id: int | None = None) -> str:
        items = self.select(course_id=course_id, overdue_only=True)
        return format_list("⚠️ *MISSING & OVERDUE WORK*", items,
                           empty="No overdue or missing submissions 🎉",
                           footer="Some assignments may still accept late submissions until their cut-off date.") + self._freshness_note()

    def courses(self) -> str:
        courses = self.repo.courses()
        if not courses:
            return "No courses stored yet. Send /sync to read your LMS."
        lines = []
        for i, course in enumerate(courses, 1):
            c = self.counts(course["id"])
            lines.append(f"{i}. 📘 *{course['name']}*\n   Pending: {c['pending']} · Overdue: {c['overdue']} · Done: {c['completed']}")
        return "🎓 *YOUR ENROLLED COURSES*\n\n" + "\n\n".join(lines) + self._freshness_note()

    def announcements(self, course_id: int | None = None, limit: int = 6) -> str:
        items = self.repo.announcements(limit=limit, course_id=course_id)
        if not items:
            return "📢 No course announcements found on your LMS."
        blocks = []
        for i, a in enumerate(items, 1):
            when = a.get("posted") or a.get("first_seen") or ""
            course = (a.get("course_name") or "").split(" (", 1)[0]
            excerpt = (a.get("excerpt") or "").strip()
            block = f"{i}. *{course} — {a['subject']}*"
            if when:
                block += f"\n   🕒 {when[:16].replace('T', ' ')}"
            if excerpt:
                block += f"\n   {excerpt[:350]}{'…' if len(excerpt) > 350 else ''}"
            blocks.append(block)
        return "📢 *COURSE ANNOUNCEMENTS*\n\n" + "\n\n".join(blocks)

    def grades(self) -> str:
        graded = [a for a in self.repo.activities() if a.grade]
        if not graded:
            return "🏅 No grades are visible on your assignment and quiz pages yet."
        graded.sort(key=lambda a: (a.short_course, a.name))
        return "🏅 *GRADES VISIBLE ON LMS*\n\n" + "\n".join(f"• {a.short_course} — {a.name}: *{a.grade}*" for a in graded)

    # ------------------------------------------------------------------ reports
    def full_report(self, title: str = "📅 *DAILY ACADEMIC REPORT*", greeting: str = "") -> str:
        items = self.select()
        due_today_bounds = period_bounds("today")
        week_end = now() + timedelta(days=7)
        used: set[int] = set()

        def take(predicate) -> list[Activity]:
            chosen = [a for a in items if a.id not in used and predicate(a)]
            used.update(a.id for a in chosen)
            return chosen

        due_today = take(lambda a: not a.done and a.deadline and _in_window(a, due_today_bounds) and not a.overdue)
        overdue = take(lambda a: a.overdue)
        quizzes = take(lambda a: a.category == "quiz" and not a.done and a.upcoming)
        labs = take(lambda a: a.category == "lab" and not a.done and a.upcoming)
        upcoming = take(lambda a: not a.done and a.deadline and now() <= a.deadline <= week_end)
        later = take(lambda a: not a.done and (a.deadline is None or a.deadline > week_end) and not a.overdue)

        sections = [UNIVERSITY, f"{title}\n🗓️ {now():%A}, {now().day} {now():%B %Y}"]
        if greeting:
            sections.insert(0, greeting)
        counter = 1

        def section(heading: str, chosen: list[Activity]) -> None:
            nonlocal counter
            if not chosen:
                return
            lines = []
            for a in chosen:
                lines.append(format_item(counter, a))
                counter += 1
            sections.append(f"{heading}\n\n" + "\n\n".join(lines))

        section("🔴 *DUE TODAY*", due_today)
        section("⚠️ *OVERDUE / MISSING*", overdue)
        section("🟠 *UPCOMING (7 DAYS)*", upcoming)
        section("📝 *QUIZZES*", quizzes)
        section("🧪 *LAB TASKS*", labs)
        if later:
            sections.append("🗂️ *LATER / NO DEADLINE*\n" + "\n".join(
                f"• {a.short_course} — {a.name} ({human_when(a.deadline, with_time=False) if a.deadline else 'no deadline'})"
                for a in later[:12]))
        if len(sections) == (3 if greeting else 2):
            sections.append("🎉 Nothing pending on your LMS right now.")

        c = self.counts()
        sections.append(
            "📊 *OVERALL SUMMARY*\n"
            f"Pending: {c['pending']}\nUpcoming (7 days): {c['upcoming']}\nOverdue: {c['overdue']}\nCompleted: {c['completed']}"
        )
        return "\n\n".join(sections) + self._freshness_note()

    def morning_summary(self) -> str:
        return self.full_report("☀️ *GOOD MORNING — TODAY'S ACADEMIC SUMMARY*")

    def evening_reminder(self) -> str:
        items = self.select(period="tomorrow", include_done=False)
        overdue = self.select(overdue_only=True)
        text = format_list("🌙 *TOMORROW'S DEADLINES*", items, empty="Nothing is due tomorrow 🎉")
        if overdue:
            text += "\n\n⚠️ *Still overdue*\n" + "\n".join(f"• {a.short_course} — {a.name}" for a in overdue[:10])
        return text + self._freshness_note()

    def reminder(self, a: Activity, hours: int) -> str:
        urgency = "⏰ *FINAL REMINDER*" if hours <= 3 else "🔔 *DEADLINE REMINDER*"
        left = human_remaining(a.deadline).replace(" left", "")
        return f"{urgency} — {left} left\n\n{format_item(None, a)}"

    def events_digest(self, events: list[dict]) -> str:
        titles = {
            "new_activity": "🆕 *NEW ON LMS*",
            "deadline_changed": "🔁 *DEADLINE CHANGED*",
            "deadline_added": "📅 *DEADLINE PUBLISHED*",
            "new_file": "📎 *NEW FILES UPLOADED*",
            "announcement": "📢 *NEW ANNOUNCEMENTS*",
            "grade_posted": "🏅 *GRADES POSTED*",
        }
        sections = []
        for kind, heading in titles.items():
            chosen = [e for e in events if e["kind"] == kind]
            if not chosen:
                continue
            lines = []
            for e in chosen[:15]:
                line = f"• *{e['title']}*"
                if e.get("detail"):
                    line += "\n   " + e["detail"].replace("\n", "\n   ")
                lines.append(line)
            if len(chosen) > 15:
                lines.append(f"…and {len(chosen) - 15} more")
            sections.append(heading + "\n" + "\n".join(lines))
        return "🎓 *LMS UPDATE*\n\n" + "\n\n".join(sections)

    def _freshness_note(self) -> str:
        last = self.repo.last_sync()
        if not last:
            return "\n\n_⚠️ LMS has not been synced yet._"
        return f"\n\n_🔄 LMS checked: {last['finished_at'][11:16]} · {last['finished_at'][:10]}_"
