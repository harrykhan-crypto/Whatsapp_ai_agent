"""Turns a WhatsApp message into answers built from real LMS data."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from database.models import Activity, Repository
from lms.authentication import SessionExpired
from lms.file_downloader import DownloadError
from services import intent as nlu
from services.monitoring import LmsService
from services.report_generator import ReportGenerator
from timeutil import now
from whatsapp.formatter import HELP, MENU

log = logging.getLogger(__name__)

MAX_FILES_PER_REQUEST = 10
SESSION_EXPIRED_TEXT = (
    "🔐 *Moodle login needed*\n\nYour LMS session has expired. Please open the HARRY browser window on your "
    "computer, sign in to Moodle yourself (HARRY never asks for your password), then send /sync."
)


@dataclass
class Reply:
    kind: str                      # text | document | menu
    text: str = ""
    path: str = ""
    filename: str = ""
    mime_type: str | None = None
    caption: str | None = None


@dataclass
class HandlerResult:
    replies: list[Reply] = field(default_factory=list)

    def text(self, body: str) -> None:
        self.replies.append(Reply("text", text=body))

    @property
    def combined_text(self) -> str:
        return "\n\n".join(r.text or f"[document] {r.filename}" for r in self.replies)


class CommandHandler:
    def __init__(self, repo: Repository, lms: LmsService | None, reports: ReportGenerator, *,
                 stale_minutes: int = 45, student_name: str = "", ai=None):
        self.repo = repo
        self.lms = lms
        self.reports = reports
        self.stale_minutes = stale_minutes
        self.student_name = student_name
        self.ai = ai   # optional OllamaIntentParser (free local AI)

    # ------------------------------------------------------------------ entry
    def handle(self, text: str, progress: Callable[[str], None] | None = None) -> HandlerResult:
        result = HandlerResult()
        progress = progress or (lambda _msg: None)
        try:
            courses = self.repo.courses()
            intent = nlu.parse(text, courses)
            if intent.action == "unknown" and self.ai is not None and self.ai.available():
                progress("🤔 Samajh raha hoon… (thinking)")
                understood = self.ai.parse(text, courses)   # free local AI, only when the rules don't understand
                if understood is not None:
                    intent = understood
            intent.terms = self._meaningful_terms(intent.terms)
            log.info("Intent: action=%s period=%s cats=%s course=%s pending=%s files=%s terms=%s",
                     intent.action, intent.period, intent.categories, intent.course_name,
                     intent.pending_only, intent.want_files, intent.terms)
            self._dispatch(intent, result, progress)
        except SessionExpired:
            result.text(SESSION_EXPIRED_TEXT)
        except Exception:  # noqa: BLE001
            log.exception("Command failed: %r", text)
            result.text("⚠️ Sorry, something went wrong while reading your LMS. I've logged the error; please try again in a minute.")
        return result

    # --------------------------------------------------------------- dispatch
    def _dispatch(self, intent: nlu.Intent, result: HandlerResult, progress) -> None:
        action = intent.action
        if action in {"menu"}:
            result.replies.append(Reply("menu", text=MENU))
            return
        if action == "help":
            result.text(HELP)
            return
        if action == "greeting":
            result.text(f"{self._greeting(intent)}\n\nHow can I help? Here is the menu 👇")
            result.replies.append(Reply("menu", text=MENU))
            return
        if action == "unknown_command":
            result.text("I don't know that command. Send /help to see everything I can do.")
            return
        if action == "status":
            result.text(self._status())
            return
        if action == "notify":
            result.text(self._notify(intent.argument))
            return
        if action == "history":
            result.text(self._history())
            return
        if action == "sync":
            progress("⏳ Checking your LMS now… this can take 1–3 minutes.")
            outcome = self.lms.sync(force_details=True) if self.lms else None
            if outcome and outcome.session_expired:
                result.text(SESSION_EXPIRED_TEXT)
            elif outcome and outcome.ok:
                result.text(f"✅ LMS synced: {outcome.courses} courses, {outcome.activities} activities "
                            f"({outcome.new_activities} new, {outcome.changes} changes).")
            else:
                result.text(f"⚠️ LMS sync failed: {outcome.error if outcome else 'LMS reader is not running'}")
            return

        # Everything below reads LMS data: make sure it is fresh enough.
        if not self._ensure_fresh(result, progress):
            return
        greeting = self._greeting(intent) if intent.greeting else ""
        if intent.ambiguous_courses and not intent.course_id:
            names = ", ".join(n.split(" (", 1)[0] for n in intent.ambiguous_courses)
            result.text(f"ℹ️ Several courses match ({names}). I'm showing all of them — mention the course more specifically to narrow it.")

        simple = {
            "today": dict(period="today"), "tomorrow": dict(period="tomorrow"), "upcoming": dict(period="upcoming"),
            "week": dict(period="week"), "next_week": dict(period="next_week"),
            "assignments": dict(categories=["assignment"]), "quizzes": dict(categories=["quiz"], period="upcoming"),
            "labs": dict(categories=["lab"]), "tasks": dict(categories=["task"]), "pending": dict(pending_only=True),
        }
        if action in simple:
            options = {"period": None, "categories": [], "pending_only": False, **simple[action]}
            result.text(self.reports.answer(course_id=intent.course_id, course_name=intent.course_name,
                                            greeting=greeting, **options))
        elif action == "overdue":
            result.text(self.reports.overdue(intent.course_id))
        elif action == "courses":
            result.text(self.reports.courses())
        elif action == "report":
            result.text(self.reports.full_report(greeting=greeting))
        elif action == "announcements":
            result.text(self.reports.announcements(intent.course_id))
        elif action == "grades":
            result.text(self.reports.grades())
        elif action == "files":
            result.text(self._files_list(intent.course_id))
        elif action == "download":
            self._download(intent, result, progress)
        elif action == "query" and intent.done_only:
            from whatsapp.formatter import format_list
            done = [a for a in self.reports.select(period=intent.period, categories=intent.categories,
                                                   course_id=intent.course_id) if a.done]
            title = "✅ *SUBMITTED / COMPLETED*" + (f"\n📘 {intent.course_name}" if intent.course_name else "")
            result.text(format_list(title, done, empty="Nothing completed found yet."))
        elif action == "query":
            if intent.terms or intent.numbers:
                matches = self._search_activities(intent, categories=intent.categories or None)
                if matches and not intent.period:
                    title = "🔎 *Matching LMS activities*"
                    from whatsapp.formatter import format_list
                    result.text((greeting + "\n\n" if greeting else "") + format_list(title, matches[:10], empty=""))
                    return
            result.text(self.reports.answer(
                period=intent.period, categories=intent.categories, course_id=intent.course_id,
                course_name=intent.course_name, pending_only=intent.pending_only, greeting=greeting,
            ))
        else:
            result.text(
                "🤔 I didn't understand that. Try:\n• _Kal kya submit karna hai?_\n• _Next week ke quizzes batao_\n"
                "• _Send me HCI assignment 2_\n\nOr send /menu."
            )

    # ---------------------------------------------------------------- helpers
    def _meaningful_terms(self, terms: list[str]) -> list[str]:
        """Keep only words that appear in your activity or file names, so stray words never block an answer."""
        if not terms:
            return []
        vocab: set[str] = set()
        for a in self.repo.activities(categories=None):
            vocab.update(re.findall(r"[a-z]{3,}", a.name.lower()))
        for f in self.repo.all_files():
            vocab.update(re.findall(r"[a-z]{3,}", f["filename"].lower()))
        kept = []
        for term in terms:
            if term in vocab or (len(term) >= 4 and any(w.startswith(term) or term.startswith(w) for w in vocab if len(w) >= 4)):
                kept.append(term)
        return kept

    def _greeting(self, intent: nlu.Intent) -> str:
        name = f" {self.student_name}" if self.student_name else ""
        if re.search(r"assalam|salam|aoa", intent.raw, re.I):
            return f"Wa Alaikum Assalam{name}! 👋"
        return f"Hello{name}! 👋"

    def _ensure_fresh(self, result: HandlerResult, progress) -> bool:
        """Answer from saved data immediately; refresh in the background when it is getting old.

        Only the very first time (no data saved yet) do we wait for the LMS.
        """
        if self.lms is None:
            return True
        age = self.lms.data_age_minutes()
        if age is None:
            progress("⏳ First-time LMS read in progress — this happens only once and takes a few minutes. "
                     "I'll answer as soon as it's done.")
            outcome = self.lms.sync()
            if outcome.session_expired:
                result.text(SESSION_EXPIRED_TEXT)
                return False
            if not outcome.ok and self.lms.data_age_minutes() is None:
                result.text(f"⚠️ I couldn't read your LMS yet: {outcome.error}")
                return False
            return True
        if self.repo.get_pref("session_state") == "expired":
            self.lms.sync_in_background()   # notices automatically when you have logged in again
            result.text("🔐 Your Moodle login has expired, so this is the last saved information. "
                        "Log in again in the HARRY browser window on your PC.")
            return True
        if age > self.stale_minutes:
            self.lms.sync_in_background()
        return True

    def _status(self) -> str:
        last = self.repo.last_sync(ok_only=False)
        ok = self.repo.last_sync()
        c = self.reports.counts()
        lines = ["🩺 *HARRY STATUS*", ""]
        session = self.repo.get_pref("session_state") or "unknown"
        lines.append(f"🔐 Moodle session: {'✅ signed in' if session == 'ok' else '❌ expired — please sign in' if session == 'expired' else '❔ not checked yet'}")
        if self.lms is not None:
            lines.append(f"🌐 Browser: {'✅ running' if self.lms.worker.alive else '❌ not running'}"
                         f"{' (syncing now)' if self.lms.syncing else ''}")
        if ok:
            lines.append(f"🔄 Last successful sync: {ok['finished_at'][:16].replace('T', ' ')}")
        if last and not last["ok"]:
            lines.append(f"⚠️ Last attempt failed: {(last['error'] or '')[:150]}")
        lines.append(f"📚 Courses: {len(self.repo.courses())}")
        lines.append(f"📋 Tracked activities: {c['total']} (pending {c['pending']}, overdue {c['overdue']}, done {c['completed']})")
        lines.append(f"🔔 Notifications: {'on' if self.repo.get_pref('notifications', 'on') == 'on' else 'off'}")
        return "\n".join(lines)

    def _notify(self, argument: str) -> str:
        value = argument.strip().lower()
        if value in {"on", "off"}:
            self.repo.set_pref("notifications", value)
            return f"🔔 Automatic notifications turned *{value}*."
        state = self.repo.get_pref("notifications", "on")
        return f"🔔 Automatic notifications are *{state}*. Send /notify on or /notify off."

    def _history(self) -> str:
        rows = self.repo.notification_history(10)
        if not rows:
            return "No notifications sent yet."
        return "🧾 *Recent notifications*\n\n" + "\n".join(
            f"• {r['created_at'][:16].replace('T', ' ')} — {r['kind']} ({r['status']})" for r in rows)

    # ------------------------------------------------------------------ files
    def _files_list(self, course_id: int | None) -> str:
        files = self.repo.all_files(course_id)
        if not files:
            return "📂 No downloadable files were found on your LMS course pages."
        lines, current = ["📂 *AVAILABLE LMS FILES*", "Send /download <code> to receive a file, e.g. /download F%s" % files[0]["id"]], None
        shown = 0
        for f in files:
            if shown >= 60:
                lines.append(f"\n…and {len(files) - shown} more. Add a course name, e.g. /files HCI")
                break
            course = (f.get("course_name") or "Course").split(" (", 1)[0]
            if course != current:
                lines.append(f"\n📘 *{course}*")
                current = course
            lines.append(f"F{f['id']} · {f['filename']}" + (f"  _({f['activity_name']})_" if f.get("activity_name") else ""))
            shown += 1
        return "\n".join(lines)

    def _search_activities(self, intent: nlu.Intent, categories=None) -> list[Activity]:
        found = self._search(intent.terms, intent.numbers, intent.course_id, categories)
        if not found and intent.terms and (intent.numbers or intent.course_id):
            # Unrecognised words should not hide an otherwise clear match ("HCI assignment 2 plzz").
            found = self._search([], intent.numbers, intent.course_id, categories)
        return found

    def _search(self, terms: list[str], numbers: list[str], course_id: int | None, categories=None) -> list[Activity]:
        cats = categories or ["assignment", "quiz", "lab", "task", "resource"]
        items = self.repo.activities(categories=cats, course_id=course_id, with_files=True)
        scored = []
        for a in items:
            haystack = f"{a.name} {' '.join(f['filename'] for f in a.files)}".lower()
            name_numbers = {str(int(n)) for n in re.findall(r"\d+", a.name)}
            score = 0
            if numbers:
                if not any(n in name_numbers for n in numbers):
                    continue
                score += 3
            term_score = 0
            for term in terms:
                if term in haystack:
                    term_score += 2
                elif len(term) >= 4 and any(w.startswith(term[:4]) for w in re.findall(r"[a-z]+", haystack)):
                    term_score += 1
            if terms and term_score == 0:
                continue
            score += term_score
            scored.append((score, a))
        if not scored:
            return []
        top = max(s for s, _ in scored)
        best = [a for s, a in scored if s == top]
        best.sort(key=lambda a: (a.done, a.deadline is None, a.deadline or now()), reverse=False)
        return best

    def _with_files(self, activities: list[Activity]) -> list[Activity]:
        return [a for a in activities if self.repo.files_for_activity(a.id)]

    def _search_files(self, intent: nlu.Intent) -> list[dict]:
        """Match words/numbers directly against file names (e.g. 'lecture 5 slides')."""
        matches = []
        for f in self.repo.all_files(intent.course_id):
            name = f["filename"].lower()
            numbers = {str(int(n)) for n in re.findall(r"\d+", name)}
            if intent.numbers and not all(n in numbers for n in intent.numbers):
                continue
            if intent.terms and not any(t in name for t in intent.terms):
                continue
            if intent.terms or intent.numbers:
                matches.append(f)
        return matches

    def _download(self, intent: nlu.Intent, result: HandlerResult, progress) -> None:
        argument = intent.argument.strip()
        code = re.fullmatch(r"[fF]?(\d+)", argument) if argument else None
        if code:
            record = self.repo.file(int(code.group(1)))
            if not record:
                result.text(f"❌ There is no file with code {argument}. Send /files to see the codes.")
                return
            self._send_files([record], result, progress, intro=f"✅ Found *{record['filename']}*. Sending…")
            return

        categories = list(intent.categories) or None
        tracked = categories or ["assignment", "quiz", "lab", "task"]
        specific = bool(intent.terms or intent.numbers)
        where = f" in {intent.course_name}" if intent.course_name else ""
        activities: list[Activity] = []

        what = " & ".join(categories) + "s" if categories else "work"
        if intent.overdue_only or intent.pending_only:
            # "sirf overdue files" -> ONLY overdue work; "pending files" -> everything not submitted yet.
            activities = self.reports.select(categories=tracked, course_id=intent.course_id, period=intent.period,
                                             pending_only=True, overdue_only=intent.overdue_only)
            label = ("overdue " if intent.overdue_only else "pending ") + what
        elif intent.period and not specific:
            activities = self.reports.select(categories=tracked, course_id=intent.course_id, period=intent.period,
                                             include_done=not intent.pending_only)
            label = {"today": "work due today", "tomorrow": "work due tomorrow", "day_after": "work due the day after tomorrow",
                     "week": "work due in the next 7 days", "next_week": "work due next week",
                     "upcoming": "upcoming work", "month": "work due this month"}.get(intent.period, "matching work")
            if categories:
                label = label.replace("work", what)
        elif intent.latest and not specific:
            recent = self._with_files(self.repo.activities(categories=categories or None, course_id=intent.course_id))
            recent.sort(key=lambda a: (a.first_seen or "", a.opens_at or now()), reverse=True)
            activities = recent[:1]
            label = "the latest " + (what if categories else "activity with a file")
        elif intent.all_items and not specific and (intent.course_id or categories):
            activities = self._with_files(self.repo.activities(categories=categories or None, course_id=intent.course_id))
            activities.sort(key=lambda a: (a.deadline is None, a.deadline or now()))
            label = f"all {what}" if categories else "all activities"
            if sum(len(self.repo.files_for_activity(a.id)) for a in activities) > MAX_FILES_PER_REQUEST:
                result.text("📂 That's a lot of files, so here is the full list. Send /download <code> for the ones you want.\n\n"
                            + self._files_list(intent.course_id))
                return
        elif specific:
            activities = self._search_activities(intent, categories=(categories + ["resource"]) if categories else None)
            label = "matching activities"
            if len(activities) == 1 and not self.repo.files_for_activity(activities[0].id) and self.lms is not None:
                try:
                    self.lms.refresh_activity_files(activities[0])   # the teacher may have just uploaded it
                except SessionExpired:
                    raise
                except Exception as error:  # noqa: BLE001
                    log.info("Could not refresh files for %s: %s", activities[0].name, error)
            if not self._with_files(activities):
                direct = self._search_files(intent)
                if direct:
                    self._send_files(direct[:MAX_FILES_PER_REQUEST], result, progress,
                                     intro=f"✅ Found {len(direct)} matching file{'s' if len(direct) != 1 else ''}. Sending…")
                    return
        else:
            # Nothing specific: the files you most likely need = nearest pending work (with attachments).
            pending = self._with_files(self.reports.select(categories=tracked, course_id=intent.course_id, pending_only=True))
            if pending:
                activities = pending[:3]
                label = "your nearest pending work"
            else:
                recent = self._with_files(self.repo.activities(categories=categories or None, course_id=intent.course_id))
                recent.sort(key=lambda a: (a.deadline is not None, a.deadline or now(), a.first_seen or ""), reverse=True)
                activities = recent[:2]
                label = "the latest activities"

        with_files = self._with_files(activities)
        if not with_files:
            if activities:
                names = "\n".join(f"• {a.short_course} — {a.name}" for a in activities[:8])
                result.text(f"📭 I found {label}{where}, but the teacher hasn't attached any file to them on the LMS:\n{names}"
                            "\n\nSend /files to see every file that is available.")
            else:
                result.text(f"❌ I couldn't find {label}{where} on your LMS. Send /files to see every available file.")
            return

        files: list[dict] = []
        for a in with_files:
            for f in self.repo.files_for_activity(a.id):
                f["activity_name"], f["course_name"] = a.name, a.course_name
                files.append(f)
        total = len(files)
        files = files[:MAX_FILES_PER_REQUEST]
        if len(with_files) == 1:
            a = with_files[0]
            intro = f"✅ Found *{a.short_course} — {a.name}*. Sending {len(files)} file{'s' if len(files) != 1 else ''}…"
        else:
            intro = f"✅ Sending {len(files)} file{'s' if len(files) != 1 else ''} from {label}…"
        if total > len(files):
            intro += f"\n(showing {len(files)} of {total}; send /files for the full list)"
        self._send_files(files, result, progress, intro=intro)
        missing = [a for a in activities if a not in with_files]
        if missing and len(activities) > 1:
            result.text("ℹ️ No attachment on LMS for: " + ", ".join(f"{a.short_course} — {a.name}" for a in missing[:6]))

    def _send_files(self, files: list[dict], result: HandlerResult, progress, intro: str) -> None:
        result.text(intro)
        failures = []
        for f in files:
            try:
                info = self.lms.download_file(f["id"]) if self.lms else None
                if not info:
                    raise DownloadError("LMS reader is not running.")
                course = (f.get("course_name") or info.get("course") or "").split(" (", 1)[0]
                caption = f"📎 {course} — {f.get('activity_name') or info.get('activity') or ''}".strip(" —")
                result.replies.append(Reply("document", path=info["path"], filename=info["filename"],
                                            mime_type=info.get("mime_type"), caption=caption))
            except SessionExpired:
                raise
            except Exception as error:  # noqa: BLE001
                log.warning("Download failed for %s: %s", f.get("filename"), error)
                failures.append(f"{f.get('filename')}: {error}")
        if failures:
            result.text("⚠️ Some files could not be downloaded:\n" + "\n".join(f"• {x}" for x in failures))
