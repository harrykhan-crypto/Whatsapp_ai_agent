"""LMS synchronisation engine: scrape Moodle, store it, and record what changed."""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from database.models import Activity, Repository
from lms import announcement_scraper, assignment_scraper, file_downloader, quiz_scraper
from lms.activity_detector import discover_course_activities
from lms.authentication import SessionExpired, check_session
from lms.browser import BrowserSession, BrowserWorker
from lms.course_scraper import get_courses
from timeutil import from_iso, human_when, now

log = logging.getLogger(__name__)

DETAIL_REFRESH_HOURS = 0.4          # re-read open activities every sync (~30 min)
RESOURCE_REFRESH_HOURS = 24
ANNOUNCEMENT_FORUM_RE = re.compile(r"announcement|news|notice", re.I)


@dataclass
class SyncResult:
    ok: bool
    courses: int = 0
    activities: int = 0
    new_activities: int = 0
    changes: int = 0
    baseline: bool = False
    error: str | None = None
    session_expired: bool = False
    course_names: list[str] = field(default_factory=list)


class LmsService:
    def __init__(self, worker: BrowserWorker, repo: Repository, *, download_dir: Path, max_download_bytes: int):
        self.worker = worker
        self.repo = repo
        self.download_dir = Path(download_dir)
        self.max_download_bytes = max_download_bytes
        self._sync_lock = threading.Lock()
        self.syncing = False
        self.on_session_expired: Callable[[], None] | None = None
        self.on_session_restored: Callable[[], None] | None = None

    # ---------------------------------------------------------------- status
    def session_status(self) -> dict:
        return self.worker.call(check_session, timeout=120)

    def data_age_minutes(self) -> float | None:
        last = self.repo.last_sync()
        if not last:
            return None
        finished = from_iso(last["finished_at"])
        return (now() - finished).total_seconds() / 60 if finished else None

    # ------------------------------------------------------------------ sync
    def sync(self, *, force_details: bool = False) -> SyncResult:
        """Full synchronisation. Concurrent callers wait for the running sync instead of starting another."""
        if not self._sync_lock.acquire(blocking=False):
            log.info("Sync already running; waiting for it to finish")
            with self._sync_lock:
                pass
            last = self.repo.last_sync(ok_only=False)
            return SyncResult(ok=bool(last and last["ok"]), error=None if last and last["ok"] else (last or {}).get("error"))
        self.syncing = True
        run_id = self.repo.start_sync()
        baseline = self.repo.activity_count() == 0
        result = SyncResult(ok=False, baseline=baseline)
        try:
            courses = self.worker.call(get_courses, timeout=300)
            if not courses:
                raise RuntimeError("No enrolled courses were visible on the Moodle dashboard.")
            self._session_ok()
            for course in courses:
                self.repo.upsert_course(course)
            self.repo.deactivate_missing_courses(c["id"] for c in courses)
            result.courses = len(courses)
            result.course_names = [c["name"] for c in courses]

            for course in courses:
                try:
                    stats = self.worker.call(
                        lambda session, c=course: self._sync_course(session, c, baseline, force_details),
                        timeout=1200,
                    )
                except SessionExpired:
                    raise
                except Exception as error:  # noqa: BLE001 - one bad course must not stop the others
                    log.exception("Course sync failed for %s", course["name"])
                    result.error = f"{course['name']}: {error}"
                    continue
                result.activities += stats["activities"]
                result.new_activities += stats["new"]
                result.changes += stats["changes"]
            result.ok = True
        except SessionExpired as error:
            result.error = str(error)
            result.session_expired = True
            self._session_expired()
        except Exception as error:  # noqa: BLE001
            log.exception("LMS sync failed")
            result.error = str(error)
        finally:
            self.repo.finish_sync(run_id, result.ok, courses=result.courses, activities=result.activities,
                                  new_activities=result.new_activities, changes=result.changes, error=result.error)
            self.syncing = False
            self._sync_lock.release()
        log.info("Sync finished: ok=%s courses=%s activities=%s new=%s changes=%s",
                 result.ok, result.courses, result.activities, result.new_activities, result.changes)
        return result

    def _session_expired(self) -> None:
        if self.repo.get_pref("session_state") != "expired":
            self.repo.set_pref("session_state", "expired")
            if self.on_session_expired:
                self.on_session_expired()

    def _session_ok(self) -> None:
        if self.repo.get_pref("session_state") == "expired":
            self.repo.set_pref("session_state", "ok")
            if self.on_session_restored:
                self.on_session_restored()
        elif self.repo.get_pref("session_state") is None:
            self.repo.set_pref("session_state", "ok")

    # Runs on the browser thread.
    def _sync_course(self, session: BrowserSession, course: dict, baseline: bool, force: bool) -> dict:
        stats = {"activities": 0, "new": 0, "changes": 0}
        discovered = discover_course_activities(session, course)
        forums = [a for a in discovered if a["modname"] == "forum"]
        announcement_forums = [f for f in forums if ANNOUNCEMENT_FORUM_RE.search(f["name"])] or forums[:1]

        for item in discovered:
            modname, category = item["modname"], item["category"]
            details: dict = {}
            checked = False
            try:
                if modname == "assign" and (force or self.repo.needs_detail_check(item["url"], DETAIL_REFRESH_HOURS)):
                    details = assignment_scraper.scrape_assignment(session, item["url"])
                    checked = True
                elif modname == "quiz" and (force or self.repo.needs_detail_check(item["url"], DETAIL_REFRESH_HOURS)):
                    details = quiz_scraper.scrape_quiz(session, item["url"])
                    checked = True
                elif category in ("lab", "task") and (force or self.repo.needs_detail_check(item["url"], DETAIL_REFRESH_HOURS)):
                    details = quiz_scraper.scrape_generic(session, item["url"])
                    checked = True
                elif modname in ("resource", "folder") and (
                    force or self.repo.needs_detail_check(item["url"], RESOURCE_REFRESH_HOURS)
                ):
                    details = {"files": file_downloader.resolve_resource_files(session, item["url"])}
                    checked = True
            except SessionExpired:
                raise
            except Exception as error:  # noqa: BLE001
                log.warning("Could not read %s (%s): %s", item["name"], item["url"], error)

            files = details.pop("files", [])
            for key, value in item.get("course_page_dates", {}).items():
                if not details.get(key):
                    details[key] = value
            data = {**item, **details}
            activity_id, changes = self.repo.upsert_activity(data, details_checked=checked)
            stats["activities"] += 1 if category in ("assignment", "quiz", "lab", "task") else 0
            is_new = any(c["kind"] == "new_activity" for c in changes)

            new_files = []
            for f in files:
                _, file_is_new = self.repo.upsert_file(activity_id, course["id"], f)
                if file_is_new:
                    new_files.append(f["filename"])

            self._record_events(course, item, activity_id, changes, new_files, is_new, baseline)
            stats["new"] += 1 if is_new else 0
            stats["changes"] += len([c for c in changes if c["kind"] != "new_activity"]) + (len(new_files) if not is_new else 0)

        for forum in announcement_forums:
            try:
                self._sync_announcements(session, course, forum, baseline)
            except SessionExpired:
                raise
            except Exception as error:  # noqa: BLE001
                log.warning("Could not read forum %s: %s", forum["name"], error)

        self.repo.deactivate_missing_activities(course["id"], [a["url"] for a in discovered])
        return stats

    def _record_events(self, course: dict, item: dict, activity_id: int, changes: list[dict],
                       new_files: list[str], is_new: bool, baseline: bool) -> None:
        course_short = course["name"].split(" (", 1)[0]
        category = item["category"]
        notify_kinds = category in ("assignment", "quiz", "lab", "task") or (category == "resource" and new_files)
        for change in changes:
            kind = change["kind"]
            if kind == "new_activity":
                if category not in ("assignment", "quiz", "lab", "task", "resource"):
                    continue
                activity = self.repo.activity(activity_id)
                detail = f"Deadline: {human_when(activity.deadline)}" if activity and activity.deadline else ""
                if new_files:
                    detail += ("\n" if detail else "") + "Files: " + ", ".join(new_files[:5])
                self.repo.add_event("new_activity", f"{course_short} — {item['name']}", detail,
                                    activity_id=activity_id, course_id=course["id"], notified=baseline)
            elif kind in ("deadline_changed", "deadline_added") and notify_kinds:
                label = {"due_at": "Deadline", "closes_at": "Closing time", "opens_at": "Opening time",
                         "cutoff_at": "Cut-off"}.get(change["field"], "Date")
                old = human_when(from_iso(change["old"])) if change.get("old") else "not set"
                detail = f"{label}: {old} ➜ {human_when(from_iso(change['new']))}"
                self.repo.add_event(kind, f"{course_short} — {item['name']}", detail,
                                    activity_id=activity_id, course_id=course["id"], notified=baseline)
            elif kind == "grade_posted" and notify_kinds:
                self.repo.add_event("grade_posted", f"{course_short} — {item['name']}", f"Grade: {change['new']}",
                                    activity_id=activity_id, course_id=course["id"], notified=baseline)
        if new_files and not is_new:
            self.repo.add_event("new_file", f"{course_short} — {item['name']}", "New file(s): " + ", ".join(new_files[:5]),
                                activity_id=activity_id, course_id=course["id"], notified=baseline)

    def _sync_announcements(self, session: BrowserSession, course: dict, forum: dict, baseline: bool) -> None:
        course_short = course["name"].split(" (", 1)[0]
        for discussion in announcement_scraper.list_discussions(session, forum["url"], limit=8):
            ann_id, is_new = self.repo.add_announcement({
                **discussion, "course_id": course["id"], "forum_name": forum["name"],
            })
            if not is_new:
                continue
            try:
                info = announcement_scraper.read_discussion(session, discussion["url"])
                self.repo.update_announcement(ann_id, info["excerpt"], info["author"])
                excerpt = info["excerpt"][:400]
            except Exception as error:  # noqa: BLE001
                log.info("Could not read discussion %s: %s", discussion["url"], error)
                excerpt = ""
            self.repo.add_event("announcement", f"{course_short} — {discussion['subject']}", excerpt,
                                course_id=course["id"], notified=baseline)

    # ------------------------------------------------------------- downloads
    def download_file(self, file_id: int) -> dict:
        """Return a local copy of a Moodle file, downloading it if needed."""
        record = self.repo.file(file_id)
        if not record:
            raise file_downloader.DownloadError("That file is no longer listed in Moodle.")
        if record.get("local_path") and Path(record["local_path"]).exists():
            return {"filename": record["filename"], "path": record["local_path"],
                    "mime_type": record.get("mime_type") or "application/octet-stream",
                    "size_bytes": record.get("size_bytes"), "course": record.get("course_name"),
                    "activity": record.get("activity_name")}

        def job(session: BrowserSession) -> dict:
            return file_downloader.download(
                session, record["url"], self.download_dir, max_bytes=self.max_download_bytes,
                course_name=record.get("course_name") or "", activity_name=record.get("activity_name") or "",
                preferred_name=record["filename"],
            )

        try:
            info = self.worker.call(job, timeout=300)
        except SessionExpired:
            self._session_expired()
            raise
        self.repo.record_download(file_id, info)
        info.update({"course": record.get("course_name"), "activity": record.get("activity_name")})
        return info

    def refresh_activity_files(self, activity: Activity) -> None:
        """Re-read one activity page so its newest attachments are known before sending."""
        def job(session: BrowserSession) -> list[dict]:
            if activity.modname in ("resource", "folder"):
                return file_downloader.resolve_resource_files(session, activity.url)
            session.open(activity.url)
            return file_downloader.discover_files(session)

        try:
            files = self.worker.call(job, timeout=180)
        except SessionExpired:
            self._session_expired()
            raise
        for f in files:
            self.repo.upsert_file(activity.id, activity.course_id, f)
