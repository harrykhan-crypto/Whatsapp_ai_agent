"""WhatsApp integrations against local mock servers (no real WhatsApp traffic)."""

import hashlib
import hmac
import json
import threading
import time

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from config import Settings
from services.command_handler import CommandHandler
from services.report_generator import ReportGenerator
from whatsapp.client import AgentPlatformMessenger, CloudApiMessenger, DeliveryError
from whatsapp.message_handler import MessageHandler
from whatsapp.webhook import create_webhook_blueprint, extract_messages, verify_signature


class MockServer:
    def __init__(self, app: Flask):
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


# ------------------------------------------------------------ Agent Platform
@pytest.fixture
def agent_platform():
    app = Flask("mock_agent_platform")
    state = {"sent": [], "uploads": [], "updates": [], "auth": set(), "fail_next": 0}

    @app.before_request
    def auth():
        state["auth"].add(request.headers.get("Authorization"))

    @app.post("/messages")
    def messages():
        if state["fail_next"]:
            state["fail_next"] -= 1
            return jsonify({"error": {"message": "Service unavailable", "code": 131016}}), 503
        body = request.get_json()
        state["sent"].append(body)
        return jsonify({"messages": [{"id": f"wamid.{len(state['sent'])}"}], "contacts": [{"wa_id": body["to"]}]})

    @app.post("/media")
    def media():
        f = request.files["file"]
        state["uploads"].append({"name": f.filename, "type": request.form.get("type"), "size": len(f.read())})
        return jsonify({"id": f"media-{len(state['uploads'])}"})

    @app.get("/updates")
    def updates():
        if not state["updates"]:
            time.sleep(0.2)
            return "", 204
        batch = state["updates"].pop(0)
        return jsonify(batch)

    @app.post("/statuses")
    def statuses():
        return jsonify({"success": True})

    server = MockServer(app)
    yield server.url, state
    server.stop()


def test_agent_platform_text_and_documents(agent_platform, tmp_path, repo):
    url, state = agent_platform
    messenger = AgentPlatformMessenger("secret-token", base_url=url, repo=repo)
    with pytest.raises(DeliveryError):
        messenger.send_text("hello")                       # recipient not known yet
    repo.set_pref("agent_recipient", "user:42")
    messenger.send_text("hello")
    assert state["sent"][-1]["to"] == "user:42" and state["sent"][-1]["text"]["body"] == "hello"
    assert "Bearer secret-token" in state["auth"]

    pdf = tmp_path / "HCI_Assignment_2.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    messenger.send_document(pdf, caption="📎 HCI — Assignment 2")
    zipf = tmp_path / "Lab 4.zip"
    zipf.write_bytes(b"PK\x03\x04")
    messenger.send_document(zipf)
    assert state["uploads"][0] == {"name": "HCI_Assignment_2.pdf", "type": "application/pdf", "size": 13}
    assert state["uploads"][1]["type"] == "application/octet-stream"   # ZIP sent as generic document
    doc = state["sent"][-1]["document"]
    assert doc["filename"] == "Lab 4.zip" and doc["id"] == "media-2"

    messenger.backoff = 0.01
    state["fail_next"] = 2
    messenger.send_text("retry me")                         # transient 503s are retried
    assert state["sent"][-1]["text"]["body"] == "retry me"


def test_agent_platform_poller_end_to_end(agent_platform, tmp_path, repo):
    """A WhatsApp message arrives through the real whagent poll loop and is answered."""
    from types import SimpleNamespace

    from whatsapp_agent import run_poller

    url, state = agent_platform
    repo.upsert_course({"id": 1, "name": "Automata Theory (CB)", "url": "u"})
    messenger = AgentPlatformMessenger("tok", base_url=url, repo=repo)
    commands = CommandHandler(repo, None, ReportGenerator(repo))
    handler = MessageHandler(commands, messenger)
    container = SimpleNamespace(messenger=messenger, messages=handler, repo=repo,
                                settings=Settings(database_path=tmp_path / "x.db"))
    state["updates"].append({
        "object": "whatsapp_business_account", "next_offset": 1,
        "entry": [{"id": "agent", "changes": [{"value": {
            "messages": [{"from": "user:7", "id": "wamid.in1", "type": "text", "text": {"body": "/courses"}}]}}]}],
    })
    stop = threading.Event()
    thread = threading.Thread(target=run_poller, args=(container, stop), daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not any("Automata Theory" in json.dumps(m) for m in state["sent"]):
        time.sleep(0.1)
    stop.set()
    thread.join(timeout=30)
    replies = [m for m in state["sent"] if m.get("type") == "text"]
    assert replies and replies[-1]["to"] == "user:7"
    assert "Automata Theory (CB)" in replies[-1]["text"]["body"]
    assert repo.get_pref("agent_recipient") == "user:7"       # remembered for scheduled notifications


# ------------------------------------------------------------------- Cloud API
@pytest.fixture
def graph():
    app = Flask("mock_graph")
    state = {"messages": [], "media": [], "window_closed": False, "fail_next": 0}

    @app.post("/v21.0/PHONE/messages")
    def messages():
        assert request.headers["Authorization"] == "Bearer cloud-token"
        body = request.get_json()
        if state["fail_next"]:
            state["fail_next"] -= 1
            return jsonify({"error": {"message": "Temporary", "code": 131000}}), 500
        if state["window_closed"] and body["type"] != "template":
            return jsonify({"error": {"message": "Re-engagement message", "code": 131047}}), 400
        if body["type"] == "template":
            state["window_closed"] = False
        state["messages"].append(body)
        return jsonify({"messages": [{"id": f"wamid.{len(state['messages'])}"}]})

    @app.post("/v21.0/PHONE/media")
    def media():
        f = request.files["file"]
        state["media"].append({"name": f.filename, "type": request.form["type"]})
        return jsonify({"id": "MEDIA1"})

    server = MockServer(app)
    yield server.url, state
    server.stop()


def test_cloud_api_messenger(graph, tmp_path):
    url, state = graph
    m = CloudApiMessenger("cloud-token", "PHONE", recipient="923001234567", api_base=url, template_name="harry_update")
    m.backoff = 0.01
    m.send_text("Hello")
    assert state["messages"][0]["to"] == "923001234567" and state["messages"][0]["text"]["body"] == "Hello"
    f = tmp_path / "Quiz.pdf"
    f.write_bytes(b"%PDF")
    m.send_document(f, caption="cap")
    assert state["media"] == [{"name": "Quiz.pdf", "type": "application/pdf"}]
    assert state["messages"][-1]["document"] == {"id": "MEDIA1", "filename": "Quiz.pdf", "caption": "cap"}
    m.send_menu("text menu")
    assert state["messages"][-1]["type"] == "interactive" and state["messages"][-1]["interactive"]["type"] == "list"
    # 24h customer-service window closed -> template first, then the message.
    state["window_closed"] = True
    m.send_text("Reminder")
    assert [x["type"] for x in state["messages"][-2:]] == ["template", "text"]
    state["fail_next"] = 1
    m.send_text("after 500")
    assert state["messages"][-1]["text"]["body"] == "after 500"


def test_webhook_security_and_routing(repo):
    settings = Settings(cloud_verify_token="verify-me", cloud_app_secret="app-secret", whatsapp_provider="cloud")
    received = []

    class Recorder:
        def handle_inbound(self, sender, text, message_id=None):
            received.append((sender, text, message_id))

    app = Flask("t")
    app.register_blueprint(create_webhook_blueprint(settings, Recorder()))
    client = app.test_client()
    ok = client.get("/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=123")
    assert ok.status_code == 200 and ok.data == b"123"
    assert client.get("/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1").status_code == 403

    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "923001234567", "id": "w1", "type": "text", "text": {"body": "Kal kya submit karna hai?"}},
        {"from": "923001234567", "id": "w2", "type": "interactive",
         "interactive": {"type": "list_reply", "list_reply": {"id": "/report", "title": "Full report"}}},
    ]}}]}]}
    body = json.dumps(payload).encode()
    assert client.post("/webhook", data=body, content_type="application/json").status_code == 403   # unsigned
    bad = {"X-Hub-Signature-256": "sha256=" + "0" * 64}
    assert client.post("/webhook", data=body, content_type="application/json", headers=bad).status_code == 403
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhook", data=body, content_type="application/json",
                       headers={"X-Hub-Signature-256": sig}).status_code == 200
    deadline = time.time() + 5
    while len(received) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert received == [("923001234567", "Kal kya submit karna hai?", "w1"), ("923001234567", "/report", "w2")]
    assert verify_signature("s", b"x", None) is False
    assert extract_messages({}) == []


def test_unknown_sender_is_ignored(repo):
    from whatsapp.client import ConsoleMessenger

    messenger = ConsoleMessenger()
    handler = MessageHandler(CommandHandler(repo, None, ReportGenerator(repo)), messenger,
                             allowed_senders=["923001234567"])
    assert handler.handle_inbound("923339999999", "/report", "x1") is None
    assert messenger.sent == []
    assert handler.handle_inbound("+923001234567", "/help", "x2") is not None
    assert "HARRY commands" in messenger.sent[-1]["body"]


def test_log_redaction():
    import logging

    from logging_setup import RedactFilter

    f = RedactFilter(["super-secret-token"])
    record = logging.LogRecord("x", logging.INFO, "f", 1, "Auth Bearer abc.def token=xyz MoodleSession=s3cr3t; %s",
                               ("super-secret-token",), None)
    f.filter(record)
    text = record.getMessage()
    assert "abc.def" not in text and "xyz" not in text and "s3cr3t" not in text and "super-secret-token" not in text


def test_agent_provider_ignores_phone_allow_list(tmp_path):
    """Regression: a phone number in WHATSAPP_ALLOWED_NUMBERS must not silence the Agent Platform."""
    from app import build
    from whatsapp.client import ConsoleMessenger

    settings = Settings(database_path=tmp_path / "a.db", download_dir=tmp_path / "d", backup_dir=tmp_path / "b",
                        log_dir=tmp_path / "l", profile_dir=tmp_path / "p", whatsapp_provider="agent",
                        allowed_numbers=["923001234567"], recipient_number="923001234567")
    messenger = ConsoleMessenger()
    c = build(settings, messenger=messenger, with_browser=False)
    assert c.messages.handle_inbound("user:5", "/help", "a1") is not None
    assert "HARRY commands" in messenger.sent[-1]["body"]
    cloud = build(Settings(database_path=tmp_path / "c.db", whatsapp_provider="cloud", log_dir=tmp_path / "l",
                           download_dir=tmp_path / "d", backup_dir=tmp_path / "b", profile_dir=tmp_path / "p",
                           allowed_numbers=["923001234567"]), messenger=ConsoleMessenger(), with_browser=False)
    assert cloud.messages.handle_inbound("923339999999", "/help", "c1") is None
