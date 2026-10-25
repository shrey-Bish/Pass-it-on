"""Twilio / Telegram adapters behind one Messenger interface, and notify(), which records every text first.

Nothing here decides what to say; agents.py and voice.py do. Demo students have no phone, so their texts land only
in the outbox, which the /demo phones show.

Try it: python -m passiton.messenger test
"""

import asyncio
import logging
import os
import sys
import time
import uuid
from datetime import datetime

import httpx

from .models import TZ, Message, User
from .store import Store

log = logging.getLogger("passiton")
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include the URL, and Telegram's has the token

TEST_TEXT = "Pass It On is set up. Your stuff has an agent now. 🧮"
# Twilio errors a trial account is likely to hit, in plain words.
TWILIO_HINTS = {
    21608: "This number isn't verified on your trial. Add it under Phone Numbers > Verified Caller IDs.",
    21211: "That phone number isn't valid. Use the +1XXXXXXXXXX form.",
    21606: "TWILIO_FROM isn't a number this account can send from.",
    30032: "The trial's toll-free number isn't verified yet, so US carriers block it.",
    30034: "US carriers block texts from unregistered numbers, and trials can't register. Use the Telegram fallback.",
    30007: "The carrier filtered the text as spam.",
}


class SendError(Exception):
    pass


class Messenger:
    channel = "outbox"

    def address(self, user: User) -> str | None:
        return None

    async def send(self, to: str, text: str) -> str | None:
        return None


class TwilioMessenger(Messenger):
    channel = "sms"

    def __init__(self, sid: str, sender: str, *, token: str = "", key: str = "", secret: str = ""):
        from twilio.rest import Client

        # An API key (SK...) and its secret work in place of the Auth Token, but still need the Account SID (AC...).
        self.client = Client(key, secret, sid) if key and secret else Client(sid, token)
        self.sender = sender

    def address(self, user: User) -> str | None:
        return user.phone

    async def send(self, to: str, text: str) -> str:
        from twilio.base.exceptions import TwilioRestException

        try:
            message = await asyncio.to_thread(self.client.messages.create, to=to, from_=self.sender, body=text)
        except TwilioRestException as e:
            raise SendError(f"Twilio error {e.code}: {TWILIO_HINTS.get(e.code, e.msg)}") from e
        return message.sid

    async def final_status(self, sid: str, wait: float = 20) -> tuple[str, int | None]:
        """Polls until Twilio knows whether the text was delivered. Trial blocks show up here, not in send()."""
        status, code = "queued", None
        for _ in range(int(wait / 2)):
            message = await asyncio.to_thread(self.client.messages(sid).fetch)
            status, code = message.status, message.error_code
            if status in ("delivered", "undelivered", "failed"):
                break
            await asyncio.sleep(2)
        return status, code


class TelegramMessenger(Messenger):
    channel = "telegram"

    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"

    def address(self, user: User) -> str | None:
        return str(user.telegram_chat_id) if user.telegram_chat_id else None

    async def _call(self, method: str, **payload) -> dict:
        async with httpx.AsyncClient(timeout=30) as http:
            data = (await http.post(f"{self.base}/{method}", json=payload)).json()
        if not data.get("ok"):
            raise SendError(f"Telegram: {data.get('description', 'unknown error')}")
        return data["result"]

    async def send(self, to: str, text: str) -> str:
        return str((await self._call("sendMessage", chat_id=int(to), text=text))["message_id"])

    async def latest_chat(self) -> int | None:
        """The chat of whoever last messaged the bot, so /start links your phone without typing an id."""
        updates = await self._call("getUpdates")
        chats = [u["message"]["chat"]["id"] for u in updates if "message" in u]
        return chats[-1] if chats else None


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def twilio_missing() -> list[str]:
    """The Twilio settings still empty in .env, or [] when it's ready (with either the Auth Token or an API key)."""
    missing = [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_FROM") if not env(k)]
    if not env("TWILIO_AUTH_TOKEN") and not (env("TWILIO_API_KEY") and env("TWILIO_API_SECRET")):
        missing.append("TWILIO_AUTH_TOKEN (or TWILIO_API_KEY + TWILIO_API_SECRET)")
    return missing


def from_env() -> Messenger:
    if not twilio_missing():
        return TwilioMessenger(env("TWILIO_ACCOUNT_SID"), env("TWILIO_FROM"), token=env("TWILIO_AUTH_TOKEN"),
                               key=env("TWILIO_API_KEY"), secret=env("TWILIO_API_SECRET"))
    if telegram := os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        return TelegramMessenger(telegram)
    return Messenger()


async def notify(store: Store, messenger: Messenger, user: User, text: str, *, item_id: str | None = None,
                 kind: str = "info", now: datetime | None = None) -> Message:
    """Records the text, then sends it if this student has a phone on the active channel. A failed send is
    logged, never raised: the outbox still shows it, and one bad number shouldn't stop a re-plan."""
    # Ids sort in the order texts were sent (the demo clock can give several the same time).
    message = Message(id=f"{time.time_ns():020d}{uuid.uuid4().hex[:4]}", user_id=user.id, text=text,
                      at=now or datetime.now(TZ), item_id=item_id, kind=kind)
    if to := messenger.address(user):
        try:
            await messenger.send(to, text)
            message.channel = messenger.channel
        except SendError:
            log.exception("Couldn't text %s", user.name)
    store.put(message)
    return message


async def _test() -> int:
    messenger = from_env()
    if isinstance(messenger, TwilioMessenger):
        to = os.environ.get("TEST_PHONE", "").strip()
        if not to:
            print("Fill TEST_PHONE in .env with your verified number, like +14805551234.")
            return 1
        try:
            sid = await messenger.send(to, TEST_TEXT)
        except SendError as e:
            print(e)
            return 1
        print(f"Twilio accepted the text ({sid}). Waiting for the carrier...")
        status, code = await messenger.final_status(sid)
        print(f"Status: {status}" + (f", error {code}: {TWILIO_HINTS.get(code, 'see twilio.com/docs/api/errors')}" if code else ""))
        return 0 if status in ("delivered", "sent") else 1
    if isinstance(messenger, TelegramMessenger):
        chat = await messenger.latest_chat()
        if chat is None:
            print("Open your bot in Telegram and send it /start, then run this again.")
            return 1
        await messenger.send(str(chat), TEST_TEXT)
        print("Sent to the Telegram chat that last messaged your bot.")
        return 0
    print(f"No texting set up yet. Still empty in .env: {', '.join(twilio_missing())}, and TEST_PHONE. "
          "(Or set TELEGRAM_BOT_TOKEN instead.) Then run this again.")
    return 1


if __name__ == "__main__":
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if sys.argv[1:] != ["test"]:
        sys.exit("Usage: python -m passiton.messenger test")
    sys.exit(asyncio.run(_test()))
