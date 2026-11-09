"""Persistent Playwright browser, owned by one dedicated worker thread.

Playwright's sync API must always be used from the thread that created it.
Flask requests, the scheduler and WhatsApp handlers therefore never touch the
browser directly: they submit jobs to :class:`BrowserWorker`, which runs them
one at a time on its own thread and restarts the browser if it crashes.

The existing ``moodle_browser_profile`` directory is reused, so the Moodle login
saved there keeps working. Nothing here ever deletes or resets that profile.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)
T = TypeVar("T")

_DEAD_BROWSER_MARKERS = (
    "has been closed",
    "target closed",
    "browser has disconnected",
    "connection closed",
    "not connected",
    "crashed",
)


class BrowserUnavailable(RuntimeError):
    pass


class BrowserSession:
    """Helpers bound to the live page. Only used on the browser thread."""

    def __init__(self, context, page, base_url: str, timeout_ms: int):
        self.context = context
        self.page = page
        self.base_url = base_url.rstrip("/")
        self.base_host = (urlparse(self.base_url).hostname or "").lower()
        self.timeout_ms = timeout_ms

    # -- URL safety ---------------------------------------------------------
    def safe_url(self, url: str) -> str:
        """Resolve a URL and refuse anything outside the configured Moodle host."""
        absolute = urljoin(self.base_url + "/", url)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != self.base_host:
            raise ValueError("Refusing to open a URL outside the configured Moodle site.")
        return absolute

    # -- navigation ---------------------------------------------------------
    def open(self, url: str, *, retries: int = 2) -> None:
        target = self.safe_url(url)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                self.page.goto(target, wait_until="domcontentloaded", timeout=self.timeout_ms)
                last_error = None
                break
            except PlaywrightTimeoutError as error:
                # Moodle pages may keep loading analytics; use what is visible.
                log.warning("Page load timed out (attempt %s): %s", attempt + 1, target)
                last_error = error
                break
            except PlaywrightError as error:
                message = str(error).lower()
                if any(marker in message for marker in _DEAD_BROWSER_MARKERS):
                    raise
                last_error = error
                log.warning("Navigation error (attempt %s) for %s: %s", attempt + 1, target, error)
                time.sleep(1.5 * (attempt + 1))
        if last_error is not None and not isinstance(last_error, PlaywrightTimeoutError):
            raise last_error
        try:
            self.page.locator("#region-main, [role='main'], main").first.wait_for(state="attached", timeout=5_000)
        except PlaywrightTimeoutError:
            pass
        except PlaywrightError:
            pass
        self.page.wait_for_timeout(300)

    @property
    def url(self) -> str:
        return self.page.url or ""

    def is_login_page(self) -> bool:
        current = self.url.lower()
        if "/login/" in current or "login.php" in current:
            return True
        try:
            return self.page.locator('input[type="password"]').count() > 0
        except PlaywrightError:
            return False

    # -- extraction ---------------------------------------------------------
    def main_text(self) -> str:
        for selector in ("#region-main", "[role='main']", "main", "body"):
            try:
                locator = self.page.locator(selector).first
                if locator.count():
                    text = locator.inner_text(timeout=5_000).strip()
                    if text:
                        return re.sub(r"\n{3,}", "\n\n", text)
            except PlaywrightError:
                continue
        return ""

    def text_of(self, selector: str) -> str:
        try:
            locator = self.page.locator(selector)
            if locator.count():
                return "\n".join(t.strip() for t in locator.all_inner_texts() if t.strip())
        except PlaywrightError:
            pass
        return ""

    def links(self, scope: str = "body") -> list[dict[str, str]]:
        """Unique same-site links with their text, nearby context and section."""
        root = None
        for selector in [part.strip() for part in scope.split(",")]:
            try:
                candidate = self.page.locator(selector).first
                if candidate.count():
                    root = candidate
                    break
            except PlaywrightError:
                continue
        if root is None:
            return []
        try:
            rows = root.evaluate(
                """root => Array.from(root.querySelectorAll('a[href]')).map(a => {
                    let raw = '';
                    const inst = a.querySelector('.instancename');
                    if (inst) {
                        const copy = inst.cloneNode(true);
                        copy.querySelectorAll('.accesshide, .sr-only, .visually-hidden').forEach(e => e.remove());
                        raw = (copy.textContent || '').trim();
                    }
                    if (!raw) raw = (a.innerText || a.getAttribute('aria-label') || a.title || '').trim();
                    const item = a.closest('.activity-item, li.activity, .activity, .coursebox, .course-card, [data-region="course-content"], li, tr');
                    const section = a.closest('li.section, .course-section, section');
                    let sectionName = '';
                    if (section) {
                        const h = section.querySelector('.sectionname, h3, h4');
                        sectionName = h ? h.innerText.trim() : '';
                    }
                    const modtype = item ? (Array.from(item.classList).find(c => c.startsWith('modtype_')) || '') : '';
                    return {
                        name: raw.replace(/\\s+/g, ' '),
                        context: item ? (item.innerText || '').trim().replace(/[ \\t]+/g, ' ').replace(/\\n\\s*\\n+/g, '\\n').slice(0, 800) : '',
                        section: sectionName.replace(/\\s+/g, ' '),
                        modtype: modtype.replace('modtype_', ''),
                        url: a.href
                    };
                })"""
            )
        except PlaywrightError as error:
            log.warning("Could not collect links: %s", error)
            return []
        output, seen = [], set()
        for row in rows:
            url = str(row.get("url", "")).split("#", 1)[0].strip()
            if not url:
                continue
            try:
                url = self.safe_url(url)
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            row["url"] = url
            output.append(row)
        return output

    def tables(self) -> dict[str, str]:
        """Merge every 2-column th/td (or td/td) row on the page into {label: value}."""
        try:
            rows = self.page.evaluate(
                """() => Array.from(document.querySelectorAll('#region-main tr, main tr, .activity-dates div, dl')).flatMap(el => {
                    if (el.tagName === 'TR') {
                        const cells = el.querySelectorAll('th, td');
                        if (cells.length >= 2) return [[cells[0].innerText, cells[1].innerText]];
                        return [];
                    }
                    if (el.tagName === 'DL') {
                        const out = [];
                        el.querySelectorAll('dt').forEach(dt => {
                            const dd = dt.nextElementSibling;
                            if (dd) out.push([dt.innerText, dd.innerText]);
                        });
                        return out;
                    }
                    return [];
                })"""
            )
        except PlaywrightError:
            return {}
        table: dict[str, str] = {}
        for label, value in rows:
            label = re.sub(r"\s+", " ", label or "").strip().rstrip(":")
            value = re.sub(r"\s+", " ", value or "").strip()
            if label and value and label not in table:
                table[label] = value
        return table


class BrowserWorker:
    """Runs browser jobs sequentially on one thread, with automatic recovery."""

    def __init__(self, profile_dir: Path, base_url: str, *, headless: bool = False,
                 timeout_ms: int = 45_000, start_url: str | None = None, executable_path: str = "",
                 channel: str = ""):
        self.profile_dir = Path(profile_dir)
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.start_url = start_url or f"{self.base_url}/my/"
        self.executable_path = executable_path
        self.channel = channel
        # Moodle's login cookie is a *session* cookie, which Chromium does not keep
        # when it restarts. We keep a private copy (Moodle host only) so a restart or
        # crash does not force a new manual login while the server session is valid.
        self.cookie_file = self.profile_dir.parent / f".{self.profile_dir.name}_session.json"
        self._cookie_snapshot = ""
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._playwright = None
        self._context = None
        self._session: BrowserSession | None = None
        self.started_at: float | None = None
        self.restarts = 0
        self.last_error: str | None = None

    # -- lifecycle ----------------------------------------------------------
    def start(self, wait: float = 120) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="browser-worker", daemon=True)
        self._thread.start()
        if not self._ready.wait(wait):
            raise BrowserUnavailable("Browser did not start in time.")
        if self._session is None:
            raise BrowserUnavailable(self.last_error or "Browser failed to start.")

    def stop(self) -> None:
        self._stopping.set()
        self._jobs.put(None)
        if self._thread:
            self._thread.join(timeout=30)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._session is not None)

    def submit(self, fn: Callable[[BrowserSession], T]) -> Future:
        future: Future = Future()
        if self._stopping.is_set():
            future.set_exception(BrowserUnavailable("Browser is shutting down."))
            return future
        self._jobs.put((fn, future))
        return future

    def call(self, fn: Callable[[BrowserSession], T], timeout: float = 900) -> T:
        return self.submit(fn).result(timeout=timeout)

    # -- internals (browser thread only) ------------------------------------
    def _launch(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        options = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            accept_downloads=True,
            viewport={"width": 1365, "height": 900},
        )
        if self.executable_path:
            options["executable_path"] = self.executable_path
        elif self.channel:
            options["channel"] = self.channel
        self._context = self._playwright.chromium.launch_persistent_context(**options)
        self._context.set_default_timeout(self.timeout_ms)
        self._restore_cookies()
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._session = BrowserSession(self._context, page, self.base_url, self.timeout_ms)
        self.started_at = time.time()
        try:
            self._session.open(self.start_url)
        except Exception as error:  # noqa: BLE001 - the page opening is best-effort
            log.warning("Moodle start page did not open: %s", error)
        log.info("Browser started with profile %s", self.profile_dir)

    def _restore_cookies(self) -> None:
        try:
            if not self.cookie_file.exists():
                return
            saved = json.loads(self.cookie_file.read_text(encoding="utf-8"))
            now = time.time()
            cookies = [c for c in saved if c.get("expires", -1) in (-1, None) or c["expires"] > now]
            if cookies:
                self._context.add_cookies(cookies)
                log.info("Restored the saved Moodle session")
        except Exception as error:  # noqa: BLE001 - never block startup on this
            log.warning("Could not restore the saved Moodle session: %s", error)

    def save_cookies(self) -> None:
        """Persist cookies for the Moodle host (called on the browser thread)."""
        if self._context is None:
            return
        try:
            cookies = self._context.cookies([self.base_url])
            snapshot = json.dumps(cookies, sort_keys=True)
            if snapshot == self._cookie_snapshot:
                return
            temp = self.cookie_file.with_suffix(".tmp")
            temp.write_text(snapshot, encoding="utf-8")
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            temp.replace(self.cookie_file)
            self._cookie_snapshot = snapshot
        except Exception as error:  # noqa: BLE001
            log.debug("Could not save Moodle session: %s", error)

    def _close_context(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception:  # noqa: BLE001
            pass
        self._context = None
        self._session = None

    def _restart(self) -> None:
        self.restarts += 1
        log.warning("Restarting browser (restart #%s)", self.restarts)
        self._close_context()
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        self._playwright = None
        self._launch()

    def _healthy(self) -> bool:
        try:
            if self._session is None:
                return False
            if self._session.page.is_closed():
                pages = self._context.pages if self._context else []
                self._session.page = pages[0] if pages else self._context.new_page()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _run(self) -> None:
        try:
            self._launch()
        except Exception as error:  # noqa: BLE001
            self.last_error = f"Could not start Chromium: {error}"
            log.exception("Browser launch failed")
        finally:
            self._ready.set()

        while True:
            job = self._jobs.get()
            if job is None:
                break
            fn, future = job
            if not future.set_running_or_notify_cancel():
                continue
            result: Any = None
            error: BaseException | None = None
            for attempt in range(2):
                try:
                    if not self._healthy():
                        self._restart()
                    result = fn(self._session)
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    message = str(exc).lower()
                    if attempt == 0 and any(marker in message for marker in _DEAD_BROWSER_MARKERS):
                        log.warning("Browser connection lost (%s); recovering and retrying once.", exc)
                        try:
                            self._restart()
                        except Exception as restart_error:  # noqa: BLE001
                            self.last_error = str(restart_error)
                            error = restart_error
                            break
                        continue
                    break
            if error is None:
                self.save_cookies()
                future.set_result(result)
            else:
                self.last_error = str(error)
                future.set_exception(error)

        self.save_cookies()
        self._close_context()
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        log.info("Browser stopped")
