import pytest

from passiton import calendar_feed, campus

from .conftest import at, events


def test_canvas_feed_finds_exams_classes_and_skips_due_dates():
    maya = events("maya")
    exams = calendar_feed.exams(maya)
    assert [(e.course, e.start, e.end, e.building) for e in exams] == [("MAT 265", at(9), at(10, 15), "WXLR")]
    assert [e.course for e in maya if e.kind == "class"] == ["CSE 240", "CSE 240"]
    assert [e.title for e in maya if e.kind == "assignment"] == ["Homework 7 [MAT 265 Calculus for Engineers III (Fall 2026)]"]
    assert all(e.kind != "assignment" for e in calendar_feed.busy(maya))


def test_times_are_arizona_time_and_rooms_become_buildings():
    jay = {e.course: e for e in calendar_feed.exams(events("jay")) if e.start.month == 10}
    assert jay["STP 226"].start == at(12) and jay["STP 226"].building == "LL"


@pytest.mark.parametrize("text, code", [("PSH 151", "PSH"), ("Durham Hall 120", "LL"), ("Hayden Library", "HAY"),
                                        ("WXLR A206", "WXLR"), ("Online", None), (None, None)])
def test_find_building(text, code):
    assert campus.find_building(text) == code


def test_walking_time_is_symmetric_and_zero_in_place():
    assert campus.walk_minutes("LL", "PSH") == campus.walk_minutes("PSH", "LL") > 0
    assert campus.walk_minutes("MU", "MU") == 0


def test_not_a_calendar_is_a_plain_error():
    with pytest.raises(calendar_feed.FeedError):
        calendar_feed.parse(b"hello", "me")
