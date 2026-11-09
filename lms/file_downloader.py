"""Find files attached to Moodle activities and download them with the
student's authenticated browser session (cookies never leave the browser)."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from lms.authentication import SessionExpired
from lms.browser import BrowserSession
from lms.parsing import filename_from_content_disposition, filename_from_url, safe_filename, slug

log = logging.getLogger(__name__)

DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".pps", ".ppsx", ".xls", ".xlsx", ".csv",
    ".txt", ".rtf", ".odt", ".odp", ".ods", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".c", ".cpp", ".h", ".java", ".py", ".ipynb", ".sql", ".html", ".xml", ".json",
    ".md", ".jpg", ".jpeg", ".png", ".mp4", ".mp3",
}

# pluginfile areas that are not course material.
_SKIP_AREAS = (
    "/user/icon/", "/course/overviewfiles/", "/core_admin/", "/theme_", "/block_html/",
    "/assignsubmission_onlinetext/", "/user/draft/",
)


class DownloadError(RuntimeError):
    pass


def _file_kind(path: str) -> str:
    lower = path.lower()
    if "/assignsubmission_file/" in lower:
        return "submission"
    if "/assignfeedback_file/" in lower or "/feedback" in lower:
        return "feedback"
    return "material"


def discover_files(session: BrowserSession, *, include_submissions: bool = False) -> list[dict]:
    """Return downloadable files linked from the current page's main region."""
    files, seen = [], set()
    for link in session.links("#region-main, [role='main'], main, body"):
        url = link["url"]
        path = unquote(urlparse(url).path)
        lower = path.lower()
        if "/pluginfile.php/" not in lower and "/webservice/pluginfile.php/" not in lower:
            continue
        if any(area in lower for area in _SKIP_AREAS):
            continue
        kind = _file_kind(lower)
        if kind == "submission" and not include_submissions:
            continue
        name = filename_from_url(url)
        if Path(name).suffix.lower() not in DOCUMENT_EXTENSIONS:
            text_name = (link.get("name") or "").strip()
            if Path(text_name).suffix.lower() in DOCUMENT_EXTENSIONS:
                name = text_name
            else:
                continue
        clean_url = url.split("#", 1)[0]
        if clean_url in seen:
            continue
        seen.add(clean_url)
        files.append({"filename": name, "url": clean_url, "kind": kind})
    return files


def _looks_like_html(content_type: str, body: bytes) -> bool:
    return "text/html" in content_type and bool(
        re.search(rb"<\s*(?:!doctype\s+html|html|form|head)\b", body[:2000], re.I)
    )


def download(session: BrowserSession, url: str, dest_dir: Path, *, max_bytes: int,
             course_name: str = "", activity_name: str = "", preferred_name: str = "") -> dict:
    """Download one file via the browser's authenticated request context."""
    url = session.safe_url(url)
    response = session.context.request.get(url, timeout=session.timeout_ms, max_redirects=5)
    final_url = response.url or url
    if "/login/" in final_url.lower():
        raise SessionExpired("Moodle session expired while downloading a file.")
    if not response.ok:
        raise DownloadError(f"Moodle returned HTTP {response.status} for this file.")
    headers = {k.lower(): v for k, v in response.headers.items()}
    declared = int(headers.get("content-length") or 0)
    if declared and declared > max_bytes:
        raise DownloadError(f"File is too large to send ({declared // (1024 * 1024)} MB).")
    body = response.body()
    if not body:
        raise DownloadError("Moodle returned an empty file.")
    if len(body) > max_bytes:
        raise DownloadError(f"File is too large to send ({len(body) // (1024 * 1024)} MB).")
    content_type = headers.get("content-type", "").lower()
    if _looks_like_html(content_type, body):
        raise DownloadError("Moodle returned a web page instead of a file.")

    name = (
        filename_from_content_disposition(headers.get("content-disposition"))
        or preferred_name
        or filename_from_url(final_url)
        or "moodle_file"
    )
    if not Path(name).suffix:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) if content_type else None
        if guessed:
            name += guessed
    filename = safe_filename(name)

    folder = Path(dest_dir) / slug(course_name or "course") / slug(activity_name or "files")
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / filename
    digest = hashlib.sha256(body).hexdigest()
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        # Same name, different content (teacher re-uploaded): keep both versions.
        destination = folder / f"{destination.stem}-{digest[:8]}{destination.suffix}"
    destination.write_bytes(body)
    log.info("Downloaded %s (%s bytes)", filename, len(body))
    return {
        "filename": filename,
        "path": str(destination.resolve()),
        "size_bytes": len(body),
        "mime_type": content_type.split(";", 1)[0].strip() or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "sha256": digest,
        "url": url,
    }


def resolve_resource_files(session: BrowserSession, url: str) -> list[dict]:
    """A mod/resource link usually redirects straight to the file; otherwise read its page."""
    url = session.safe_url(url)
    try:
        # HEAD follows Moodle's redirect to the file without downloading the file itself.
        response = session.context.request.head(url, timeout=session.timeout_ms, max_redirects=5)
        final_url = response.url or url
        content_type = (response.headers.get("content-type") or "").lower()
        if response.ok and "text/html" not in content_type and "/pluginfile.php/" in final_url:
            name = filename_from_content_disposition(response.headers.get("content-disposition")) or filename_from_url(final_url)
            return [{"filename": name, "url": final_url.split("#", 1)[0], "kind": "material"}]
    except Exception as error:  # noqa: BLE001
        log.info("Direct resource check failed for %s: %s", url, error)
    session.open(url)
    return discover_files(session)
