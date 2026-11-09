"""WhatsApp text formatting (WhatsApp supports *bold*, _italic_ and emoji)."""

from __future__ import annotations

from database.models import Activity
from timeutil import human_remaining, human_when, now

UNIVERSITY = "🎓 *ABASYN UNIVERSITY*"

STATUS_LABELS = {
    "submitted": "✅ Submitted",
    "attempted": "✅ Attempted",
    "not_required": "✅ Nothing to submit",
    "not_submitted": "❌ Not submitted",
    "draft": "📝 Draft (not submitted)",
    "not_attempted": "❌ Not attempted",
    "in_progress": "⏳ Attempt in progress",
    "unknown": "❔ Status not shown",
}

CATEGORY_ICONS = {"assignment": "📄", "quiz": "📝", "lab": "🧪", "task": "📌", "resource": "📚", "forum": "📢"}
CATEGORY_TITLES = {"assignment": "Assignment", "quiz": "Quiz", "lab": "Lab", "task": "Task"}


def status_label(activity: Activity) -> str:
    label = STATUS_LABELS.get(activity.status or "unknown", activity.status_text or "❔ Status not shown")
    if activity.overdue:
        label += " ⚠️ OVERDUE"
    return label


def format_item(index: int | None, a: Activity, *, show_course: bool = True, show_files: bool = True) -> str:
    prefix = f"{index}. " if index is not None else "• "
    title = f"{a.short_course} — {a.name}" if show_course else a.name
    lines = [f"{prefix}{CATEGORY_ICONS.get(a.category, '•')} *{title}*"]
    if a.category == "quiz" or a.modname == "quiz":
        if a.opens_at:
            lines.append(f"   🟢 Opens: {human_when(a.opens_at)}")
        if a.deadline:
            lines.append(f"   🔒 Closes: {human_when(a.deadline)} ({human_remaining(a.deadline)})")
        if a.time_limit:
            lines.append(f"   ⏱️ Time limit: {a.time_limit}")
        if a.attempts_allowed:
            lines.append(f"   🔁 Attempts: {a.attempts_allowed}")
    else:
        if a.deadline:
            lines.append(f"   ⏰ Deadline: {human_when(a.deadline)} ({human_remaining(a.deadline)})")
        else:
            lines.append("   ⏰ Deadline: not set on LMS")
        if a.opens_at and a.opens_at > now():
            lines.append(f"   🟢 Opens: {human_when(a.opens_at)}")
    lines.append(f"   📌 Status: {status_label(a)}")
    if a.grade:
        lines.append(f"   🏅 Grade: {a.grade}")
    if show_files and a.files:
        names = ", ".join(f["filename"] for f in a.files[:3])
        more = f" +{len(a.files) - 3} more" if len(a.files) > 3 else ""
        lines.append(f"   📎 Files: {names}{more}")
    return "\n".join(lines)


def format_list(title: str, items: list[Activity], *, empty: str, numbered: bool = True, footer: str = "") -> str:
    if not items:
        return f"{title}\n\n{empty}"
    body = "\n\n".join(format_item(i if numbered else None, a) for i, a in enumerate(items, 1))
    return f"{title}\n\n{body}" + (f"\n\n{footer}" if footer else "")


MENU = (
    "🎓 *HARRY — Abasyn LMS Assistant*\n\n"
    "Reply with a number or command:\n"
    "1️⃣ /today — Due today\n"
    "2️⃣ /tomorrow — Due tomorrow\n"
    "3️⃣ /upcoming — Everything upcoming\n"
    "4️⃣ /assignments — All assignments\n"
    "5️⃣ /quizzes — Upcoming quizzes\n"
    "6️⃣ /labs — Lab tasks & reports\n"
    "7️⃣ /tasks — Weekly tasks & activities\n"
    "8️⃣ /overdue — Missing & overdue work\n"
    "9️⃣ /courses — Enrolled courses\n"
    "🔟 /files — Available files\n"
    "1️⃣1️⃣ /report — Full academic report\n"
    "1️⃣2️⃣ /announcements — Course announcements\n\n"
    "You can also ask naturally, e.g.\n"
    "• _Kal kya submit karna hai?_\n"
    "• _Next week ke quizzes batao_\n"
    "• _Automata ka assignment bhejo_\n"
    "• _Send me all pending assignment files_"
)

HELP = (
    "🤖 *HARRY commands*\n\n"
    "/menu — main menu\n"
    "/today — everything due today\n"
    "/tomorrow — tomorrow's deadlines\n"
    "/upcoming — all upcoming activities\n"
    "/assignments [course] — assignments with deadlines\n"
    "/quizzes [course] — upcoming quizzes\n"
    "/labs [course] — lab tasks & lab reports\n"
    "/tasks [course] — weekly tasks & other activities\n"
    "/overdue — missing & overdue submissions\n"
    "/pending — everything not yet submitted\n"
    "/courses — enrolled courses\n"
    "/files [course] — available files (with download codes)\n"
    "/download <code | activity> — send the actual file(s)\n"
    "/report — complete academic report\n"
    "/announcements — latest course announcements\n"
    "/grades — grades visible on the LMS\n"
    "/sync — check the LMS right now\n"
    "/status — system & LMS session status\n"
    "/notify on|off — automatic notifications\n\n"
    "English and Roman Urdu both work: _\"Is week ke labs show karo\"_, "
    "_\"Psychology ki file download kar ke bhejo\"_."
)
