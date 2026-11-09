"""Timezone helpers. All user-facing times are Asia/Karachi (configurable)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from config import settings

# Tests replace this to freeze time.
_now_provider: Callable[[], datetime] | None = None


def now() -> datetime:
    if _now_provider is not None:
        return _now_provider()
    return datetime.now(settings.tz)


def set_now_provider(provider: Callable[[], datetime] | None) -> None:
    global _now_provider
    _now_provider = provider


def today() -> date:
    return now().date()


def localize(dt: datetime | None) -> datetime | None:
    """Attach the LMS timezone to naive datetimes (Moodle shows local times)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=settings.tz)
    return dt.astimezone(settings.tz)


def to_iso(dt: datetime | None) -> str | None:
    dt = localize(dt)
    return dt.isoformat(timespec="minutes") if dt else None


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return localize(datetime.fromisoformat(value))
    except ValueError:
        return None


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def human_when(dt: datetime | None, *, with_time: bool = True) -> str:
    """'Today 11:59 PM', 'Tomorrow 9:00 AM', 'Fri 14 Nov, 11:59 PM'."""
    if dt is None:
        return "No date"
    dt = localize(dt)
    days = (dt.date() - today()).days
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if days == 0:
        label = "Today"
    elif days == 1:
        label = "Tomorrow"
    elif days == -1:
        label = "Yesterday"
    elif 1 < days < 7:
        label = dt.strftime("%A")
    else:
        label = dt.strftime("%a %d %b").replace(" 0", " ")
    return f"{label}, {clock}" if with_time else label


def human_remaining(dt: datetime | None) -> str:
    if dt is None:
        return ""
    delta = localize(dt) - now()
    seconds = int(delta.total_seconds())
    overdue = seconds < 0
    seconds = abs(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        text = f"{days}d {hours}h"
    elif hours:
        text = f"{hours}h {minutes}m"
    else:
        text = f"{minutes}m"
    return f"overdue by {text}" if overdue else f"{text} left"


def day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day, tzinfo=settings.tz)
    return start, start + timedelta(days=1)
