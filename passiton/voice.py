"""The item's voice: in-character texts written per recipient from structured facts only.

The model gets the event, the persona and a few facts (first names, a building, a time), never anyone's schedule
or reasons. Its reply is checked: no other student's name, short enough for a text. If it fails the check, or the
model is down, a template in the same persona is used, so a text always goes out. Nudges stop at MAX_NUDGES; after
that the owner is told, privately.
"""

import logging
import os
import re

import anthropic
from pydantic import BaseModel

from . import llm
from .models import Item

log = logging.getLogger("passiton")

MAX_NUDGES = 3
MAX_LENGTH = 320
EMOJI = {"calculator": "🧮", "lab_gear": "🥽", "clothing": "👔", "electronics": "📷", "tools": "🧰", "books": "📘"}

SYSTEM = """You write one short text message in the voice of a lent item, to the student holding or getting it.

Persona: {persona}.
- dramatic: theatrical, a little over the top, funny
- chill: relaxed, low-key, few words
- wholesome: warm, kind, encouraging

Rules:
- Say what the facts say. Keep every time, building and first name exactly as given. Add no other names, places or times.
- Under 280 characters. At most one emoji. No hashtags.
- Never shame or scold. Nudges are playful, never guilt-tripping.

Reply with only a JSON object: {{"text": "..."}}"""

TEMPLATES = {
    "your_pickup": {
        "dramatic": "It's {owner}'s {thing} {emoji}! Our story continues: meet {other} at {building} at {time}. I'll be waiting, breathlessly.",
        "chill": "Hey, {owner}'s {thing} here {emoji}. Grab me from {other} at {building}, {time}.",
        "wholesome": "Hi! It's {owner}'s {thing} {emoji}. {other} will hand me to you at {building} at {time}. Good luck today!",
    },
    "your_dropoff": {
        "dramatic": "{owner}'s {thing} {emoji} again. {other} needs me at {building} at {time}. Don't leave me hanging!",
        "chill": "{owner}'s {thing} {emoji}. {other} needs me at {building} at {time}. Mind dropping me off?",
        "wholesome": "Hey, it's {owner}'s {thing} {emoji}. {other} needs me at {building} at {time}. Thanks for passing me on!",
    },
    "your_return": {
        "dramatic": "Time to go home! {owner} meets you at {building} at {time}. What an adventure {emoji}.",
        "chill": "Heading home: {owner} at {building}, {time} {emoji}.",
        "wholesome": "Last stop! Please bring me back to {owner} at {building} at {time}. Thank you {emoji}",
    },
    "owner_handoff": {
        "dramatic": "Off on an adventure {emoji}! Please hand me to {other} at {building} at {time}.",
        "chill": "Hand me to {other} at {building}, {time} {emoji}.",
        "wholesome": "I'm helping {other} today {emoji}. Hand me over at {building} at {time}, and I'll be home by {return_by}.",
    },
    "owner_back": {
        "dramatic": "Home sweet home! {other} brings me back at {building} at {time} {emoji}.",
        "chill": "Coming home: {other}, {building}, {time} {emoji}.",
        "wholesome": "{other} will bring me home at {building} at {time} {emoji}. Thanks for sharing me!",
    },
    "plan_changed": {
        "dramatic": "Plot twist {emoji}! New plan: {building} at {time}. Tap to confirm.",
        "chill": "Plan changed {emoji}: {building} at {time}. Confirm when you can.",
        "wholesome": "Small change {emoji}: we'll meet at {building} at {time} now. Please confirm. Thank you!",
    },
    "waitlisted": {
        "dramatic": "Alas {emoji}, someone already has a {thing} then. I'm watching for another and will text you.",
        "chill": "No {thing} free then yet {emoji}. I'll keep looking.",
        "wholesome": "Every {thing} is busy then right now {emoji}. I'll keep looking and text you if one frees up.",
    },
    "owner_ask": {
        "dramatic": "{other} wants to borrow me {emoji} ({window}, {building}). Say yes?",
        "chill": "{other} wants me {window} at {building} {emoji}. OK?",
        "wholesome": "{other} would like to borrow me {window} at {building} {emoji}. Is that OK?",
    },
    "nudge": {
        "dramatic": "{owner}'s {thing} {emoji} here. Nudge {n} of 3: I'm still with you, and {other} is waiting at {building}. Please rescue me!",
        "chill": "Nudge {n} {emoji}: {other}'s waiting at {building}.",
        "wholesome": "Hi! Just a reminder ({n} of 3) {emoji}: {other} is waiting for me at {building}. No stress!",
    },
    "owner_late": {
        "dramatic": "{emoji} I've nudged 3 times and I'm still not where I should be. I'm working on a new route.",
        "chill": "{emoji} Running late after 3 nudges. Re-routing.",
        "wholesome": "{emoji} I'm running late today even after 3 reminders. I'm finding a new route, and I'll keep you posted.",
    },
    "handoff_done": {
        "dramatic": "{emoji} I'm with {other} now. Onward!",
        "chill": "{emoji} With {other} now.",
        "wholesome": "{emoji} I'm with {other} now. Thanks for keeping me safe!",
    },
    "checkin_mismatch": {
        "dramatic": "{emoji} Hmm. The photo {other} took doesn't look like me. Can you check?",
        "chill": "{emoji} {other}'s check-in photo doesn't match me. Worth a look.",
        "wholesome": "{emoji} The photo from {other}'s check-in doesn't quite match me. Could you take a look?",
    },
    "back_home": {
        "dramatic": "{emoji} I'm HOME. {n} exams today. {n} people didn't have to buy one.",
        "chill": "{emoji} Back home. {n} exams today.",
        "wholesome": "{emoji} I'm back home! I went to {n} exams today. {n} people didn't have to buy one.",
    },
    "recap": {
        "dramatic": "{emoji} This week I went to {n} exams. {n} people didn't have to buy one. I'm basically famous.",
        "chill": "{emoji} This week: {n} exams. {n} people didn't have to buy one.",
        "wholesome": "{emoji} Your {thing} went to {n} exams this week. {n} people didn't have to buy one.",
    },
    "moveout_offer": {
        "dramatic": "{emoji} {owner}'s moving out, and I'd like to stay at ASU. You have {course} coming up. Want me, forever?",
        "chill": "{emoji} {owner}'s moving out. You have {course} coming up. Want to keep me?",
        "wholesome": "{emoji} {owner}'s moving out, and I'd like to stay at ASU. You have {course} coming up. Want me?",
    },
    "new_home": {
        "dramatic": "{emoji} {owner} graduated. I live with {other} now 💛",
        "chill": "{emoji} {owner} graduated. I live with {other} now 💛",
        "wholesome": "{emoji} {owner} graduated. I live with {other} now 💛",
    },
    "donate": {
        "dramatic": "{emoji} Nobody's claimed me yet. Please drop me in an ASU Ditch the Dumpster bin or at Goodwill, so I can help someone else.",
        "chill": "{emoji} No taker yet. ASU Ditch the Dumpster bins or Goodwill would be great.",
        "wholesome": "{emoji} No one needs me right now. Could you drop me at an ASU Ditch the Dumpster bin or Goodwill? Someone will.",
    },
}


class Text(BaseModel):
    text: str


def thing(item: Item) -> str:
    return "calculator" if item.category == "calculator" else item.type


def template(event: str, item: Item, facts: dict) -> str:
    values = {"thing": thing(item), "emoji": EMOJI.get(item.category, "📦"), "owner": "", "other": "",
              "building": "", "time": "", "return_by": "", "window": "", "course": "", "n": ""} | facts
    return TEMPLATES[event][item.persona].format(**values)


def acceptable(text: str, facts: dict, other_names: set[str]) -> bool:
    """No student named except the ones in the facts, the time and building kept exactly, and short enough."""
    if not text or len(text) > MAX_LENGTH:
        return False
    if any(facts.get(k) and str(facts[k]) not in text for k in ("time", "building")):
        return False
    allowed = {str(facts.get(k, "")) for k in ("owner", "other")}
    words = set(re.findall(r"\b[A-Z][a-z]+\b", text))
    return not (words & (other_names - allowed))


async def say(event: str, item: Item, facts: dict, *, other_names: set[str] = frozenset(),
              use_model: bool | None = None, client: anthropic.AsyncAnthropic | None = None) -> str:
    """One text for one recipient. other_names: every student's first name, to catch a model adding one."""
    fallback = template(event, item, facts)
    if use_model is None:
        use_model = os.environ.get("PASSITON_VOICE", "model") == "model" and bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not use_model:
        return fallback
    prompt = (f"Event: {event}\nItem: {item.type}\nFacts: {facts}\n"
              f"Meaning to keep (rewrite it in your persona): {fallback}")
    try:
        text = (await llm.ask(Text, SYSTEM.format(persona=item.persona), [{"type": "text", "text": prompt}],
                              max_tokens=800, llm=client)).text.strip()
    except (anthropic.APIError, llm.ModelError):
        log.warning("Voice model unavailable; using the template")
        return fallback
    return text if acceptable(text, facts, set(other_names)) else fallback
