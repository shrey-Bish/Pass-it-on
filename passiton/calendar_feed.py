"""Canvas calendar feed (.ics) -> exams, classes and blocked times. Plain parsing, no model.

Canvas gives every student a feed link on its Calendar page ("Calendar Feed"). Timed events whose title says exam,
midterm, final, quiz or test are exams. Assignment due dates (Canvas UIDs start with "event-assignment-") are skipped
for planning: nobody has to be anywhere for them.

Try it: python -m passiton.calendar_feed assets/samples/maya.ics
"""

import re
import sys
from datetime import date, datetime, timedelta

import httpx
from icalendar import Calendar

from . import campus
from .models import TZ, CalEvent

EXAM = re.compile(r"\b(exams?|midterms?|finals?|quiz(?:zes)?|tests?)\b", re.IGNORECASE)
COURSE = re.compile(r"\b([A-Z]{2,4})\s?-?\s?(\d{3}[A-Z]?)\b")


class FeedError(ValueError):
    """The file or link isn't a calendar we can read."""


def _local(value) -> datetime | None:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=TZ)).astimezone(TZ)
    return None  # an all-day date: no time to plan around


def parse(data: bytes | str, user_id: str) -> list[CalEvent]:
    try:
        calendar = Calendar.from_ical(data)
    except ValueError as e:
        raise FeedError("That isn't a calendar (.ics) file.") from e
    events = []
    for component in calendar.walk("VEVENT"):
        start = _local(component.decoded("DTSTART"))
        if start is None:
            continue
        if "DTEND" in component:
            end = _local(component.decoded("DTEND")) or start
        elif "DURATION" in component:
            end = start + component.decoded("DURATION")
        else:
            end = start
        uid = str(component.get("UID") or f"{user_id}-{start.isoformat()}")
        title = str(component.get("SUMMARY") or "").strip()
        location = str(component.get("LOCATION") or "").strip() or None
        if uid.startswith("event-assignment-") or end <= start:
            kind = "assignment"
        elif EXAM.search(title):
            kind = "exam"
        else:
            kind = "class"
        course = COURSE.search(title)
        events.append(CalEvent(
            uid=f"{user_id}:{uid}", user_id=user_id, title=title, kind=kind, start=start, end=end, location=location,
            course=f"{course.group(1)} {course.group(2)}" if course else None,
            building=campus.find_building(location),
        ))
    return sorted(events, key=lambda e: e.start)


async def fetch(url: str) -> bytes:
    """Downloads a Canvas feed link. Only https links, and only calendar files."""
    if not url.startswith("https://"):
        raise FeedError("Paste the https link from Canvas > Calendar > Calendar Feed.")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        response = await http.get(url)
    if response.status_code != 200 or b"BEGIN:VCALENDAR" not in response.content[:2000]:
        raise FeedError("That link didn't return a calendar. Copy it again from Canvas > Calendar > Calendar Feed.")
    return response.content


def exams(events: list[CalEvent]) -> list[CalEvent]:
    return [e for e in events if e.kind == "exam"]


def busy(events: list[CalEvent]) -> list[CalEvent]:
    """Times someone has to be somewhere: exams and classes, not due dates."""
    return [e for e in events if e.kind in ("exam", "class")]


def on_day(events: list[CalEvent], day: date) -> list[CalEvent]:
    return [e for e in events if e.start.date() == day]


def describe(event: CalEvent) -> str:
    where = campus.name(event.building) if event.building else (event.location or "no room listed")
    return f"{event.start:%a %b %-d}, {event.start:%-I:%M}–{event.end:%-I:%M %p} · {event.course or event.title} · {where}"


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, "rb") as f:
            found = parse(f.read(), "me")
        print(f"{path}: {len(found)} events")
        for e in found:
            print(f"  {e.kind:<10} {describe(e)}")
        print("  Blocked for lending (exams):", "; ".join(describe(e) for e in exams(found)) or "none")
        _ = timedelta  # keeps the import for callers that shift sample dates
