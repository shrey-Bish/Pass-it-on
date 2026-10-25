from datetime import timedelta

import pytest

from passiton import calendar_feed, campus, scheduler as s

from .conftest import at, events


def busy_of(*people, moved=None):
    out = {}
    for person in people:
        out[person] = [s.Busy(e.start, e.end, e.building) for e in calendar_feed.busy(events(person))
                       if e.start.date() == at(8).date()]
    if moved:
        person, old_start, new = moved
        out[person] = [new if b.start == old_start else b for b in out[person]]
    return out


def day_world(busy=None, return_by=at(17)):
    return s.World(owner_id="maya", home="MU", return_by=return_by, busy=busy or busy_of("maya", "jay", "priya", "dev"))


MAYA_EXAM = [s.Need("maya", at(9), at(10, 15), "WXLR")]
JAY = s.Need("jay", at(12), at(13, 15), "LL", "r-jay", at(7))
PRIYA = s.Need("priya", at(15), at(16, 15), "PSH", "r-priya", at(7, 5))
DEV = s.Need("dev", at(12, 30), at(13, 20), "WXLR", "r-dev", at(7, 10))


def check_route(world, stops, needs):
    """Every rule the planner promises, checked against its own output."""
    by_request = {n.request_id: n for n in needs}
    for stop in stops:
        assert campus.is_building(stop.pickup_building)
        assert world.day_start <= stop.pickup_time and stop.pickup_time + s.HANDOFF <= world.day_end
        for person in (stop.holder_id, stop.giver_id):
            for b in world.busy.get(person, []):
                assert not (b.start < stop.pickup_time + s.HANDOFF and b.end > stop.pickup_time), (person, stop)
        if stop.request_id:
            need = by_request[stop.request_id]
            assert stop.pickup_time + s.HANDOFF <= need.start - s.BUFFER
            assert stop.pickup_time + s.HANDOFF + timedelta(minutes=campus.walk_minutes(stop.pickup_building, need.building)) <= need.start
    assert stops[-1].holder_id == world.owner_id and stops[-1].pickup_time + s.HANDOFF <= world.return_by


def test_the_demo_day_matches_the_story():
    world = day_world()
    plan = s.plan(world, MAYA_EXAM, [JAY, PRIYA, DEV])
    route = [(x.giver_id, x.holder_id, x.pickup_building, x.pickup_time) for x in plan.stops]
    assert route[0] == ("maya", "jay", "COOR", at(11, 45))  # Maya keeps it until her class ends at 11:45
    assert [(g, h) for g, h, _, _ in route] == [("maya", "jay"), ("jay", "priya"), ("priya", "maya")]
    assert plan.waitlisted == {"r-dev": s.TAKEN}
    check_route(world, plan.stops, [JAY, PRIYA])


def test_owner_exams_are_never_broken():
    early = s.Need("jay", at(9, 30), at(10), "WXLR", "r-early", at(7))
    plan = s.plan(day_world(), MAYA_EXAM, [early])
    assert plan.waitlisted == {"r-early": s.OWNER_EXAM}
    assert plan.stops == []


def test_first_come_wins_an_overlap_even_if_the_later_ask_starts_earlier():
    first = s.Need("dev", at(12, 30), at(13, 20), "WXLR", "r-dev", at(6))
    plan = s.plan(day_world(), MAYA_EXAM, [JAY, first])
    assert plan.waitlisted == {"r-jay": s.TAKEN}


def test_a_borrower_who_cant_get_home_by_return_time_isnt_added():
    # Priya's exam ends at 4:15 and the item is due home at 4:15: there's no time left to hand it back.
    plan = s.plan(day_world(return_by=at(16, 15)), MAYA_EXAM, [JAY, PRIYA])
    assert plan.waitlisted == {"r-priya": s.RETURN_BY}


def test_walking_time_counts():
    # Jay is in class across campus until 11:58, and the item is Maya's until her exam ends at 10:15.
    busy = busy_of("maya", "jay")
    busy["jay"] = [s.Busy(at(9), at(11, 58), "BYENG"), s.Busy(at(12), at(13, 15), "LL")]
    plan = s.plan(day_world(busy), MAYA_EXAM, [JAY])
    assert plan.waitlisted == {"r-jay": s.NO_HANDOFF}


def test_a_handoff_moves_to_fit_both_free_gaps():
    world = day_world()
    plan = s.plan(world, MAYA_EXAM, [JAY])
    pickup = plan.stops[0].pickup_time
    assert at(11, 45) <= pickup <= at(11, 45)  # after Maya's class, before Jay's exam, with walking and buffer
    check_route(world, plan.stops, [JAY])


def test_exam_moving_an_hour_replans_and_dev_is_never_affected():
    before = s.plan(day_world(), MAYA_EXAM, [JAY, PRIYA, DEV])
    moved_jay = s.Need("jay", at(13), at(14, 15), "LL", "r-jay", at(7))
    busy = busy_of("maya", "jay", "priya", "dev", moved=("jay", at(12), s.Busy(at(13), at(14, 15), "LL")))
    world = day_world(busy)
    after = s.plan(world, MAYA_EXAM, [moved_jay, PRIYA, DEV])
    check_route(world, after.stops, [moved_jay, PRIYA])
    affected = s.changed(before.stops, after.stops)
    assert "jay" in affected and "maya" in affected and "dev" not in affected


def test_handoffs_only_in_daytime():
    late = s.Need("jay", at(20, 40), at(20, 59), "LL", "r-late", at(7))
    plan = s.plan(day_world(return_by=at(21)), MAYA_EXAM, [late])
    assert "r-late" in plan.waitlisted


@pytest.mark.parametrize("minute, expected", [(0, 0), (1, 5), (44, 45), (56, 60)])
def test_handoff_times_are_on_a_five_minute_grid(minute, expected):
    assert s._ceil(at(10, minute)) == at(10) + timedelta(minutes=expected)
