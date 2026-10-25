import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

# No network in tests: no model, no Twilio, no Telegram. Set before anything reads .env (dotenv never overrides).
os.environ.update(PASSITON_VOICE="template", TWILIO_FROM="", TELEGRAM_BOT_TOKEN="",
                  PASSITON_DATA=tempfile.mkdtemp(prefix="passiton-tests-"))

from passiton import calendar_feed, demo  # noqa: E402
from passiton.agents import Hub  # noqa: E402
from passiton.models import TZ  # noqa: E402
from passiton.store import Store  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "assets" / "samples"


def at(hour: int, minute: int = 0) -> datetime:
    """A time on the demo Thursday."""
    return datetime(2026, 10, 29, hour, minute, tzinfo=TZ)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def hub() -> Hub:
    return Hub(Store(":memory:"), voice_model=False)


@pytest.fixture
def world(hub, tmp_path) -> Hub:
    """The seeded demo world: Maya's TI-84 lent to Jay and Priya, Dev waitlisted, clock 8:00 AM."""
    run(demo.seed(hub, tmp_path))
    return hub


def events(person: str):
    return calendar_feed.parse((SAMPLES / f"{person}.ics").read_bytes(), person)
