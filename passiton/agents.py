"""Owner, borrower and item agents, and the hub that carries messages between them through the gate.

- Borrower agent: turns its student's need into {item_type, window, building, rating_bucket}. Never why.
- Item agent: one per item. Plans its route with scheduler.py and tells each person only their own part.
- Owner agent: applies the owner's rules (rating, audience, auto-approve) and asks the owner when it can't decide.

Every agent-to-agent message goes through gate.cross(). What reaches a student is a text written from their own part
only: first names and buildings, never anyone's schedule or reasons.
"""

import re
import uuid
from datetime import date, datetime, time, timedelta

from . import campus, rating, scheduler, voice
from .calendar_feed import busy as busy_events
from .calendar_feed import exams as exam_events
from .gate import Gate
from .messenger import Messenger, notify
from .models import TZ, CalEvent, Item, Leg, Message, Request, User
from .store import Store

ACTIVE = ("planned", "confirmed", "active")
LIVE_LEGS = ("planned", "confirmed")
# Courses whose exams usually allow (and need) a calculator: the borrower agent offers to find one.
CALCULATOR_COURSES = ("MAT", "STP", "CHM", "PHY", "EEE", "ECN", "MAE", "CEE")
CATEGORY_WORDS = {"calculator": "calculator", "goggles": "lab_gear", "goggle": "lab_gear", "lab coat": "lab_gear",
                  "blazer": "clothing", "suit": "clothing", "camera": "electronics", "tripod": "electronics",
                  "hand truck": "tools", "dolly": "tools"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def type_matches(wanted: str, item: Item) -> bool:
    """'TI-84' or 'graphing calculator' matches a TI-84 Plus CE; 'TI-30' doesn't."""
    w, t = normalize(wanted), normalize(item.type)
    if not w:
        return False
    if w in t or t in w:
        return True
    numbers = set(re.findall(r"\d+", w))
    if numbers and not numbers <= set(re.findall(r"\d+", t)):
        return False
    return any(word in w for word, category in CATEGORY_WORDS.items() if category == item.category)


def clock(t: datetime) -> str:
    return f"{t:%-I:%M %p}"


class Hub:
    def __init__(self, store: Store, messenger: Messenger | None = None, *, voice_model: bool | None = None):
        self.store = store
        self.messenger = messenger or Messenger()
        self.gate = Gate()
        self.voice_model = voice_model

    # ---- basics

    def now(self) -> datetime:
        """Real time, or the demo's clock when it's set."""
        value = self.store.setting("clock")
        return datetime.fromisoformat(value) if value else datetime.now(TZ)

    def user(self, user_id: str) -> User:
        return self.store.get(User, user_id)

    def _cross(self, message: dict, sender: str, receiver: str) -> dict:
        self.gate.known_types = {i.type for i in self.store.all(Item) if i.listed}
        return self.gate.cross(message, sender=sender, receiver=receiver)

    async def say(self, user_id: str, event: str, item: Item, facts: dict, kind: str = "info") -> Message:
        names = {u.name for u in self.store.all(User)}
        body = await voice.say(event, item, facts, other_names=names, use_model=self.voice_model)
        return await notify(self.store, self.messenger, self.user(user_id), body, item_id=item.id, kind=kind,
                            now=self.now())

    async def plain(self, user_id: str, body: str, kind: str = "info") -> Message:
        return await notify(self.store, self.messenger, self.user(user_id), body, kind=kind, now=self.now())

    # ---- borrower agent

    async def request(self, borrower_id: str, item_type: str, start: datetime, end: datetime, building: str,
                      event_uid: str | None = None) -> Request:
        if not campus.is_building(building):
            raise ValueError("handoffs happen only in public campus buildings")
        req = Request(id=new_id(), borrower_id=borrower_id, item_type=item_type, window_start=start, window_end=end,
                      building=building, created_at=self.now(), event_uid=event_uid)
        self.store.put(req)
        await self.find_item(req)
        return self.store.get(Request, req.id)

    def candidates(self, req: Request) -> list[Item]:
        borrower = self.user(req.borrower_id)
        items = [i for i in self.store.all(Item) if i.listed and i.mode == "lend" and i.owner_id != req.borrower_id
                 and i.id not in req.declined_by and type_matches(req.item_type, i) and self._audience_ok(i, borrower)]
        # Items this borrower already qualifies for first, then the one that lives closest to where they'll be.
        return sorted(items, key=lambda i: (not rating.may_borrow(borrower, i),
                                            campus.walk_minutes(i.rules.home_building, req.building), i.created_at))

    def _audience_ok(self, item: Item, borrower: User) -> bool:
        if item.rules.audience == "asu":
            return True
        owner = self.user(item.owner_id)
        if borrower.id in owner.friends:
            return True
        return item.rules.audience == "friends_of_friends" and any(
            borrower.id in (self.user(f).friends if self.user(f) else []) for f in owner.friends)

    async def find_item(self, req: Request) -> None:
        """Asks each matching item's agent in turn. The first that fits (and whose owner agrees) gets the request."""
        borrower = self.user(req.borrower_id)
        for item in self.candidates(req):
            need = self._cross({"kind": "need", "item_type": item.type, "window_start": req.window_start,
                                "window_end": req.window_end, "building_code": req.building,
                                "rating_bucket": rating.bucket(borrower)},
                               sender=f"borrower:{borrower.id}", receiver=f"item:{item.id}")
            if not self.item_fits(item, req):
                continue
            req.item_id = item.id
            if rating.may_borrow(borrower, item) and item.rules.auto_approve:
                req.status = "planned"
                self.store.put(req)
                await self.replan(item.id)
            else:
                req.status = "pending"
                self.store.put(req)
                self._cross({"kind": "offer", "item_id": item.id, "window_start": need["window_start"],
                             "window_end": need["window_end"], "building_code": need["building_code"],
                             "rating_bucket": need["rating_bucket"]},
                            sender=f"item:{item.id}", receiver=f"owner:{item.owner_id}")
                await self.say(item.owner_id, "owner_ask", item, {
                    "other": borrower.name, "building": campus.name(req.building),
                    "window": f"{req.window_start:%a} {clock(req.window_start)}–{clock(req.window_end)}"}, kind="ask")
                await self.plain(req.borrower_id, f"Found one! Waiting for its owner to say yes.")
            return
        req.status, req.item_id = "waitlisted", None
        self.store.put(req)
        await self.plain(req.borrower_id, f"No {req.item_type} is free {req.window_start:%a} at {clock(req.window_start)} "
                                          "yet. I'll keep looking and text you if one frees up.", kind="waitlisted")

    def offers(self, user_id: str) -> list[CalEvent]:
        """Upcoming exams that usually need a calculator, when this student has none and hasn't asked yet."""
        now = self.now()
        if any(i.category == "calculator" for i in self.store.all(Item, owner_id=user_id)):
            return []
        asked = {r.event_uid for r in self.store.all(Request, borrower_id=user_id) if r.status != "cancelled"}
        return [e for e in exam_events(self.store.all(CalEvent, user_id=user_id))
                if e.start > now and e.start - now < timedelta(days=7) and e.uid not in asked and e.building
                and e.course and e.course.split()[0] in CALCULATOR_COURSES]

    # ---- owner agent

    async def approve(self, request_id: str, owner_id: str, yes: bool) -> None:
        req = self.store.get(Request, request_id)
        item = self.store.get(Item, req.item_id) if req and req.item_id else None
        if item is None or item.owner_id != owner_id or req.status != "pending":
            return
        self._cross({"kind": "confirm" if yes else "decline", "item_id": item.id, "confirm": yes},
                    sender=f"owner:{owner_id}", receiver=f"borrower:{req.borrower_id}")
        if yes:
            req.status = "planned"
            self.store.put(req)
            await self.replan(item.id)
        else:
            # The borrower never hears why. Their agent just tries the next lender.
            req.declined_by.append(item.id)
            req.item_id, req.status = None, "pending"
            self.store.put(req)
            await self.find_item(req)

    # ---- item agent

    def _world(self, item: Item, day: date, extra: Request | None = None):
        now = self.now()
        holder = item.holder_id or item.owner_id
        requests = [r for r in self.store.all(Request, item_id=item.id)
                    if r.status in ACTIVE and r.window_start.date() == day]
        if extra:
            requests.append(extra)
        since = now if now.date() == day and now.time() > scheduler.DAY_START else None
        place = None
        if holder != item.owner_id:
            current = [l for l in self.store.all(Leg, item_id=item.id) if l.status == "active" and l.holder_id == holder]
            if current:
                mine = self.store.get(Request, current[0].request_id) if current[0].request_id else None
                place = mine.building if mine and since and mine.window_start <= since else current[0].pickup_building
        people = {item.owner_id, holder} | {r.borrower_id for r in requests}
        busy = {}
        for person in people:
            events = [e for e in busy_events(self.store.all(CalEvent, user_id=person)) if e.start.date() == day]
            busy[person] = [scheduler.Busy(e.start, e.end, e.building) for e in events]
            if (late := self.store.setting(f"delay:{person}")) and since:
                until = datetime.fromisoformat(late)
                if until > since:
                    busy[person].append(scheduler.Busy(since, until, None))
        owner_events = self.store.all(CalEvent, user_id=item.owner_id)
        owner_exams = [scheduler.Need(item.owner_id, e.start, e.end, e.building or item.rules.home_building)
                       for e in exam_events(owner_events) if e.start.date() == day]
        needs = []
        for r in requests:
            start = r.window_start
            if since and r.borrower_id == holder and start < since:
                start = since  # they already have it; the rest of their window still counts
            needs.append(scheduler.Need(r.borrower_id, start, r.window_end, r.building, r.id, r.created_at))
        world = scheduler.World(owner_id=item.owner_id, home=item.rules.home_building, holder_id=holder, place=place,
                                return_by=datetime.combine(day, time.fromisoformat(item.rules.return_by), TZ),
                                busy=busy, since=since)
        return world, owner_exams, needs

    def item_fits(self, item: Item, req: Request) -> bool:
        trial = req.model_copy(update={"status": "planned", "item_id": item.id})
        world, owner_exams, needs = self._world(item, req.window_start.date(), extra=trial)
        others = [n for n in needs if n.request_id != req.id]
        mine = [n for n in needs if n.request_id == req.id]
        return req.id not in scheduler.plan(world, owner_exams, others + mine).waitlisted

    def legs(self, item_id: str, statuses=LIVE_LEGS) -> list[Leg]:
        return sorted((l for l in self.store.all(Leg, item_id=item_id) if l.status in statuses),
                      key=lambda l: l.pickup_time)

    async def replan(self, item_id: str) -> set[str]:
        """Plans the item's day again. Keeps legs that didn't change (and their confirmations). Texts only the
        people whose part changed, and only their part. Returns who that was."""
        item = self.store.get(Item, item_id)
        now = self.now()
        days = sorted({r.window_start.date() for r in self.store.all(Request, item_id=item_id) if r.status in ACTIVE})
        days = days or [now.date()]
        affected: set[str] = set()
        for day in days:
            world, owner_exams, needs = self._world(item, day)
            result = scheduler.plan(world, owner_exams, needs)
            dropped = []
            for request_id in result.waitlisted:
                req = self.store.get(Request, request_id)
                req.status, req.item_id = "waitlisted", None
                self.store.put(req)
                dropped.append(req)
            old = [l for l in self.legs(item_id) if l.pickup_time.date() == day]
            old_stops = [scheduler.Stop(l.holder_id, l.giver_id, l.pickup_building, l.pickup_time, l.start, l.end,
                                        l.request_id) for l in old]
            by_key = {(l.holder_id, l.giver_id, l.pickup_building, l.pickup_time): l for l in old}
            fresh = []
            for stop in result.stops:
                leg = by_key.pop((stop.holder_id, stop.giver_id, stop.pickup_building, stop.pickup_time), None)
                if leg is None:
                    leg = Leg(id=new_id(), item_id=item_id, holder_id=stop.holder_id, giver_id=stop.giver_id,
                              start=stop.start, end=stop.end, pickup_building=stop.pickup_building,
                              pickup_time=stop.pickup_time, request_id=stop.request_id)
                leg.start, leg.end = stop.start, stop.end
                self.store.put(leg)
                fresh.append(leg)
            for leg in by_key.values():
                leg.status = "cancelled"
                self.store.put(leg)
            changed = scheduler.changed(old_stops, result.stops)
            for person in sorted(changed):
                await self._tell_part(item, person, fresh, again=bool(old))
            for req in dropped:
                await self.plain(req.borrower_id, f"Plans changed, and the {req.item_type} can't reach you "
                                 f"{req.window_start:%a} at {clock(req.window_start)} anymore. I'm looking for another.",
                                 kind="waitlisted")
                await self.find_item(req)
            affected |= changed | {r.borrower_id for r in dropped}
        return affected

    async def brief(self, item_id: str) -> None:
        """Tells everyone on today's route their own first part, fresh (not as a change)."""
        legs = self.legs(item_id)
        for person in sorted({p for l in legs for p in (l.holder_id, l.giver_id)}):
            await self._tell_part(self.store.get(Item, item_id), person, legs, again=False)

    def inbox(self, user_id: str) -> list[Message]:
        return sorted(self.store.all(Message, user_id=user_id), key=lambda m: m.id)

    async def _tell_part(self, item: Item, person: str, legs: list[Leg], again: bool) -> None:
        """One text with this person's next part, after it crosses the gate as structured fields."""
        mine = [l for l in legs if person in (l.holder_id, l.giver_id)]
        if not mine:
            return
        leg = mine[0]
        self._cross({"kind": "plan", "item_id": item.id, "item_type": item.type, "window_start": leg.pickup_time,
                     "window_end": leg.pickup_time + scheduler.HANDOFF, "building_code": leg.pickup_building},
                    sender=f"item:{item.id}", receiver=f"{'owner' if person == item.owner_id else 'borrower'}:{person}")
        owner = self.user(item.owner_id).name
        facts = {"owner": owner, "building": campus.name(leg.pickup_building), "time": clock(leg.pickup_time),
                 "return_by": clock(datetime.combine(leg.pickup_time.date(), time.fromisoformat(item.rules.return_by)))}
        if again:
            event = "plan_changed"
        elif person == item.owner_id:
            event = "owner_back" if leg.holder_id == person else "owner_handoff"
        elif leg.holder_id == person:
            event = "your_pickup"
        else:
            event = "your_return" if leg.holder_id == item.owner_id else "your_dropoff"
        facts["other"] = self.user(leg.giver_id if leg.holder_id == person else leg.holder_id).name
        await self.say(person, event, item, facts, kind="plan")

    # ---- confirmations, custody, lateness

    def confirm(self, leg_id: str, user_id: str) -> Leg | None:
        leg = self.store.get(Leg, leg_id)
        if leg is None or user_id not in (leg.holder_id, leg.giver_id) or leg.status not in LIVE_LEGS:
            return None
        if user_id not in leg.confirmed_by:
            leg.confirmed_by.append(user_id)
        if {leg.holder_id, leg.giver_id} <= set(leg.confirmed_by):
            leg.status = "confirmed"
            if leg.request_id and (req := self.store.get(Request, leg.request_id)) and req.status == "planned":
                req.status = "confirmed"
                self.store.put(req)
        self.store.put(leg)
        return leg

    async def transfer(self, leg_id: str) -> Leg:
        """The handoff happened (scan, code or photo): custody moves, the giver's rating updates."""
        leg = self.store.get(Leg, leg_id)
        item = self.store.get(Item, leg.item_id)
        now = self.now()
        for previous in self.store.all(Leg, item_id=item.id, status="active"):
            previous.status = "done"
            self.store.put(previous)
            if previous.request_id and (req := self.store.get(Request, previous.request_id)):
                req.status = "done"
                self.store.put(req)
        home = leg.holder_id == item.owner_id
        leg.status = "done" if home else "active"
        self.store.put(leg)
        item.holder_id = None if home else leg.holder_id
        self.store.put(item)
        if leg.request_id and (req := self.store.get(Request, leg.request_id)):
            req.status = "active"
            self.store.put(req)
        giver = self.user(leg.giver_id)
        if giver.id != item.owner_id:
            rating.record(giver, (now - leg.pickup_time).total_seconds() / 60, is_return=home)
            self.store.put(giver)
        self.store.set_setting(f"delay:{giver.id}", None)
        self.store.set_setting(f"nudges:{leg.id}", None)
        if home:
            served = self.exams_served(item.id, now - timedelta(hours=20))
            await self.say(item.owner_id, "back_home", item, {"owner": self.user(item.owner_id).name, "n": served})
        else:
            await self.say(leg.giver_id, "handoff_done", item, {"other": self.user(leg.holder_id).name})
        return leg

    async def nudge(self, leg_id: str) -> str | None:
        """A playful reminder to whoever should hand it on. After the third, the owner hears, privately."""
        leg = self.store.get(Leg, leg_id)
        item = self.store.get(Item, leg.item_id)
        count = int(self.store.setting(f"nudges:{leg_id}", "0"))
        if count >= voice.MAX_NUDGES:
            return None
        count += 1
        self.store.set_setting(f"nudges:{leg_id}", str(count))
        await self.say(leg.giver_id, "nudge", item, {"owner": self.user(item.owner_id).name, "n": count,
                                                    "other": self.user(leg.holder_id).name,
                                                    "building": campus.name(leg.pickup_building)}, kind="nudge")
        if count == voice.MAX_NUDGES and leg.giver_id != item.owner_id:
            await self.say(item.owner_id, "owner_late", item, {"owner": self.user(item.owner_id).name}, kind="late")
        return leg.giver_id

    async def running_late(self, user_id: str, minutes: int) -> set[str]:
        """They can't make their next handoff on time: nudge them, then plan around the delay."""
        until = self.now() + timedelta(minutes=minutes)
        self.store.set_setting(f"delay:{user_id}", until.isoformat())
        legs = sorted((l for l in self.store.all(Leg) if l.giver_id == user_id and l.status in LIVE_LEGS),
                      key=lambda l: l.pickup_time)
        affected = set()
        if legs:
            await self.nudge(legs[0].id)
            affected |= await self.replan(legs[0].item_id)
        return affected

    async def exam_moved(self, event_uid: str, shift: timedelta) -> set[str]:
        """A calendar change: the exam and any request for it move, and each item re-plans."""
        event = self.store.get(CalEvent, event_uid)
        event.start, event.end = event.start + shift, event.end + shift
        self.store.put(event)
        items = set()
        for req in self.store.all(Request, event_uid=event_uid):
            if req.status in ACTIVE + ("pending",):
                req.window_start, req.window_end = event.start, event.end
                self.store.put(req)
                if req.item_id:
                    items.add(req.item_id)
        for leg in self.store.all(Leg):
            if leg.status in LIVE_LEGS and event.user_id in (leg.holder_id, leg.giver_id):
                items.add(leg.item_id)
        affected = set()
        for item_id in sorted(items):
            affected |= await self.replan(item_id)
        return affected

    # ---- recaps

    def exams_served(self, item_id: str, since: datetime) -> int:
        item = self.store.get(Item, item_id)
        return sum(1 for l in self.store.all(Leg, item_id=item_id)
                   if l.status in ("active", "done") and l.holder_id != item.owner_id and l.request_id
                   and l.pickup_time >= since)

    async def weekly_recap(self, owner_id: str) -> list[Message]:
        sent = []
        for item in self.store.all(Item, owner_id=owner_id):
            n = self.exams_served(item.id, self.now() - timedelta(days=7))
            if n:
                sent.append(await self.say(owner_id, "recap", item, {"owner": self.user(owner_id).name, "n": n}))
        return sent
