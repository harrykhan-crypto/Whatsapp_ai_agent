# HARRY — Abasyn WhatsApp LMS Intelligence Agent

A personal academic assistant that watches your Abasyn University Moodle
(`https://fall2026.auplmscs.com`) with your own logged-in browser session and talks
to you on WhatsApp: deadlines, quizzes, labs, overdue work, new files,
announcements, and the actual LMS files themselves, in English or Roman Urdu.

```
You:   Assalam o Alaikum, kal university ka kya kaam hai?
HARRY: Wa Alaikum Assalam! 👋
       🟠 DUE TOMORROW
       1. 📄 Human Computer Interaction — Assignment 2
          ⏰ Deadline: Tomorrow, 5:00 PM (18h 39m left)
          📌 Status: ❌ Not submitted
          📎 Files: HCI_Assignment_2.pdf, HCI_Template.docx
       ...
You:   Send me all pending assignment files
HARRY: ✅ Sending 4 files from pending assignments…   [actual files attached]
```

## Easiest way (double-click)

| File | When | What it does |
|---|---|---|
| `SETUP_HARRY.bat` | once (and after each update) | finds Python, creates `venv`, installs packages + Chromium, checks `.env`, runs the tests |
| `CHECK_HARRY.bat` | once, before first use | live check of your real LMS and WhatsApp (sign in to Moodle in the window if asked) |
| `START_HARRY.bat` | every day | starts HARRY and restarts it automatically if it ever crashes |

Before `CHECK_HARRY.bat`, send any message (e.g. `menu`) to your HARRY agent on WhatsApp once.
Keep the START_HARRY window and the Moodle browser window open (minimising is fine).
To stop HARRY, close the START_HARRY window.

## Log in once (automatic re-login)

Double-click **`SAVE_LOGIN.bat`** and type your Moodle username and password. It is checked on
Moodle and saved in `.env` (on your server too, if you use one). HARRY then signs in again by
itself whenever Moodle ends the session. After changing your LMS password, run `SAVE_LOGIN.bat`
again. A wrong password is tried only once (no account lockout), and a CAPTCHA or verification
code is never bypassed: HARRY asks you to sign in instead. The password never appears in logs or
WhatsApp messages.

## Run 24/7 for free (laptop off)

See **[docs/FREE_24x7_SERVER.md](docs/FREE_24x7_SERVER.md)**: a free Oracle Cloud server, set up
with `DEPLOY_TO_SERVER.bat`. When Moodle needs a new login, `LOGIN_AND_SEND.bat` on the laptop
sends it to the server.

## Setup (Windows PowerShell, manual)

```powershell
cd C:\path\to\Whatsapp_ai_agent
git fetch origin
git checkout claude/laughing-bohr-j1rbr6

py -3.13 -m venv venv                      # skip if venv already exists
.\venv\Scripts\Activate.ps1                # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -r requirements.txt
python -m playwright install chromium

Copy-Item .env.example .env                # only if you do not have a .env yet
notepad .env                               # fill in WHATSAPP_AGENT_API_KEY (and/or Cloud API values)
```

### Test, then run

```powershell
python -m pytest -q                        # 43 automated tests (mock Moodle + mock WhatsApp)
python diagnostics.py lms                  # live: your real LMS (sign in if the window asks)
python diagnostics.py whatsapp             # live: sends you a test text, document and menu
python app.py                              # start HARRY (keep the browser window open)
```

Both `diagnostics.py` and `app.py` use the same browser profile and WhatsApp key,
so run one at a time. `python whatsapp_agent.py` still works: it starts `app.py`.

### First start
1. A Chromium window opens on Moodle. If you see the login page, **sign in yourself**
   (HARRY never asks for, stores or sends your password).
2. HARRY reads every course and sends *"HARRY is connected to your LMS"* on WhatsApp.
3. With the default WhatsApp Agent Platform, send any message (e.g. `menu`) to your
   agent once so HARRY learns where to send automatic notifications.

### Start automatically with Windows (optional)
Press Win + R, type `shell:startup`, press Enter, and put a **shortcut** to
`START_HARRY.bat` in that folder. Keep your PC awake while you want monitoring
(Settings → System → Power → Sleep: Never while plugged in).

## WhatsApp commands

| Command | What you get |
|---|---|
| `/menu`, `/help` | Main menu / all commands |
| `/today`, `/tomorrow`, `/upcoming` | Deadlines by period |
| `/assignments [course]`, `/quizzes`, `/labs`, `/tasks` | By activity type |
| `/overdue`, `/pending` | Missing / unsubmitted work |
| `/courses` | Enrolled courses with pending/overdue counts |
| `/files [course]` | Every LMS file with a code such as `F12` |
| `/download F12` or `/download HCI assignment 2` | The actual file(s) |
| `/report` | Full daily academic report |
| `/announcements`, `/grades` | Latest announcements, grades visible on LMS |
| `/sync`, `/status` | Check the LMS now; system status |
| `/notify on` / `/notify off` | Automatic notifications on or off |

Natural language works too: *"Kal kya submit karna hai?"*, *"Next week ke quizzes batao"*,
*"Automata ka assignment bhejo"*, *"Konsi assignments pending hain?"*,
*"Is week ke labs show karo"*, *"Psychology ki file download kar ke bhejo"*,
*"Send me HCI Assignment 2"*. Courses can be named by any word or by abbreviation
(`OS`, `HCI`, `SQE`, `SCD`, `psych`, `automata`).

## Automatic monitoring (Asia/Karachi)

| When | What |
|---|---|
| Every 30 min | Full LMS check → one *LMS UPDATE* message for new activities, changed deadlines, new files, announcements, grades |
| 8:00 AM | Daily academic report |
| 8:00 PM | Tomorrow's deadlines + anything still overdue |
| 24 h and 3 h before a deadline | Reminder (only for work that isn't submitted yet) |
| Session expired | "Moodle login needed" alert (once), and "login restored" once you're signed in again |
| Daily | SQLite backup to `storage/backups` (last 7 kept) |

Every notification has a unique key in the `notifications` table, so it is sent
**once**, even across restarts. Failed deliveries are retried up to 5 times. A
changed deadline gets its own new reminders. If HARRY starts after 8 AM (until
noon), it still sends that day's summary once.

## Architecture

```
app.py               entry point: wires everything, Flask API (/health, /ask, /files/search, /sync, /report, /webhook)
whatsapp_agent.py    WhatsApp Agent Platform poll loop (runs inside app.py)
diagnostics.py       live checks on your PC
config.py            settings from .env          timeutil.py  Asia/Karachi helpers
logging_setup.py     rotating logs, secrets redacted
lms/
  browser.py         one browser thread (Playwright sync API), job queue, crash recovery, saved session
  authentication.py  login-page detection only (no credential handling)
  course_scraper.py  courses from My courses / dashboard
  activity_detector.py  every activity on each course page + classification
  assignment_scraper.py dates, submission status, grade, requirements, attachments
  quiz_scraper.py    open/close dates, attempts, time limit, grade, state (+ generic activities)
  announcement_scraper.py  forum discussions
  file_downloader.py authenticated downloads, original filenames, size/HTML checks
  parsing.py         pure parsers (dates, statuses, classification, filenames)
services/
  monitoring.py      sync engine + change detection → events
  scheduler.py       30-min sync, 8 AM / 8 PM, reminders, retries, backups
  notification.py    deduplicated sending
  report_generator.py   reports & answers from SQLite
  command_handler.py    commands, natural language, file search & delivery
  intent.py          English + Roman Urdu understanding
whatsapp/
  client.py          Agent Platform / Cloud API / console senders, chunking, retries
  webhook.py         Cloud API webhook (verify token + X-Hub-Signature-256)
  message_handler.py inbound routing, sender allow-list, duplicate protection
  formatter.py       WhatsApp formatting, menu, help
database/            db.py (SQLite, WAL, migrations, backups) · models.py (repository)
storage/             agent.db, downloads/<course>/<activity>/<file>, backups/
legacy/              your original app.py and whatsapp_agent.py, unchanged
tests/               mock Moodle (Moodle 4 HTML) + unit, WhatsApp and end-to-end tests
```

**Database tables:** courses, activities (assignments, quizzes, labs, tasks,
resources, forums with all dates, statuses and grades), deadline_history, files,
announcements, events, notifications, preferences, sync_runs.

## WhatsApp configuration

### Option A: WhatsApp Agent Platform (default, already in this project)
`WHATSAPP_PROVIDER=agent` and `WHATSAPP_AGENT_API_KEY=<key>` (WhatsApp → Settings → Agents →
your agent → Chat info → API key). HARRY polls for messages, so no public URL or webhook
is needed. It can only message you, the agent's creator. Documents up to 16 MB
(PDF, Office, TXT natively; ZIP/RAR are sent as generic documents).

### Option B: Official WhatsApp Cloud API
1. developers.facebook.com → create an app → add **WhatsApp** → note the *Phone number ID*.
2. Create a **System User** permanent access token with `whatsapp_business_messaging`
   (the 24-hour test token expires).
3. Add your own number as a recipient (test mode) or verify a business number.
4. Expose the local server over HTTPS, for example:
   `cloudflared tunnel --url http://localhost:5000` (or `ngrok http 5000`).
5. Webhook URL: `https://<tunnel-host>/webhook`, Verify token = `WHATSAPP_VERIFY_TOKEN`,
   subscribe to the **messages** field. Put the app's *App Secret* in `WHATSAPP_APP_SECRET`
   (unsigned or wrongly signed webhook calls are rejected).
6. `.env`: `WHATSAPP_PROVIDER=cloud`, `WHATSAPP_CLOUD_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
   `WHATSAPP_RECIPIENT=92300xxxxxxx`, and a `LOCAL_API_KEY` (the tunnel exposes the local API).
7. Cloud API rule: if you haven't messaged HARRY for 24 h, only approved **templates** can
   be sent. Create a simple utility template and set `WHATSAPP_TEMPLATE_NAME`; HARRY sends
   it automatically to reopen the conversation, then delivers the notification.

## Security

* Login is always done by you in the browser; no password is stored. `get_moodle_token.py`
  is legacy (Moodle web services are disabled on this LMS) and isn't used.
* Moodle's login cookie is a *session* cookie, which Chromium discards on restart. HARRY
  keeps a copy for the Moodle host only in `.moodle_browser_profile_session.json`
  (git-ignored) so restarts and crash recovery don't force a new login while Moodle still
  considers the session valid. Delete that file to forget the session.
* Browser navigation and downloads are restricted to the configured Moodle host.
* Tokens, passwords and `MoodleSession` values are redacted from logs. Nothing about your
  session is ever sent over WhatsApp.
* Only the allowed number(s) can command the bot (Cloud API). The Agent Platform only
  accepts messages from you.
* ⚠️ **`moodle_browser_profile/` is committed to this repository** (from before this
  version) and contains cookies, history and saved-login data. Keep the repository
  **private**. To stop tracking it without deleting your local copy, run
  `git rm -r --cached moodle_browser_profile` yourself, commit, and add it to `.gitignore`.
  (HARRY doesn't do this automatically: pulling such a commit on another machine would
  delete the profile there.)

## Troubleshooting

* **"Moodle login needed"**: sign in in the HARRY browser window, then send `/sync`.
* **No automatic messages (Agent Platform)**: send any message to the agent once.
* **`PollReplacedError` in the log**: another program (an old `whatsapp_agent.py`, or
  `diagnostics.py`) is using the same API key. Close it.
* **A deadline shows "not set"**: run `python diagnostics.py lms`. It lists activities
  without a detected deadline and their URLs.
* Logs: `logs/agent.log`. Health: <http://127.0.0.1:5000/health>.
