"""WhatsApp side of HARRY (WhatsApp Agent Platform via the `whagent` library).

The original bot forwarded every message to app.py over HTTP. Now the poll loop
runs inside app.py's process, so scheduled notifications and replies share one
WhatsApp client (the platform allows only one poller per API key).

    python app.py              # recommended: starts everything
    python whatsapp_agent.py   # same thing, kept for backwards compatibility
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


def run_poller(container, stop: threading.Event) -> None:
    from whagent import Agent, FileOffsetStore
    from whagent.errors import AuthenticationError, PollReplacedError

    messenger = container.messenger
    offset_file = container.settings.database_path.parent / "whatsapp_offset.txt"
    agent = Agent(client=messenger.client, typing=True, start="new", offset_store=FileOffsetStore(offset_file))

    @agent.on_text
    def on_text(ctx):
        # Remember the recipient so scheduled notifications can reach you after a restart.
        container.repo.set_pref("agent_recipient", ctx.sender)
        container.messages.handle_inbound(ctx.sender, ctx.text or "", ctx.message.id)

    @agent.on_media
    def on_media(ctx):
        if ctx.text:  # a caption works like a text command
            container.messages.handle_inbound(ctx.sender, ctx.text, ctx.message.id)

    @agent.on_status
    def on_status(status):
        if getattr(status, "status", "") == "failed":
            log.warning("WhatsApp reported a failed delivery for message %s", getattr(status, "id", "?"))

    @agent.on_error
    def on_error(error, ctx):
        log.error("WhatsApp handler error: %s", error)
        if ctx is not None:
            try:
                ctx.reply("⚠️ Sorry, something went wrong. Please try again.")
            except Exception:  # noqa: BLE001
                pass

    backoff = 5
    log.info("WhatsApp Agent Platform poller started")
    while not stop.is_set():
        try:
            agent.run(stop=stop.is_set)
            backoff = 5
        except AuthenticationError:
            log.error("WhatsApp Agent API key was rejected. Check WHATSAPP_AGENT_API_KEY in .env.")
            stop.wait(300)
        except PollReplacedError:
            log.error("Another program is polling with the same WhatsApp API key "
                      "(is an old whatsapp_agent.py still running?). Retrying in 60s.")
            stop.wait(60)
        except Exception as error:  # noqa: BLE001 - network errors etc.
            log.warning("WhatsApp polling error: %s; retrying in %ss", error, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    from app import main

    main()
