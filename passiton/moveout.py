"""Move-out mode: when an owner moves out or graduates, each item finds its next owner instead of its next borrower.

Who gets it is plain code: whoever's need comes soonest (an upcoming exam in a course that uses this kind of item,
or a request still waiting), then whoever asked first. Nobody's reason is shown to anyone. The new owner says yes
before anything changes. If nobody does, the owner gets donation options.
"""

from datetime import datetime

from .agents import CALCULATOR_COURSES, Hub, type_matches
from .calendar_feed import exams as exam_events
from .models import CalEvent, Item, Request, User

LAB_COURSES = ("CHM", "BIO", "PHY", "MIC", "BCH")
COURSES_FOR = {"calculator": CALCULATOR_COURSES, "lab_gear": LAB_COURSES}
DONATION = "ASU's Ditch the Dumpster bins during move-out, or Goodwill"


def candidates(hub: Hub, item: Item) -> list[tuple[str, str]]:
    """(student id, what to call their need) in order: soonest need first, then earliest ask."""
    now = hub.now()
    declined = set((hub.store.setting(f"moveout_declined:{item.id}") or "").split(",")) - {""}
    best: dict[str, tuple[datetime, datetime, str]] = {}

    def consider(user_id: str, need_at: datetime, asked_at: datetime, label: str) -> None:
        if user_id == item.owner_id or user_id in declined or need_at < now:
            return
        if any(type_matches(item.type, i) for i in hub.store.all(Item, owner_id=user_id)):
            return  # they already have one
        if user_id not in best or (need_at, asked_at) < best[user_id][:2]:
            best[user_id] = (need_at, asked_at, label)

    for req in hub.store.all(Request):
        if req.status in ("pending", "waitlisted", "planned", "confirmed") and type_matches(req.item_type, item):
            event = hub.store.get(CalEvent, req.event_uid) if req.event_uid else None
            consider(req.borrower_id, req.window_start, req.created_at, event.course if event and event.course else "an exam")
    courses = COURSES_FOR.get(item.category, ())
    never_asked = datetime(2100, 1, 1, tzinfo=now.tzinfo)  # an exam alone ranks after anyone who asked
    for event in exam_events(hub.store.all(CalEvent)):
        if event.course and event.course.split()[0] in courses:
            consider(event.user_id, event.start, never_asked, event.course)
    ranked = sorted(best.items(), key=lambda kv: kv[1][:2])
    return [(user_id, label) for user_id, (_, _, label) in ranked]


async def start(hub: Hub, owner_id: str) -> None:
    """The owner is moving out: every item they own switches to finding a new owner."""
    for item in hub.store.all(Item, owner_id=owner_id):
        item.mode = "give_away"
        hub.store.put(item)
        await offer_next(hub, item.id)


async def offer_next(hub: Hub, item_id: str) -> str | None:
    item = hub.store.get(Item, item_id)
    owner = hub.store.get(User, item.owner_id)
    for user_id, course in candidates(hub, item):
        hub.store.set_setting(f"moveout_offer:{item.id}", user_id)
        await hub.say(user_id, "moveout_offer", item, {"owner": owner.name, "course": course}, kind="moveout")
        await hub.plain(owner.id, f"I'm finding your {item.type} a new home at ASU. I'll tell you who says yes.")
        return user_id
    hub.store.set_setting(f"moveout_offer:{item.id}", None)
    await hub.say(owner.id, "donate", item, {"owner": owner.name}, kind="moveout")
    return None


async def answer(hub: Hub, item_id: str, user_id: str, yes: bool) -> bool:
    """The student who was offered the item says yes (it's theirs) or no (the next one is asked)."""
    item = hub.store.get(Item, item_id)
    if item is None or item.mode != "give_away" or hub.store.setting(f"moveout_offer:{item_id}") != user_id:
        return False
    hub.store.set_setting(f"moveout_offer:{item_id}", None)
    if not yes:
        declined = (hub.store.setting(f"moveout_declined:{item_id}") or "").split(",")
        hub.store.set_setting(f"moveout_declined:{item_id}", ",".join(d for d in declined + [user_id] if d))
        await offer_next(hub, item_id)
        return True
    old, new = hub.store.get(User, item.owner_id), hub.store.get(User, user_id)
    item.owner_id, item.mode, item.holder_id = new.id, "lend", None
    hub.store.put(item)
    for req in hub.store.all(Request, item_id=item_id):
        if req.status in ("pending", "planned", "confirmed"):
            req.status, req.item_id = "waitlisted", None
            hub.store.put(req)
    for person in (old.id, new.id):
        await hub.say(person, "new_home", item, {"owner": old.name, "other": new.name}, kind="moveout")
    return True
