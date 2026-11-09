"""Repository: every read/write the application does against SQLite."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from database.db import Database
from timeutil import from_iso, now, now_iso, to_iso

DATE_FIELDS = ("opens_at", "due_at", "cutoff_at", "closes_at")
TRACKED_CATEGORIES = ("assignment", "quiz", "lab", "task")
DONE_STATUSES = {"submitted", "attempted", "not_required"}
OPEN_STATUSES = {"not_submitted", "draft", "not_attempted", "in_progress"}


@dataclass
class Activity:
    id: int
    cmid: int | None
    course_id: int | None
    course_name: str
    modname: str
    category: str
    name: str
    url: str
    section: str | None = None
    opens_at: datetime | None = None
    due_at: datetime | None = None
    cutoff_at: datetime | None = None
    closes_at: datetime | None = None
    status: str | None = None
    status_text: str | None = None
    grading_status: str | None = None
    grade: str | None = None
    attempts_allowed: str | None = None
    time_limit: str | None = None
    time_remaining: str | None = None
    requirements: str | None = None
    first_seen: str | None = None
    last_checked: str | None = None
    files: list[dict] = field(default_factory=list)

    @property
    def deadline(self) -> datetime | None:
        if self.category == "quiz" or self.modname == "quiz":
            return self.closes_at or self.due_at
        return self.due_at or self.closes_at or self.cutoff_at

    @property
    def done(self) -> bool:
        return (self.status or "") in DONE_STATUSES

    @property
    def overdue(self) -> bool:
        deadline = self.deadline
        return bool(
            deadline
            and deadline < now()
            and not self.done
            and (self.status or "unknown") in OPEN_STATUSES
        )

    @property
    def upcoming(self) -> bool:
        deadline = self.deadline
        if self.done:
            return False
        if deadline is None:
            return self.opens_at is not None and self.opens_at > now()
        return deadline >= now()

    @property
    def short_course(self) -> str:
        return self.course_name.split(" (", 1)[0] if self.course_name else "Course"

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        for key in DATE_FIELDS:
            data[key] = to_iso(getattr(self, key))
        data["deadline"] = to_iso(self.deadline)
        data["done"] = self.done
        data["overdue"] = self.overdue
        return data


def _hash(data: dict) -> str:
    relevant = {k: data.get(k) for k in (*DATE_FIELDS, "status", "grade", "name", "attempts_allowed", "time_limit")}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ courses
    def upsert_course(self, course: dict) -> bool:
        stamp = now_iso()
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT id FROM courses WHERE id = ?", (course["id"],)).fetchone()
            if existing:
                # Keep a full name learned from the course page when the list only shows the code.
                conn.execute(
                    "UPDATE courses SET name = CASE WHEN short_name IS NOT NULL AND short_name = ? THEN name ELSE ? END,"
                    " url = ?, active = 1, last_seen = ? WHERE id = ?",
                    (course["name"], course["name"], course["url"], stamp, course["id"]),
                )
                return False
            conn.execute(
                "INSERT INTO courses(id, name, short_name, url, active, first_seen, last_seen) VALUES(?,?,?,?,1,?,?)",
                (course["id"], course["name"], course.get("short_name"), course["url"], stamp, stamp),
            )
            return True

    def set_course_title(self, course_id: int, full_name: str) -> None:
        """Store the full course name; the previous (code-like) name becomes the short name."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT name, short_name FROM courses WHERE id = ?", (course_id,)).fetchone()
            if not row or row["name"] == full_name:
                return
            short = row["short_name"] or row["name"]
            conn.execute("UPDATE courses SET name = ?, short_name = ? WHERE id = ?", (full_name, short, course_id))

    def course(self, course_id: int) -> dict | None:
        row = self.db.query_one("SELECT * FROM courses WHERE id = ?", (course_id,))
        return dict(row) if row else None

    def deactivate_missing_courses(self, seen_ids: Iterable[int]) -> None:
        ids = list(seen_ids)
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        self.db.execute(f"UPDATE courses SET active = 0 WHERE id NOT IN ({marks})", tuple(ids))

    def courses(self, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM courses" + (" WHERE active = 1" if active_only else "") + " ORDER BY name COLLATE NOCASE"
        return [dict(r) for r in self.db.query(sql)]

    # --------------------------------------------------------------- activities
    def activity_count(self) -> int:
        return self.db.query_one("SELECT COUNT(*) AS n FROM activities")["n"]

    def get_activity_row(self, url: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM activities WHERE url = ?", (url,))
        return dict(row) if row else None

    def upsert_activity(self, data: dict, *, details_checked: bool) -> tuple[int, list[dict]]:
        """Insert or update an activity. Returns (id, list of change events)."""
        stamp = now_iso()
        values = {k: to_iso(data.get(k)) if isinstance(data.get(k), datetime) else data.get(k) for k in DATE_FIELDS}
        changes: list[dict] = []
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM activities WHERE url = ?", (data["url"],)).fetchone()
            if row is None:
                cursor = conn.execute(
                    """INSERT INTO activities(cmid, course_id, modname, category, name, url, section,
                        opens_at, due_at, cutoff_at, closes_at, status, status_text, grading_status, grade,
                        attempts_allowed, time_limit, time_remaining, requirements, content_hash, active,
                        first_seen, last_seen, last_checked, last_changed)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)""",
                    (
                        data.get("cmid"), data.get("course_id"), data["modname"], data["category"], data["name"],
                        data["url"], data.get("section"), values["opens_at"], values["due_at"], values["cutoff_at"],
                        values["closes_at"], data.get("status"), data.get("status_text"), data.get("grading_status"),
                        data.get("grade"), data.get("attempts_allowed"), data.get("time_limit"),
                        data.get("time_remaining"), data.get("requirements"), _hash({**data, **values}),
                        stamp, stamp, stamp if details_checked else None, stamp,
                    ),
                )
                activity_id = cursor.lastrowid
                changes.append({"kind": "new_activity", "activity_id": activity_id})
                return activity_id, changes

            activity_id = row["id"]
            updates: dict[str, Any] = {
                "name": data["name"], "category": data["category"], "section": data.get("section") or row["section"],
                "course_id": data.get("course_id") or row["course_id"], "active": 1, "last_seen": stamp,
            }
            if details_checked:
                for date_field in DATE_FIELDS:
                    old, new = row[date_field], values[date_field]
                    if old != new and (new is not None or old is not None):
                        # Ignore a date vanishing because a page failed to render it.
                        if new is None:
                            continue
                        conn.execute(
                            "INSERT INTO deadline_history(activity_id, field, old_value, new_value, changed_at) VALUES(?,?,?,?,?)",
                            (activity_id, date_field, old, new, stamp),
                        )
                        if old is not None:
                            changes.append({"kind": "deadline_changed", "activity_id": activity_id,
                                            "field": date_field, "old": old, "new": new})
                        elif date_field in ("due_at", "closes_at") and row["last_checked"]:
                            changes.append({"kind": "deadline_added", "activity_id": activity_id,
                                            "field": date_field, "old": None, "new": new})
                        updates[date_field] = new
                for key in ("status", "status_text", "grading_status", "grade", "attempts_allowed",
                            "time_limit", "time_remaining", "requirements"):
                    if key in data:
                        updates[key] = data.get(key)
                if data.get("grade") and data.get("grade") != row["grade"]:
                    changes.append({"kind": "grade_posted", "activity_id": activity_id, "new": data.get("grade")})
                updates["last_checked"] = stamp
                new_hash = _hash({**dict(row), **updates})
                if new_hash != row["content_hash"]:
                    updates["content_hash"] = new_hash
                    updates["last_changed"] = stamp
            assignments = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE activities SET {assignments} WHERE id = ?", (*updates.values(), activity_id))
        return activity_id, changes

    def deactivate_missing_activities(self, course_id: int, seen_urls: Iterable[str]) -> None:
        urls = list(seen_urls)
        if not urls:
            return
        marks = ",".join("?" * len(urls))
        self.db.execute(
            f"UPDATE activities SET active = 0 WHERE course_id = ? AND url NOT IN ({marks})",
            (course_id, *urls),
        )

    def _to_activity(self, row: dict) -> Activity:
        return Activity(
            id=row["id"], cmid=row["cmid"], course_id=row["course_id"], course_name=row.get("course_name") or "",
            modname=row["modname"], category=row["category"], name=row["name"], url=row["url"],
            section=row["section"], opens_at=from_iso(row["opens_at"]), due_at=from_iso(row["due_at"]),
            cutoff_at=from_iso(row["cutoff_at"]), closes_at=from_iso(row["closes_at"]), status=row["status"],
            status_text=row["status_text"], grading_status=row["grading_status"], grade=row["grade"],
            attempts_allowed=row["attempts_allowed"], time_limit=row["time_limit"],
            time_remaining=row["time_remaining"], requirements=row["requirements"],
            first_seen=row["first_seen"], last_checked=row["last_checked"],
        )

    def activities(self, categories: Iterable[str] | None = TRACKED_CATEGORIES, course_id: int | None = None,
                   with_files: bool = False) -> list[Activity]:
        sql = ("SELECT a.*, c.name AS course_name FROM activities a LEFT JOIN courses c ON c.id = a.course_id "
               "WHERE a.active = 1 AND (c.active IS NULL OR c.active = 1)")
        params: list[Any] = []
        if categories:
            cats = list(categories)
            sql += f" AND a.category IN ({','.join('?' * len(cats))})"
            params += cats
        if course_id is not None:
            sql += " AND a.course_id = ?"
            params.append(course_id)
        items = [self._to_activity(dict(r)) for r in self.db.query(sql, tuple(params))]
        if with_files:
            for item in items:
                item.files = self.files_for_activity(item.id)
        return items

    def activity(self, activity_id: int) -> Activity | None:
        row = self.db.query_one(
            "SELECT a.*, c.name AS course_name FROM activities a LEFT JOIN courses c ON c.id = a.course_id WHERE a.id = ?",
            (activity_id,),
        )
        if not row:
            return None
        item = self._to_activity(dict(row))
        item.files = self.files_for_activity(item.id)
        return item

    def needs_detail_check(self, url: str, max_age_hours: float) -> bool:
        row = self.db.query_one("SELECT last_checked, due_at, closes_at, status FROM activities WHERE url = ?", (url,))
        if not row or not row["last_checked"]:
            return True
        checked = from_iso(row["last_checked"])
        if checked is None:
            return True
        age_hours = (now() - checked).total_seconds() / 3600
        deadline = from_iso(row["due_at"] or row["closes_at"])
        # Past and finished work changes rarely: refresh once a day.
        if deadline and deadline < now() and (row["status"] or "") in DONE_STATUSES:
            return age_hours >= 24
        return age_hours >= max_age_hours

    # -------------------------------------------------------------------- files
    def upsert_file(self, activity_id: int | None, course_id: int | None, f: dict) -> tuple[int, bool]:
        stamp = now_iso()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM files WHERE url = ?", (f["url"],)).fetchone()
            if row:
                conn.execute("UPDATE files SET last_seen = ?, filename = ? WHERE id = ?", (stamp, f["filename"], row["id"]))
                return row["id"], False
            cursor = conn.execute(
                "INSERT INTO files(activity_id, course_id, filename, url, first_seen, last_seen) VALUES(?,?,?,?,?,?)",
                (activity_id, course_id, f["filename"], f["url"], stamp, stamp),
            )
            return cursor.lastrowid, True

    def record_download(self, file_id: int, info: dict) -> None:
        self.db.execute(
            "UPDATE files SET local_path = ?, size_bytes = ?, mime_type = ?, sha256 = ?, filename = ?, downloaded_at = ? WHERE id = ?",
            (info["path"], info["size_bytes"], info["mime_type"], info["sha256"], info["filename"], now_iso(), file_id),
        )

    def files_for_activity(self, activity_id: int) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM files WHERE activity_id = ? ORDER BY filename", (activity_id,))]

    def all_files(self, course_id: int | None = None) -> list[dict]:
        sql = ("SELECT f.*, a.name AS activity_name, a.category AS category, c.name AS course_name FROM files f "
               "LEFT JOIN activities a ON a.id = f.activity_id LEFT JOIN courses c ON c.id = f.course_id "
               "WHERE (a.active IS NULL OR a.active = 1)")
        params: tuple = ()
        if course_id is not None:
            sql += " AND f.course_id = ?"
            params = (course_id,)
        return [dict(r) for r in self.db.query(sql + " ORDER BY c.name, a.name, f.filename", params)]

    def file(self, file_id: int) -> dict | None:
        row = self.db.query_one(
            "SELECT f.*, a.name AS activity_name, c.name AS course_name FROM files f "
            "LEFT JOIN activities a ON a.id = f.activity_id LEFT JOIN courses c ON c.id = f.course_id WHERE f.id = ?",
            (file_id,),
        )
        return dict(row) if row else None

    # ------------------------------------------------------------ announcements
    def add_announcement(self, a: dict) -> tuple[int, bool]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM announcements WHERE url = ?", (a["url"],)).fetchone()
            if row:
                return row["id"], False
            cursor = conn.execute(
                "INSERT INTO announcements(discussion_id, course_id, forum_name, subject, url, author, posted, excerpt, first_seen)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (a.get("discussion_id"), a.get("course_id"), a.get("forum_name"), a["subject"], a["url"],
                 a.get("author"), to_iso(a.get("posted")) if isinstance(a.get("posted"), datetime) else a.get("posted"),
                 a.get("excerpt"), now_iso()),
            )
            return cursor.lastrowid, True

    def update_announcement(self, announcement_id: int, excerpt: str, author: str) -> None:
        self.db.execute("UPDATE announcements SET excerpt = ?, author = ? WHERE id = ?", (excerpt, author, announcement_id))

    def announcements(self, limit: int = 10, course_id: int | None = None) -> list[dict]:
        sql = ("SELECT n.*, c.name AS course_name FROM announcements n LEFT JOIN courses c ON c.id = n.course_id")
        params: tuple = ()
        if course_id is not None:
            sql += " WHERE n.course_id = ?"
            params = (course_id,)
        sql += " ORDER BY COALESCE(n.posted, n.first_seen) DESC, n.id DESC LIMIT ?"
        return [dict(r) for r in self.db.query(sql, (*params, limit))]

    # ------------------------------------------------------------------- events
    def add_event(self, kind: str, title: str, detail: str = "", *, activity_id: int | None = None,
                  course_id: int | None = None, notified: bool = False) -> int:
        stamp = now_iso()
        cursor = self.db.execute(
            "INSERT INTO events(kind, activity_id, course_id, title, detail, created_at, notified_at) VALUES(?,?,?,?,?,?,?)",
            (kind, activity_id, course_id, title, detail, stamp, stamp if notified else None),
        )
        return cursor.lastrowid

    def pending_events(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM events WHERE notified_at IS NULL ORDER BY id")]

    def mark_events_notified(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if ids:
            self.db.execute(f"UPDATE events SET notified_at = ? WHERE id IN ({','.join('?' * len(ids))})", (now_iso(), *ids))

    def recent_events(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------ notifications
    def notification_sent(self, key: str) -> bool:
        row = self.db.query_one("SELECT status FROM notifications WHERE dedupe_key = ?", (key,))
        return bool(row and row["status"] == "sent")

    def claim_notification(self, key: str, kind: str, body: str, max_attempts: int = 5) -> bool:
        """True if this notification should be sent now (never sent, or failed and retryable)."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT status, attempts FROM notifications WHERE dedupe_key = ?", (key,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO notifications(dedupe_key, kind, body, status, attempts, created_at) VALUES(?,?,?,?,1,?)",
                    (key, kind, body[:4000], "sending", now_iso()),
                )
                return True
            if row["status"] == "sent" or row["attempts"] >= max_attempts:
                return False
            conn.execute(
                "UPDATE notifications SET status = 'sending', attempts = attempts + 1, body = ? WHERE dedupe_key = ?",
                (body[:4000], key),
            )
            return True

    def finish_notification(self, key: str, ok: bool, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE notifications SET status = ?, error = ?, sent_at = ? WHERE dedupe_key = ?",
            ("sent" if ok else "failed", (error or "")[:500] or None, now_iso() if ok else None, key),
        )

    def failed_notifications(self, max_attempts: int = 5) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM notifications WHERE status IN ('failed', 'sending') AND attempts < ? ORDER BY id", (max_attempts,))]

    def notification_history(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]

    # -------------------------------------------------------------- preferences
    def get_pref(self, key: str, default: str | None = None) -> str | None:
        row = self.db.query_one("SELECT value FROM preferences WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_pref(self, key: str, value: str | None) -> None:
        self.db.execute(
            "INSERT INTO preferences(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ---------------------------------------------------------------- sync runs
    def start_sync(self) -> int:
        return self.db.execute("INSERT INTO sync_runs(started_at) VALUES(?)", (now_iso(),)).lastrowid

    def finish_sync(self, run_id: int, ok: bool, *, courses: int = 0, activities: int = 0,
                    new_activities: int = 0, changes: int = 0, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE sync_runs SET finished_at = ?, ok = ?, courses = ?, activities = ?, new_activities = ?, changes = ?, error = ? WHERE id = ?",
            (now_iso(), 1 if ok else 0, courses, activities, new_activities, changes, (error or "")[:500] or None, run_id),
        )

    def last_sync(self, ok_only: bool = True) -> dict | None:
        sql = "SELECT * FROM sync_runs WHERE finished_at IS NOT NULL" + (" AND ok = 1" if ok_only else "") + " ORDER BY id DESC LIMIT 1"
        row = self.db.query_one(sql)
        return dict(row) if row else None
