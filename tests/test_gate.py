import logging
from datetime import timedelta

import pytest

from passiton import demo
from passiton.gate import ALLOWED, Gate, GateBlocked

from .conftest import at, run


def gate():
    return Gate(known_types={"TI-84 Plus CE"})


NEED = {"kind": "need", "item_type": "TI-84 Plus CE", "window_start": at(12), "window_end": at(13, 15),
        "building_code": "LL", "rating_bucket": "good"}


def test_an_allowed_message_crosses_unchanged():
    assert gate().cross(dict(NEED), sender="borrower:jay", receiver="item:x") == NEED


@pytest.mark.parametrize("extra", [
    {"reason": "my stats exam"},              # why they need it
    {"note": "Jay sits by the window"},        # free text
    {"schedule": [{"start": "09:00"}]},        # another student's schedule
    {"grade": "B+"},
    {"borrower_name": "Jay Okafor"},
    {"phone": "+14805550101"},
])
def test_anything_off_the_allowlist_is_blocked(extra):
    with pytest.raises(GateBlocked):
        gate().cross(NEED | extra, sender="borrower:jay", receiver="item:x")


@pytest.mark.parametrize("field, value", [
    ("item_type", "TI-84; Jay's exam is at noon in LL"),  # a sentence hiding in an allowed field
    ("building_code", "Jay's dorm, room 214"),            # not a public building
    ("rating_bucket", "1080"),                            # the raw rating never crosses
    ("window_start", "tomorrow"),
    ("window_start", at(12).replace(tzinfo=None)),        # no time zone
])
def test_allowed_fields_cant_smuggle_anything(field, value):
    with pytest.raises(GateBlocked):
        gate().cross(NEED | {field: value}, sender="borrower:jay", receiver="item:x")


def test_an_agent_cant_learn_a_whole_schedule():
    g = gate()
    whole_day = NEED | {"window_start": at(0), "window_end": at(0) + timedelta(hours=23, minutes=59)}
    with pytest.raises(GateBlocked):
        g.cross(whole_day, sender="item:x", receiver="borrower:jay")      # one window, at most 12 hours
    with pytest.raises(GateBlocked):
        g.cross(NEED | {"window_start": [at(9), at(12)], "window_end": [at(10), at(13)]},
                sender="item:x", receiver="borrower:jay")                 # never a list of windows
    with pytest.raises(GateBlocked):
        g.cross({"kind": "need", "full_schedule": True}, sender="borrower:dev", receiver="item:x")


def test_confirm_must_be_a_real_yes_or_no():
    with pytest.raises(GateBlocked):
        gate().cross({"kind": "confirm", "item_id": "ti84maya", "confirm": "yes please"}, sender="a", receiver="b")


def test_every_block_is_logged_with_field_names_but_never_values(caplog):
    g = gate()
    with caplog.at_level(logging.WARNING), pytest.raises(GateBlocked):
        g.cross(NEED | {"reason": "SECRET-REASON"}, sender="borrower:jay", receiver="item:x")
    assert g.blocked == [{"sender": "borrower:jay", "receiver": "item:x", "why": "fields not allowed: reason",
                          "fields": sorted([*NEED, "reason"])}]
    assert "SECRET-REASON" not in caplog.text and "reason" in caplog.text


def test_in_the_whole_demo_only_allowlisted_fields_ever_cross(world):
    crossed = []
    original = world.gate.cross

    def spy(message, *, sender, receiver):
        clean = original(message, sender=sender, receiver=receiver)
        crossed.append(clean)
        return clean

    world.gate.cross = spy
    run(demo.exam_plus_hour(world))
    run(demo.jay_late(world))
    assert crossed
    for message in crossed:
        assert set(message) <= set(ALLOWED) | {"kind"}
    assert world.gate.blocked == []
