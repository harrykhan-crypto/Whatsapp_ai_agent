"""Free, local AI understanding through Ollama (https://ollama.com).

Used only when the fast rule-based parser does not understand a message. The
model runs on your own PC: no API key, no cost, and messages never leave the
computer. It only *classifies* the request (what, when, which course, which
filters). Every answer is still built from your real LMS data, so the AI can
never invent deadlines or files.

If Ollama is not installed or not running, HARRY simply works without it.
"""

from __future__ import annotations

import json
import logging
import time

import requests

from services.intent import Intent

log = logging.getLogger(__name__)

ACTIONS = ["query", "download", "overdue", "courses", "report", "announcements", "grades", "files", "menu",
           "help", "greeting", "unknown"]
PERIODS = ["today", "tomorrow", "day_after", "week", "next_week", "month", "upcoming"]
CATEGORIES = ["assignment", "quiz", "lab", "task"]

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "period": {"type": "string", "enum": PERIODS + ["none"]},
        "categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}},
        "course": {"type": "string"},
        "pending_only": {"type": "boolean"},
        "overdue_only": {"type": "boolean"},
        "done_only": {"type": "boolean"},
        "latest": {"type": "boolean"},
        "all_items": {"type": "boolean"},
        "search_words": {"type": "array", "items": {"type": "string"}},
        "numbers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "period", "categories", "course", "pending_only", "overdue_only", "done_only",
                 "latest", "all_items", "search_words", "numbers"],
}

PROMPT = """You convert a university student's WhatsApp message (English, Urdu or Roman Urdu) into a JSON request
for an assistant that reads their Moodle LMS. Reply with JSON only, matching the schema.

action:
- "query": list academic work (assignments, quizzes, labs, tasks, deadlines)
- "download": the student wants FILES sent (file, pdf, slides, bhejo/send + file, download)
- "overdue": list late / missed / overdue work
- "courses": list enrolled courses
- "report": full summary report
- "announcements": course announcements / news / notices
- "grades": marks / grades / results
- "files": list available files without sending them
- "menu", "help", "greeting" (only a greeting with no request), "unknown" (not about university work)
period: today, tomorrow (kal), day_after (parson), week (this week / is hafte / soon), next_week (agle hafte),
        month, upcoming (aane wale / coming), or "none".
categories: any of assignment, quiz (test, mcq), lab (practical, lab report), task (weekly task, activity). Empty = all.
course: copy EXACTLY one name from this list if the message refers to it (abbreviations count, e.g. OS, HCI), else "":
{courses}
pending_only: not yet submitted / baqi / reh gaya. overdue_only: late / missed / deadline guzar gayi.
done_only: already submitted / completed. latest: newest / last / nayi. all_items: all / sari / sab.
search_words: words that name a specific activity or file (e.g. "lecture", "project"); numbers: e.g. "2" for "assignment 2".
"""


class OllamaIntentParser:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen2.5:3b", timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._available: bool | None = None
        self._checked_at = 0.0

    # ----------------------------------------------------------------- status
    def available(self) -> bool:
        """True if Ollama is running and the model is installed (re-checked every 5 minutes)."""
        if self._available is not None and time.time() - self._checked_at < 300:
            return self._available
        self._checked_at = time.time()
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=3)
            names = {m.get("name", "") for m in response.json().get("models", [])}
            wanted = self.model if ":" in self.model else f"{self.model}:latest"
            self._available = wanted in names or self.model in names
            if not self._available:
                log.info("Ollama is running but model %s is not installed (run: ollama pull %s)", self.model, self.model)
        except (requests.RequestException, ValueError):
            self._available = False
        return self._available

    # ------------------------------------------------------------------ parse
    def parse(self, text: str, courses: list[dict]) -> Intent | None:
        if not text.strip() or not self.available():
            return None
        names = "\n".join(f"- {c['name']}" for c in courses) or "- (none known yet)"
        payload = {
            "model": self.model,
            "stream": False,
            "format": SCHEMA,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": PROMPT.replace("{courses}", names)},
                {"role": "user", "content": text.strip()[:500]},
            ],
        }
        try:
            started = time.time()
            response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            data = json.loads(content)
            log.info("🧠 Local AI understood the message in %.1fs: %s", time.time() - started, data)
        except (requests.RequestException, ValueError, KeyError) as error:
            log.warning("Local AI could not parse the message: %s", error)
            return None
        return self._to_intent(data, text, courses)

    @staticmethod
    def _to_intent(data: dict, text: str, courses: list[dict]) -> Intent | None:
        action = data.get("action")
        if action not in ACTIONS or action == "unknown":
            return None
        intent = Intent(action=action, raw=text)
        period = data.get("period")
        intent.period = period if period in PERIODS else None
        intent.categories = [c for c in (data.get("categories") or []) if c in CATEGORIES]
        course_name = (data.get("course") or "").strip().lower()
        if course_name:
            for course in courses:
                if course["name"].lower() == course_name or course_name in course["name"].lower():
                    intent.course_id, intent.course_name = course["id"], course["name"]
                    break
        for flag in ("pending_only", "overdue_only", "done_only", "latest", "all_items"):
            setattr(intent, flag, bool(data.get(flag)))
        intent.want_files = action == "download"
        intent.terms = [w.lower() for w in (data.get("search_words") or []) if isinstance(w, str) and len(w) >= 2][:5]
        intent.numbers = [str(int(n)) for n in (data.get("numbers") or []) if str(n).isdigit()][:3]
        if action == "overdue":
            intent.overdue_only = True
        return intent
