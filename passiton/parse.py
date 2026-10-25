"""Claude: a borrow text -> a structured need. The model reads the words; plain code checks every value.

"need a TI-84 for my stats exam Thu 2pm in Durham Hall" -> TI-84, Thu Oct 29 2:00-3:15 PM, LL.
Only the item, the window and the building are kept. Why they need it ("my stats exam") is never stored.

Try it: python -m passiton.parse "need a TI-84 for my stats exam Thu 2pm in Durham Hall"
"""

import asyncio
import sys
from datetime import date, datetime, timedelta

import anthropic
from pydantic import BaseModel, field_validator

from . import campus, llm
from .models import TZ
from .vision import NOT_ALLOWED

SYSTEM = """A student texted what they need to borrow. Turn it into a request.

Today is {today}. Times are Arizona time.

Reply with only a JSON object, no other text:
{{
  "found": true if they're asking to borrow a physical item, otherwise false,
  "item_type": the item, short, like "TI-84", "graphing calculator" or "safety goggles",
  "date": the day they need it as YYYY-MM-DD; a weekday name means the next one on or after today,
  "start": when they need it from, as HH:MM in 24-hour time,
  "end": when they'll be done, as HH:MM, or null if they didn't say,
  "place": the building or room they named, copied as written, or null
}}"""

DEFAULT_LENGTH = timedelta(minutes=75)  # an ASU exam slot, when they don't say when they're done


class Parsed(BaseModel):
    found: bool
    item_type: str = ""
    date: str | None = None
    start: str | None = None
    end: str | None = None
    place: str | None = None

    @field_validator("item_type")
    @classmethod
    def _short(cls, v: str) -> str:
        return " ".join(v.split())[:40]


class Need(BaseModel):
    item_type: str
    window_start: datetime
    window_end: datetime
    building: str


class NotUnderstood(ValueError):
    """The text can't become a request. The message says what to add, in plain words."""


def to_need(parsed: Parsed) -> Need:
    if not parsed.found or not parsed.item_type:
        raise NotUnderstood("Tell me what you need to borrow, like “a TI-84 Thu 2pm in Durham Hall”.")
    if NOT_ALLOWED.search(parsed.item_type):
        raise NotUnderstood("Pass It On is for physical things like calculators and gear, not this.")
    try:
        day = date.fromisoformat(parsed.date or "")
        start = datetime.combine(day, datetime.strptime(parsed.start or "", "%H:%M").time(), TZ)
    except ValueError:
        raise NotUnderstood("When do you need it? Add a day and time, like “Thu 2pm”.") from None
    end = start + DEFAULT_LENGTH
    if parsed.end:
        try:
            end = datetime.combine(day, datetime.strptime(parsed.end, "%H:%M").time(), TZ)
        except ValueError:
            pass
    if end <= start:
        end = start + DEFAULT_LENGTH
    building = campus.find_building(parsed.place)
    if building is None:
        raise NotUnderstood("Which building? Handoffs happen in campus buildings, like Durham Hall or Hayden Library.")
    return Need(item_type=parsed.item_type, window_start=start, window_end=end, building=building)


async def read(text: str, today: date, client: anthropic.AsyncAnthropic | None = None) -> Need:
    system = SYSTEM.format(today=f"{today:%A}, {today.isoformat()}")
    parsed = await llm.ask(Parsed, system, [{"type": "text", "text": text[:500]}], max_tokens=1500, llm=client)
    return to_need(parsed)


if __name__ == "__main__":
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    need = asyncio.run(read(" ".join(sys.argv[1:]), datetime.now(TZ).date()))
    print(f"{need.item_type} · {need.window_start:%a %b %-d %-I:%M}–{need.window_end:%-I:%M %p} · {campus.name(need.building)}")
