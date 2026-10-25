"""Seeded demo world: Maya lends her TI-84 to Jay and Priya on one Thursday, and Dev asks for an overlapping time.

Everyone and everything here is made up. Calendars are the sample Canvas feeds in assets/samples/, and the photo is
the sample TI-84. The demo has its own database and clock (8:00 AM, Thu Oct 29, 2026), so the story works any day.

Scripted events for the /demo buttons: skip to the next handoff, Jay's exam moves an hour, Jay runs late, and Maya
moves out.
"""

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import calendar_feed, moveout
from .agents import Hub
from .models import TZ, CalEvent, Item, Leg, Message, Request, Rules, User

SAMPLES = Path(__file__).resolve().parents[1] / "assets" / "samples"
START = datetime(2026, 10, 29, 8, 0, tzinfo=TZ)
# (first name, rating, finished handoffs). Dev is new, so a mid-value item needs the owner's OK for him.
PEOPLE = {"maya": ("Maya", 1120, 9), "jay": ("Jay", 1080, 6), "priya": ("Priya", 1130, 11), "dev": ("Dev", 1000, 0)}
ITEM_ID = "ti84maya"
FINGERPRINT = "yellow smiley-face sticker on the bottom-right corner; small scratch on the top-left of the screen bezel"
# Who asks, when (minutes after 7:00 AM), so the earlier asker wins an overlap.
ASKS = (("jay", 0, "Exam 2"), ("priya", 5, "Exam 2"), ("dev", 10, "Quiz 4"))
SESSION = {person: f"demo-{person}" for person in PEOPLE}  # fixed tokens, so each /demo phone is signed in


def exam_uid(hub: Hub, person: str, title_start: str) -> CalEvent:
    return next(e for e in hub.store.all(CalEvent, user_id=person) if e.title.startswith(title_start))


async def seed(hub: Hub, photo_dir: Path) -> None:
    """Wipes the demo database and builds the world. Uses the template voice, so reset is instant."""
    model_voice, hub.voice_model = hub.voice_model, False
    for table in ("docs", "kv"):
        with hub.store.lock:
            hub.store.db.execute(f"DELETE FROM {table}")
            hub.store.db.commit()
    for person, (name, score, done) in PEOPLE.items():
        hub.store.put(User(id=person, name=name, rating=score, completed=done, demo=True,
                           friends=[p for p in PEOPLE if p != person]))
        for event in calendar_feed.parse((SAMPLES / f"{person}.ics").read_bytes(), person):
            hub.store.put(event)
        hub.store.set_setting(f"session:{SESSION[person]}", person)
    photo_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / "ti84.jpg", photo_dir / f"{ITEM_ID}.jpg")
    hub.store.put(Item(id=ITEM_ID, owner_id="maya", type="TI-84 Plus CE", category="calculator", value_tier="mid",
                       condition="good, light wear on the case", fingerprint=FINGERPRINT, persona="dramatic",
                       rules=Rules(return_by="17:00", home_building="MU", min_rating="ok", auto_approve=True),
                       photo=f"{ITEM_ID}.jpg", listed=True, created_at=START - timedelta(days=3)))
    for person, minutes, title in ASKS:
        hub.store.set_setting("clock", (START - timedelta(hours=1) + timedelta(minutes=minutes)).isoformat())
        exam = exam_uid(hub, person, title)
        await hub.request(person, "TI-84", exam.start, exam.end, exam.building, event_uid=exam.uid)
    # The asks above re-planned the day twice. A demo starts clean: one text each with their part.
    keep = {m.id for m in hub.store.all(Message) if m.kind == "waitlisted"}
    for m in hub.store.all(Message):
        if m.id not in keep:
            hub.store.delete(Message, m.id)
    hub.store.set_setting("clock", START.isoformat())
    await hub.brief(ITEM_ID)
    hub.voice_model = model_voice


async def next_handoff(hub: Hub) -> Leg | None:
    """Moves the demo clock to the next planned handoff, so a judge can do it on the phones."""
    legs = sorted((l for l in hub.store.all(Leg) if l.status in ("planned", "confirmed")), key=lambda l: l.pickup_time)
    if not legs:
        return None
    if legs[0].pickup_time > hub.now():
        hub.store.set_setting("clock", legs[0].pickup_time.isoformat())
    return legs[0]


async def finish_until(hub: Hub, when: datetime) -> None:
    """Does every handoff due before `when`, as if each scan happened on time, then sets the clock there."""
    while True:
        legs = sorted((l for l in hub.store.all(Leg) if l.status in ("planned", "confirmed") and l.pickup_time <= when),
                      key=lambda l: l.pickup_time)
        if not legs:
            break
        hub.store.set_setting("clock", legs[0].pickup_time.isoformat())
        await hub.transfer(legs[0].id)
    if hub.now() < when:
        hub.store.set_setting("clock", when.isoformat())


async def exam_plus_hour(hub: Hub) -> set[str]:
    return await hub.exam_moved(exam_uid(hub, "jay", "Exam 2").uid, timedelta(hours=1))


async def jay_late(hub: Hub) -> set[str]:
    """Jay finishes his exam with the calculator and is 30 minutes late to pass it on."""
    exam = exam_uid(hub, "jay", "Exam 2")
    await finish_until(hub, exam.end)
    return await hub.running_late("jay", 30)


async def evening(hub: Hub) -> None:
    """Every handoff happens on time and the calculator is home (used for recording the end of the day)."""
    await finish_until(hub, START.replace(hour=17, minute=30))


async def maya_moves_out(hub: Hub) -> None:
    """The day ends with the calculator home, then Maya moves out and it looks for its next owner."""
    await finish_until(hub, START.replace(hour=17, minute=30))
    await moveout.start(hub, "maya")


def waiting(hub: Hub) -> list[Request]:
    return [r for r in hub.store.all(Request) if r.status == "waitlisted"]
