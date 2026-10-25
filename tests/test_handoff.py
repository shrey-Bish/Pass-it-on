from datetime import timedelta

from passiton import handoff, vision
from passiton.models import Handoff, Item, Leg

from .conftest import at, run
from .test_agents import live_legs


def first_leg(world) -> Leg:
    return live_legs(world)[0]  # Maya hands it to Jay


def test_the_qr_moves_custody_once(world):
    leg = first_leg(world)
    h = handoff.open_handoff(world, leg.id, "maya")
    assert h is not None and handoff.open_handoff(world, leg.id, "maya").token == h.token  # refresh keeps the code
    done, error = run(handoff.receive(world, "jay", token=h.token))
    assert error is None and done.status == "active"
    assert world.store.get(Item, leg.item_id).holder_id == "jay"
    _, again = run(handoff.receive(world, "jay", token=h.token))
    assert again == "That code was already used."


def test_only_the_giver_can_open_and_only_the_receiver_can_scan(world):
    leg = first_leg(world)
    assert handoff.open_handoff(world, leg.id, "jay") is None
    h = handoff.open_handoff(world, leg.id, "maya")
    _, error = run(handoff.receive(world, "priya", token=h.token))
    assert error == "This handoff is for someone else."
    assert world.store.get(Item, leg.item_id).holder_id is None


def test_codes_expire(world):
    leg = first_leg(world)
    h = handoff.open_handoff(world, leg.id, "maya")
    world.store.set_setting("clock", (world.now() + timedelta(minutes=31)).isoformat())
    _, error = run(handoff.receive(world, "jay", token=h.token))
    assert "expired" in error


def test_the_four_digit_code_allows_only_a_few_tries(world):
    leg = first_leg(world)
    h = handoff.open_handoff(world, leg.id, "maya")
    wrong = "0000" if h.code != "0000" else "1111"
    for _ in range(handoff.MAX_CODE_TRIES):
        _, error = run(handoff.receive(world, "jay", leg_id=leg.id, code=wrong))
    _, error = run(handoff.receive(world, "jay", leg_id=leg.id, code=h.code))
    assert error.startswith("Too many tries")


def test_the_right_code_works(world):
    leg = first_leg(world)
    h = handoff.open_handoff(world, leg.id, "maya")
    done, error = run(handoff.receive(world, "jay", leg_id=leg.id, code=h.code))
    assert error is None and done.holder_id == "jay"
    assert world.store.get(Handoff, h.id).used


def test_a_mismatched_photo_is_flagged_to_the_owner(world, monkeypatch):
    leg = first_leg(world)
    h = handoff.open_handoff(world, leg.id, "maya")
    run(handoff.receive(world, "jay", token=h.token))

    async def different(*args, **kwargs):
        return vision.PhotoCheck(match=False, why="It's white, and the sticker is missing.")

    monkeypatch.setattr(vision, "compare", different)
    check = run(handoff.photo_check(world, leg.id, "jay", b"listing", "image/jpeg", b"photo", "image/jpeg"))
    assert not check.match
    assert world.store.get(Handoff, h.id).photo_match == "mismatch"
    alert = [m for m in world.inbox("maya") if m.kind == "alert"]
    assert len(alert) == 1 and "Jay" in alert[0].text


def test_qr_png_is_an_image():
    assert handoff.qr_png("https://example.org/h/abc").startswith(b"\x89PNG")
