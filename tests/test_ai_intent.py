"""Free local AI (Ollama) fallback, against a mock Ollama server."""

import json
import threading

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from services.ai_intent import OllamaIntentParser
from services.command_handler import CommandHandler
from services.report_generator import ReportGenerator


@pytest.fixture
def ollama():
    app = Flask("mock_ollama")
    state = {"calls": [], "reply": None, "models": ["qwen2.5:3b"]}

    @app.get("/api/tags")
    def tags():
        return jsonify({"models": [{"name": n} for n in state["models"]]})

    @app.post("/api/chat")
    def chat():
        body = request.get_json()
        state["calls"].append(body)
        return jsonify({"message": {"role": "assistant", "content": json.dumps(state["reply"])}})

    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


def reply(**overrides):
    base = dict(action="unknown", period="none", categories=[], course="", pending_only=False, overdue_only=False,
                done_only=False, latest=False, all_items=False, search_words=[], numbers=[])
    base.update(overrides)
    return base


def test_ai_used_only_when_rules_fail(ollama, repo):
    url, state = ollama
    repo.upsert_course({"id": 3, "name": "Human Computer Interaction (CB)", "url": "u"})
    ai = OllamaIntentParser(url, "qwen2.5:3b")
    handler = CommandHandler(repo, None, ReportGenerator(repo), ai=ai)

    handler.handle("/today")                              # rules understand -> AI not called
    handler.handle("Kal kya submit karna hai?")
    assert state["calls"] == []

    state["reply"] = reply(action="query", period="tomorrow", categories=["quiz"], course="Human Computer Interaction (CB)")
    progress = []
    result = handler.handle("yaar woh cheez jo parhai wali hai uska scene kya hai", progress=progress.append)
    assert len(state["calls"]) == 1
    call = state["calls"][0]
    assert call["model"] == "qwen2.5:3b" and call["format"]["type"] == "object"
    assert "Human Computer Interaction (CB)" in call["messages"][0]["content"]   # real course names given to the model
    assert any("Samajh raha" in p for p in progress)
    text = result.combined_text
    assert "QUIZZES DUE TOMORROW" in text and "Human Computer Interaction" in text


def test_ai_unavailable_falls_back(ollama, repo):
    url, state = ollama
    state["models"] = ["llama3.2:1b"]                    # running, but the configured model isn't installed
    handler = CommandHandler(repo, None, ReportGenerator(repo), ai=OllamaIntentParser(url, "qwen2.5:3b"))
    assert "didn't understand" in handler.handle("purple monkey dishwasher").combined_text
    assert state["calls"] == []
    down = CommandHandler(repo, None, ReportGenerator(repo), ai=OllamaIntentParser("http://127.0.0.1:9", "qwen2.5:3b"))
    assert "didn't understand" in down.handle("purple monkey dishwasher").combined_text


def test_ai_reply_mapping():
    courses = [{"id": 2, "name": "Operating System Concepts (CB)"}]
    intent = OllamaIntentParser._to_intent(
        reply(action="download", period="none", categories=["lab", "bogus"], course="operating system concepts (cb)",
              overdue_only=True, numbers=["3", "x"], search_words=["Report"]), "raw", courses)
    assert intent.action == "download" and intent.want_files and intent.period is None
    assert intent.categories == ["lab"] and intent.course_id == 2 and intent.overdue_only
    assert intent.numbers == ["3"] and intent.terms == ["report"]
    assert OllamaIntentParser._to_intent(reply(action="unknown"), "x", courses) is None
    assert OllamaIntentParser._to_intent({"action": "hack"}, "x", courses) is None
