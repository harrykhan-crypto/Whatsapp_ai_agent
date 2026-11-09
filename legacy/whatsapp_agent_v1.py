
import os
import requests
from dotenv import load_dotenv
from whagent import Agent

load_dotenv()

token = os.getenv("WHATSAPP_AGENT_API_KEY")

if not token:
    raise SystemExit("WHATSAPP_AGENT_API_KEY is missing from .env")

agent = Agent(token)
students = {}

MENU = (
    "HARRY - SMART UNIVERSITY ASSISTANT\n\n"
    "1. My Courses\n"
    "2. Assignments Due Today\n"
    "3. Quizzes\n"
    "4. Weekly Assignment Summary\n"
    "5. Course Announcements\n\n"
    "You can also ask naturally:\n"
    "- What assignments are due tomorrow?\n"
    "- Show overdue assignments\n"
    "- Show my Automata Theory assignments\n"
    "- What quizzes do I have?\n\n"
    "Type MENU to return here."
)

OPTIONS = {
    "1": "courses",
    "courses": "courses",
    "my courses": "courses",
    "2": "assignments due today",
    "assignments": "assignments due today",
    "assignments due today": "assignments due today",
    "3": "quizzes",
    "quiz": "quizzes",
    "quizzes": "quizzes",
    "4": "weekly summary",
    "weekly summary": "weekly summary",
    "weekly assignment summary": "weekly summary",
    "5": "announcements",
    "announcements": "announcements",
    "course announcements": "announcements",
}


def ask_moodle(question):
    response = requests.post(
        "http://127.0.0.1:5000/ask",
        json={"question": question},
        timeout=180
    )
    response.raise_for_status()

    data = response.json()
    return data.get("answer", "HARRY received no answer from Moodle.")


def send_answer(ctx, answer):
    answer = str(answer).strip()

    if not answer:
        ctx.reply("Moodle returned an empty response.")
        return

    limit = 3000
    chunks = []

    while len(answer) > limit:
        cut = answer.rfind("\n", 0, limit)

        if cut < limit // 2:
            cut = answer.rfind(" ", 0, limit)

        if cut < limit // 2:
            cut = limit

        chunks.append(answer[:cut].strip())
        answer = answer[cut:].strip()

    if answer:
        chunks.append(answer)

    for index, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            chunk = f"PART {index}/{len(chunks)}\n\n{chunk}"

        ctx.reply(chunk)


@agent.on_text
def handle_message(ctx):
    message = (ctx.text or "").strip()
    key = message.lower()

    sender = str(
        getattr(ctx, "sender", None)
        or getattr(ctx, "from_number", None)
        or "default"
    )

    state = students.setdefault(
        sender,
        {"stage": "name", "name": ""}
    )

    if not message:
        ctx.reply("Please send a message.")
        return

    if key in {"start", "restart"}:
        students[sender] = {"stage": "name", "name": ""}
        ctx.reply("Welcome to HARRY!\n\nPlease enter your full name.")
        return

    if key in {"menu", "help"}:
        if not state["name"]:
            state["stage"] = "name"
            ctx.reply("Welcome to HARRY!\n\nPlease enter your full name.")
        else:
            state["stage"] = "menu"
            ctx.reply(MENU)
        return

    if state["stage"] == "name":
        state["name"] = message
        state["stage"] = "menu"
        ctx.reply(f"Welcome, {message}!\n\n{MENU}")
        return

    if key in {"hi", "hello", "salam", "assalamualaikum"}:
        ctx.reply(f"Hello, {state['name']}!\n\n{MENU}")
        return

    question = OPTIONS.get(key, message)

    ctx.reply("HARRY is checking Moodle. Please wait...")

    try:
        answer = ask_moodle(question)
        send_answer(ctx, answer)

    except requests.RequestException as error:
        print("Moodle connection error:", repr(error))
        ctx.reply(
            "I couldn't connect to the Moodle reader. "
            "Please check that app.py is running."
        )


print("Starting HARRY Smart University Assistant...")
agent.run()
