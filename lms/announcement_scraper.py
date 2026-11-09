"""Read discussions (announcements) from a Moodle forum."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from lms.authentication import require_session
from lms.browser import BrowserSession
from lms.parsing import clean_text, parse_date

log = logging.getLogger(__name__)


def list_discussions(session: BrowserSession, forum_url: str, limit: int = 10) -> list[dict]:
    session.open(forum_url)
    require_session(session)
    discussions, seen = [], set()
    for link in session.links("#region-main, [role='main'], main, body"):
        parsed = urlparse(link["url"])
        if "/mod/forum/discuss.php" not in parsed.path:
            continue
        ids = parse_qs(parsed.query).get("d")
        if not ids or not ids[0].isdigit():
            continue
        discussion_id = int(ids[0])
        subject = (link.get("name") or "").strip()
        if discussion_id in seen or not subject or subject.lower() in {"reply", "discuss this topic", "view"}:
            continue
        seen.add(discussion_id)
        context = link.get("context", "")
        posted, _ = parse_date(context)
        discussions.append({
            "discussion_id": discussion_id,
            "subject": subject[:200],
            "url": f"{session.base_url}/mod/forum/discuss.php?d={discussion_id}",
            "posted": posted,
            "context": context,
        })
        if len(discussions) >= limit:
            break
    return discussions


def read_discussion(session: BrowserSession, url: str) -> dict:
    session.open(url)
    require_session(session)
    body = session.text_of(".post-content-container, .posting, [data-region-content='forum-post-core']")
    author = session.text_of(".author a, [data-region='author-name'], .mb-3 a[href*='/user/view.php']").split("\n", 1)[0]
    return {"excerpt": clean_text(body or session.main_text(), 700), "author": author[:80]}
