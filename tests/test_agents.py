from passiton import demo, rating, voice
from passiton.agents import Hub, type_matches
from passiton.models import Item, Leg, Message, Request, Rules, User

from .conftest import at, run


def texts(hub: Hub, user_id: str) -> list[str]:
    return [m.text for m in hub.inbox(user_id)]


def live_legs(hub: Hub) -> list[Leg]:
    return sorted((l for l in hub.store.all(Leg) if l.status in ("planned", "confirmed", "active")),
                  key=lambda l: l.pickup_time)


def test_the_seeded_story(world):
    route = [(l.giver_id, l.holder_id) for l in live_legs(world)]
    assert route == [("maya", "jay"), ("jay", "priya"), ("priya", "maya")]
    assert [r.borrower_id for r in demo.waiting(world)] == ["dev"]


def test_each_person_hears_only_their_own_part(world):
    assert texts(world, "jay")[0].count("Coor Hall") == 1 and "Priya" not in texts(world, "jay")[0]
    assert "Jay" not in " ".join(texts(world, "dev"))  # Dev is waitlisted without learning who has it
    everyone = " ".join(m.text for m in world.store.all(Message))
    for private in ("Okafor", "Nair", "Raman", "Chen", "STP 226", "MAT 265", "Exam 2", "Quiz"):
        assert private not in everyone  # last names, other people's courses and exams never appear in any text


def test_an_exam_moving_texts_only_the_affected(world):
    before = {p: len(texts(world, p)) for p in demo.PEOPLE}
    affected = run(demo.exam_plus_hour(world))
    assert "dev" not in affected
    for person in demo.PEOPLE:
        assert (len(texts(world, person)) > before[person]) == (person in affected)
    jay_pickup = live_legs(world)[0]
    assert jay_pickup.holder_id == "jay" and jay_pickup.pickup_time == at(12, 45) and jay_pickup.confirmed_by == []


def test_confirming_takes_both_sides(world):
    leg = live_legs(world)[0]
    world.confirm(leg.id, "jay")
    assert world.store.get(Leg, leg.id).status == "planned"
    world.confirm(leg.id, "maya")
    assert world.store.get(Leg, leg.id).status == "confirmed"
    assert world.confirm(leg.id, "dev") is None  # not their handoff


def test_running_late_nudges_then_replans(world):
    affected = run(demo.jay_late(world))
    assert affected == {"jay", "priya"}
    assert any("Nudge 1 of 3" in t for t in texts(world, "jay"))


def test_at_most_three_nudges_then_the_owner_is_told(world):
    run(demo.finish_until(world, at(13, 15)))
    leg = next(l for l in live_legs(world) if l.giver_id == "jay")
    for _ in range(5):
        run(world.nudge(leg.id))
    assert sum(m.kind == "nudge" for m in world.inbox("jay")) == voice.MAX_NUDGES
    assert sum(m.kind == "late" for m in world.inbox("maya")) == 1  # the owner hears once, privately


def test_owner_approval_and_a_second_lender(world):
    # Dev's quiz overlaps Jay's exam, so Maya's calculator can't help. A second TI-84 appears: Dev's agent finds it.
    world.store.put(User(id="sam", name="Sam", demo=True))
    world.store.put(Item(id="ti84samxx", owner_id="sam", type="TI-84 Plus CE", category="calculator", value_tier="mid",
                         rules=Rules(home_building="WXLR", min_rating="ok", auto_approve=True), listed=True,
                         created_at=at(7)))
    dev = next(r for r in world.store.all(Request) if r.borrower_id == "dev")
    dev.status = "pending"
    run(world.find_item(dev))
    dev = world.store.get(Request, dev.id)
    # Dev is new, and a mid-value item needs "ok", so Sam has to say yes himself.
    assert dev.status == "pending" and dev.item_id == "ti84samxx"
    assert any("Dev" in t for t in texts(world, "sam"))
    run(world.approve(dev.id, "sam", True))
    assert world.store.get(Request, dev.id).status == "planned"


def test_a_no_from_the_owner_tries_the_next_lender_without_a_reason(world):
    world.store.put(User(id="sam", name="Sam", demo=True))
    world.store.put(Item(id="ti84samxx", owner_id="sam", type="TI-84 Plus CE", category="calculator",
                         rules=Rules(min_rating="ok"), listed=True, created_at=at(7)))
    dev = next(r for r in world.store.all(Request) if r.borrower_id == "dev")
    dev.status = "pending"
    run(world.find_item(dev))
    run(world.approve(dev.id, "sam", False))
    dev = world.store.get(Request, dev.id)
    assert dev.status == "waitlisted" and "ti84samxx" in dev.declined_by
    assert not any("Sam" in t for t in texts(world, "dev"))


def test_a_handoff_moves_custody_and_updates_the_rating(world):
    run(demo.finish_until(world, at(13, 30)))  # Maya -> Jay done, Jay -> Priya done
    item = world.store.get(Item, demo.ITEM_ID)
    assert item.holder_id == "priya"
    assert world.store.get(User, "jay").completed == demo.PEOPLE["jay"][2] + 1


def test_back_home_and_weekly_recap(world):
    run(demo.finish_until(world, at(17, 30)))
    assert world.store.get(Item, demo.ITEM_ID).holder_id is None
    assert any("2 exams" in t for t in texts(world, "maya"))
    recap = run(world.weekly_recap("maya"))
    assert "2 exams" in recap[0].text and "2 people didn't have to buy one" in recap[0].text


def test_proactive_offer_for_an_exam_without_a_calculator(world):
    for r in world.store.all(Request, borrower_id="priya"):
        world.store.delete(Request, r.id)
    offers = world.offers("priya")
    assert [e.course for e in offers] == ["CHM 113"]


def test_rating_buckets_gate_valuable_items():
    new, regular = User(id="a", name="A"), User(id="b", name="B", rating=1100, completed=8)
    calc = Item(id="x" * 8, owner_id="o", type="TI-84 Plus CE", value_tier="mid", rules=Rules(min_rating="ok"),
                created_at=at(7))
    assert not rating.may_borrow(new, calc) and rating.may_borrow(regular, calc)
    assert rating.may_borrow(new, calc, override=True)
    assert rating.record(regular, 3) > 0 > rating.record(regular, 30)


def test_type_matching():
    calc = Item(id="x" * 8, owner_id="o", type="TI-84 Plus CE", category="calculator", created_at=at(7))
    assert type_matches("TI-84", calc) and type_matches("graphing calculator", calc)
    assert not type_matches("TI-30", calc) and not type_matches("safety goggles", calc)
