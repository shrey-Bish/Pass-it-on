"""The allowlist between agents. Every message one agent sends another passes through cross(), and nothing else.

Only these fields cross: item_id, item_type, window_start, window_end, building_code, rating_bucket, confirm (plus
the message's kind). No free text, no schedules, no grades, no reasons. Values are checked too, so a field can't
smuggle a sentence: item_type must be a type that's actually listed, building_code a real public building, times
one window at most 12 hours long. Every block is logged with the field names, never their values.
"""

import logging
import re
from datetime import datetime, timedelta

from . import campus

log = logging.getLogger("passiton.gate")

ALLOWED = ("item_id", "item_type", "window_start", "window_end", "building_code", "rating_bucket", "confirm")
KINDS = ("need", "offer", "plan", "confirm", "decline", "cancel")
RATING_BUCKETS = ("new", "ok", "good", "great")
MAX_WINDOW = timedelta(hours=12)
ITEM_ID = re.compile(r"^[a-z0-9]{6,16}$")


class GateBlocked(ValueError):
    """A message tried to carry something that isn't allowed between agents."""


class Gate:
    def __init__(self, known_types: set[str] | None = None):
        self.known_types = known_types or set()
        self.blocked: list[dict] = []  # what was stopped: sender, receiver, field names and why. Never values.

    def cross(self, message: dict, *, sender: str, receiver: str) -> dict:
        """Returns a clean copy of an allowed message, or raises GateBlocked and logs it."""
        try:
            return self._check(message)
        except GateBlocked as e:
            fields = sorted(map(str, message)) if isinstance(message, dict) else [type(message).__name__]
            entry = {"sender": sender, "receiver": receiver, "fields": fields, "why": str(e)}
            self.blocked.append(entry)
            log.warning("Gate blocked %s -> %s: %s (fields: %s)", sender, receiver, e, ", ".join(fields))
            raise

    def _check(self, message: dict) -> dict:
        if not isinstance(message, dict):
            raise GateBlocked("not a message")
        if message.get("kind") not in KINDS:
            raise GateBlocked("unknown kind")
        extra = set(message) - set(ALLOWED) - {"kind"}
        if extra:
            raise GateBlocked(f"fields not allowed: {', '.join(sorted(extra))}")
        clean = {"kind": message["kind"]}
        for key in ALLOWED:
            if key in message:
                clean[key] = getattr(self, f"_{key}")(message[key])
        if ("window_start" in clean) != ("window_end" in clean):
            raise GateBlocked("a window needs both a start and an end")
        if "window_start" in clean:
            span = clean["window_end"] - clean["window_start"]
            if span <= timedelta(0) or span > MAX_WINDOW:
                raise GateBlocked("a window must be one stretch of time, up to 12 hours")
        return clean

    def _item_id(self, value) -> str:
        if not isinstance(value, str) or not ITEM_ID.match(value):
            raise GateBlocked("item_id must be an id")
        return value

    def _item_type(self, value) -> str:
        if not isinstance(value, str) or value not in self.known_types:
            raise GateBlocked("item_type must be a listed type")
        return value

    def _time(self, value) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                raise GateBlocked("times must be ISO times") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise GateBlocked("times must be ISO times with a time zone")
        return value

    _window_start = _window_end = _time

    def _building_code(self, value) -> str:
        if not campus.is_building(value):
            raise GateBlocked("building_code must be a public campus building")
        return value

    def _rating_bucket(self, value) -> str:
        if value not in RATING_BUCKETS:
            raise GateBlocked("rating_bucket must be new, ok, good or great")
        return value

    def _confirm(self, value) -> bool:
        if type(value) is not bool:
            raise GateBlocked("confirm must be true or false")
        return value
