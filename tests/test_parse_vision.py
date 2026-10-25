import pytest

from passiton import parse, vision

from .conftest import at


def parsed(**kw):
    return parse.Parsed(**({"found": True, "item_type": "TI-84", "date": "2026-10-29", "start": "14:00"} | kw))


def test_a_borrow_text_becomes_a_need_with_a_building():
    need = parse.to_need(parsed(place="Durham Hall"))
    assert (need.item_type, need.window_start, need.window_end, need.building) == ("TI-84", at(14), at(15, 15), "LL")


def test_an_end_time_is_kept():
    assert parse.to_need(parsed(end="15:30", place="PSH 151")).window_end == at(15, 30)


@pytest.mark.parametrize("kw, words", [
    ({"found": False}, "what you need"),
    ({"start": None, "place": "LL"}, "When do you need it"),
    ({"place": "my dorm"}, "Which building"),
    ({"item_type": "exam answers", "place": "LL"}, "physical things"),
])
def test_unclear_texts_get_a_plain_question_back(kw, words):
    with pytest.raises(parse.NotUnderstood) as e:
        parse.to_need(parsed(**kw))
    assert words in str(e.value)


@pytest.mark.parametrize("item_type, category, allowed", [
    ("TI-84 Plus CE", "calculator", True), ("Safety goggles", "lab_gear", True), ("Navy blazer", "clothing", True),
    ("Lecture notes", "books", False), ("Dorm keys", "other", False), ("Student ID card", "other", False),
    ("Ibuprofen pills", "other", False), ("Keyboard", "electronics", True),
])
def test_what_can_be_listed_is_also_checked_in_code(item_type, category, allowed):
    found = vision.Identified(found=True, item_type=item_type, category=category, allowed=True).check_rules()
    assert found.allowed is allowed
