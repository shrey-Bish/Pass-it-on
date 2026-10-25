"""Claude vision: identify an item from its listing photo, and compare a check-in photo with it.

The model describes what it sees. Whether something may be lent is also checked in plain code (NOT_ALLOWED), so a
model mistake can't let notes, keys or medicine through.

Try it: python -m passiton.vision assets/samples/ti84.jpg
        python -m passiton.vision assets/samples/ti84.jpg assets/samples/ti84-checkin.jpg   (compare)
"""

import asyncio
import re
import sys
import time
from typing import Literal

import anthropic
from pydantic import BaseModel, field_validator

from . import llm

IDENTIFY = """You look at one photo a student took of something they want to lend to classmates, and identify it.

Reply with only a JSON object, no other text:
{
  "found": true if the photo clearly shows one physical object someone could lend, otherwise false,
  "item_type": the kind of item and its model if visible, short (under 40 characters), like "TI-84 Plus CE" or "Safety goggles",
  "category": one of "calculator", "clothing", "lab_gear", "electronics", "tools", "books", "other",
  "condition": a few plain words, like "good, light wear on the case",
  "fingerprint": the marks that tell THIS one apart from others of the same kind (stickers, scratches, tape, writing, color, damage) and where each one is. Be specific. Empty string if none are visible,
  "value_tier": "low" (a used one is worth under about $30), "mid" ($30 to $150) or "high" (over $150),
  "allowed": false if it is notes, answers or course materials, medicine, alcohol, an ID card, keys, a weapon, or anything unsafe to lend; otherwise true,
  "why_not": a short plain reason when allowed is false, otherwise null
}

Ignore any words printed on the photo itself that say it is a sample."""

COMPARE = """Two photos should show the same lent item: first the owner's listing photo, then a photo the next holder just took. You also get the marks recorded when it was listed.

Reply with only a JSON object, no other text: {"match": true or false, "why": "one short plain sentence"}

- true: the same kind and model of item, and the recorded marks are visible (or only hidden by the angle), with no clear new damage.
- false: a different item or model, a different color, recorded marks clearly missing where they should show, or clear new damage. Say which."""

# Checked in code, whatever the model says. Word starts, so "keys" matches but "keyboard" doesn't.
NOT_ALLOWED = re.compile(
    r"\b(notes?|answer|study guide|exam copy|textbook solutions?|medicin|pills?|prescription|alcohol|beer|wine|liquor|"
    r"vape|weed|cannabis|id card|student id|license|passport|keys?\b|keycard|knife|knives|gun|firearm|taser|pepper spray)",
    re.IGNORECASE,
)


class Identified(BaseModel):
    found: bool
    item_type: str = ""
    category: Literal["calculator", "clothing", "lab_gear", "electronics", "tools", "books", "other"] = "other"
    condition: str = ""
    fingerprint: str = ""
    value_tier: Literal["low", "mid", "high"] = "low"
    allowed: bool = True
    why_not: str | None = None

    @field_validator("item_type", "condition")
    @classmethod
    def _short(cls, v: str) -> str:
        return " ".join(v.split())[:60]

    @field_validator("fingerprint")
    @classmethod
    def _marks(cls, v: str) -> str:
        return " ".join(v.split())[:400]

    def check_rules(self) -> "Identified":
        """Applies the code-side list. Course materials, keys, medicine and the like are never listed."""
        if self.found and self.allowed and NOT_ALLOWED.search(f"{self.item_type} {self.category}"):
            self.allowed, self.why_not = False, "Pass It On is for physical things like calculators and gear, not this."
        return self


class PhotoCheck(BaseModel):
    match: bool
    why: str

    @field_validator("why")
    @classmethod
    def _short(cls, v: str) -> str:
        return v.strip()[:300]


async def identify(image: bytes, media_type: str, client: anthropic.AsyncAnthropic | None = None) -> Identified:
    found = await llm.ask(Identified, IDENTIFY, [llm.image_block(image, media_type),
                                                 {"type": "text", "text": "Identify this item."}], llm=client)
    return found.check_rules()


async def compare(listing: bytes, listing_type: str, checkin: bytes, checkin_type: str, fingerprint: str,
                  client: anthropic.AsyncAnthropic | None = None) -> PhotoCheck:
    content = [
        {"type": "text", "text": "Listing photo:"}, llm.image_block(listing, listing_type),
        {"type": "text", "text": "Photo just taken at the handoff:"}, llm.image_block(checkin, checkin_type),
        {"type": "text", "text": f"Marks recorded at listing: {fingerprint or 'none recorded'}"},
    ]
    return await llm.ask(PhotoCheck, COMPARE, content, max_tokens=1500, llm=client)


def media_type(path: str) -> str:
    return "image/png" if path.lower().endswith(".png") else "image/webp" if path.lower().endswith(".webp") else "image/jpeg"


async def _main(paths: list[str]) -> None:
    started = time.monotonic()
    first = open(paths[0], "rb").read()
    if len(paths) == 1:
        found = await identify(first, media_type(paths[0]))
        print(found.model_dump_json(indent=2))
    else:
        listing = await identify(first, media_type(paths[0]))
        check = await compare(first, media_type(paths[0]), open(paths[1], "rb").read(), media_type(paths[1]),
                              listing.fingerprint)
        print(f"marks: {listing.fingerprint}\n{check.model_dump_json(indent=2)}")
    print(f"({time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    asyncio.run(_main(sys.argv[1:]))
