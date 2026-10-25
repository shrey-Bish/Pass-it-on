"""Plans an item's route through one day: who holds it when, and where each handoff happens. Plain code, no model.

Rules it never breaks:
- The owner has the item during their own exams.
- Nobody is asked to be somewhere during their own classes or exams, and walking time between buildings counts.
- A borrower has the item at least BUFFER before their window starts.
- The item is home by the owner's return_by time. A borrower whose window would break that isn't added.
- One item can't be in two places: when windows overlap, the earlier request keeps it and the later one waits.
- Handoffs happen only at public campus buildings (assets/buildings.json), between DAY_START and DAY_END.

Among the handoffs that fit, it picks the one with the least extra walking for both people. On a tie, the owner
keeps their item as long as they can (latest time), while a borrower passes it on as soon as they're done (earliest
time), which leaves slack before the next exam if someone runs late.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from . import campus

BUFFER = timedelta(minutes=10)
HANDOFF = timedelta(minutes=5)
STEP = timedelta(minutes=5)
DAY_START, DAY_END = time(8, 0), time(21, 0)

# Why a request didn't fit. Only the requester's own agent ever sees this, as a plain sentence.
OWNER_EXAM, TAKEN, NO_HANDOFF, RETURN_BY, NOT_TODAY = "owner_exam", "taken", "no_handoff", "return_by", "not_today"


@dataclass(frozen=True)
class Busy:
    start: datetime
    end: datetime
    building: str | None = None


@dataclass(frozen=True)
class Need:
    """Someone needs the item from start to end at building. request_id is None for the owner's own exams."""

    holder_id: str
    start: datetime
    end: datetime
    building: str
    request_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class Meet:
    place: str
    time: datetime
    walking: int


@dataclass
class Stop:
    """holder_id gets the item from giver_id at pickup_building, pickup_time, and keeps it until end."""

    holder_id: str
    giver_id: str
    pickup_building: str
    pickup_time: datetime
    start: datetime
    end: datetime
    request_id: str | None = None


@dataclass
class World:
    """Everything the planner reads for one item on one day."""

    owner_id: str
    home: str                                  # the owner's home building for this item
    return_by: datetime
    busy: dict[str, list[Busy]] = field(default_factory=dict)
    holder_id: str | None = None               # who has it now (default: the owner)
    place: str | None = None                   # where they are with it (default: home, for the owner)
    since: datetime | None = None              # from when (default: the start of the day)
    walk: Callable[[str, str], int] = campus.walk_minutes

    def __post_init__(self):
        self.holder_id = self.holder_id or self.owner_id
        if self.place is None and self.holder_id == self.owner_id:
            self.place = self.home
        day = self.return_by.date()
        self.day_start = datetime.combine(day, DAY_START, self.return_by.tzinfo)
        self.day_end = datetime.combine(day, DAY_END, self.return_by.tzinfo)
        self.since = self.since or self.day_start
        self.busy = {person: sorted(events, key=lambda b: b.start) for person, events in self.busy.items()}


@dataclass
class Plan:
    stops: list[Stop]
    waitlisted: dict[str, str]                 # request_id -> one of the reasons above


def plan(world: World, owner_exams: list[Need], requests: list[Need]) -> Plan:
    """Adds requests first come, first served, keeping each only if the whole day still works with it."""
    accepted = [n for n in owner_exams if n.end > world.since]
    waitlisted = {}
    for need in sorted(requests, key=lambda n: (n.created_at or n.start, n.start)):
        if need.start.date() != world.return_by.date() or need.end > world.return_by or need.start < world.since:
            waitlisted[need.request_id] = RETURN_BY if need.end > world.return_by else NOT_TODAY
            continue
        stops, reason = route(world, accepted + [need])
        if stops is None:
            waitlisted[need.request_id] = reason
        else:
            accepted.append(need)
    stops, reason = route(world, accepted)
    assert stops is not None, reason  # every accepted need already fit
    return Plan(stops, waitlisted)


def route(world: World, needs: list[Need]) -> tuple[list[Stop] | None, str | None]:
    """The handoff chain for these needs in time order, ending back with the owner. (None, reason) if it breaks."""
    needs = sorted(needs, key=lambda n: n.start)
    for i, a in enumerate(needs):
        for b in needs[i + 1:]:
            if b.start < a.end and a.holder_id != b.holder_id:
                owners_exam = None in (a.request_id, b.request_id) and world.owner_id in (a.holder_id, b.holder_id)
                return None, OWNER_EXAM if owners_exam else TAKEN
    holder, place, free = world.holder_id, world.place, world.since
    stops: list[Stop] = []
    for need in needs:
        if need.holder_id == holder:
            # They already have it, like the owner getting it back before their own exam.
            if stops:
                stops[-1].end = max(stops[-1].end, need.end)
            place, free = need.building, max(free, need.end)
            continue
        meet = best_meet(world, holder, place, free, need.holder_id, need.building, need.start, need.start - BUFFER)
        if meet is None:
            return None, NO_HANDOFF
        if stops:
            stops[-1].end = meet.time
        stops.append(Stop(need.holder_id, holder, meet.place, meet.time, need.start, need.end, need.request_id))
        holder, place, free = need.holder_id, need.building, need.end
    if holder != world.owner_id:
        meet = best_meet(world, holder, place, free, world.owner_id, world.home, None, world.return_by)
        if meet is None:
            return None, RETURN_BY
        if stops:
            stops[-1].end = meet.time
        stops.append(Stop(world.owner_id, holder, meet.place, meet.time, meet.time, world.return_by))
    return stops, None


def best_meet(world: World, giver: str, giver_place: str | None, giver_free: datetime, receiver: str,
              receiver_next: str | None, receiver_needs_at: datetime | None, deadline: datetime) -> Meet | None:
    """The handoff that fits both people with the least extra walking, latest on a tie. None if nothing fits."""
    deadline = min(deadline, world.day_end)
    t = _ceil(max(giver_free, world.day_start, world.since))
    owner_gives = giver == world.owner_id
    best = None
    while t + HANDOFF <= deadline:
        for place in campus.buildings():
            g = _detour(world, giver, place, t, came_from=(giver_place, giver_free))
            if g is None:
                continue
            r = _detour(world, receiver, place, t,
                        going_to=(receiver_next, receiver_needs_at) if receiver_needs_at else None,
                        home=receiver_next if receiver_needs_at is None else None)
            if r is None:
                continue
            if best is None or g + r < best.walking or (g + r == best.walking and owner_gives and t >= best.time):
                best = Meet(place, t, g + r)
        t += STEP
    return best


def _detour(world: World, person: str, place: str, t: datetime, came_from: tuple[str | None, datetime] | None = None,
            going_to: tuple[str | None, datetime] | None = None, home: str | None = None) -> int | None:
    """Extra walking minutes for person to be at place from t to t + HANDOFF, or None if their day can't fit it."""
    events = world.busy.get(person, [])
    end = t + HANDOFF
    before = [e for e in events if e.end <= t]
    prev_place, prev_end = (before[-1].building, before[-1].end) if before else (None, None)
    if person == world.owner_id and prev_place is None:
        prev_place = world.home
    if came_from and (prev_end is None or came_from[1] >= prev_end):
        prev_place, prev_end = came_from
    walk_in = world.walk(prev_place, place) if prev_place else 0
    leave = t - timedelta(minutes=walk_in)
    if prev_end is not None and leave < prev_end:
        return None
    after = [e for e in events if e.start >= end]
    next_place, next_start = (after[0].building, after[0].start) if after else (None, None)
    if going_to and (next_start is None or going_to[1] <= next_start):
        next_place, next_start = going_to
    if next_place is None and home:
        next_place = home  # the owner heads home with it; no deadline, but the walk counts
    walk_out = world.walk(place, next_place) if next_place else 0
    if any(e.start < end and e.end > leave for e in events):
        return None  # in class, in an exam, or walking through one
    if next_start is not None and end + timedelta(minutes=walk_out) > next_start:
        return None
    direct = world.walk(prev_place, next_place) if prev_place and next_place else 0
    return walk_in + walk_out - direct


def _ceil(t: datetime) -> datetime:
    minutes = (t.minute // 5 + (1 if t.minute % 5 or t.second or t.microsecond else 0)) * 5
    return t.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)


def changed(old: list[Stop], new: list[Stop]) -> set[str]:
    """Who has to confirm again after a re-plan: anyone whose pickup or dropoff moved, appeared or disappeared."""
    def parts(stops: list[Stop]) -> dict[str, set]:
        out: dict[str, set] = {}
        for s in stops:
            out.setdefault(s.holder_id, set()).add(("get", s.giver_id, s.pickup_building, s.pickup_time))
            out.setdefault(s.giver_id, set()).add(("give", s.holder_id, s.pickup_building, s.pickup_time))
        return out
    a, b = parts(old), parts(new)
    return {person for person in a.keys() | b.keys() if a.get(person) != b.get(person)}
