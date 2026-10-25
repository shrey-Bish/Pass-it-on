from passiton import demo, moveout
from passiton.models import CalEvent, Item, User

from .conftest import run
from .test_agents import texts


def test_soonest_need_wins_and_nobody_learns_why(world):
    run(demo.finish_until(world, demo.START.replace(hour=17, minute=30)))
    ranked = moveout.candidates(world, world.store.get(Item, demo.ITEM_ID))
    # Finals: Priya's CHM 113 is Dec 10, Dev's MAT 210 is Dec 11, Jay's STP 226 is Dec 14.
    assert [user for user, _ in ranked] == ["priya", "dev", "jay"]
    run(moveout.start(world, "maya"))
    offer = texts(world, "priya")[-1]
    assert "CHM 113" in offer and "Maya" in offer
    assert "Dev" not in offer and "Jay" not in offer
    assert not any("CHM 113" in t for t in texts(world, "maya"))  # the owner never sees anyone's reason


def test_yes_makes_a_new_owner(world):
    run(demo.maya_moves_out(world))
    assert run(moveout.answer(world, demo.ITEM_ID, "priya", True))
    item = world.store.get(Item, demo.ITEM_ID)
    assert item.owner_id == "priya" and item.mode == "lend"
    assert texts(world, "maya")[-1].endswith("Maya graduated. I live with Priya now 💛")


def test_no_asks_the_next_one(world):
    run(demo.maya_moves_out(world))
    run(moveout.answer(world, demo.ITEM_ID, "priya", False))
    assert world.store.setting(f"moveout_offer:{demo.ITEM_ID}") == "dev"
    assert not run(moveout.answer(world, demo.ITEM_ID, "priya", True))  # Priya's offer is gone


def test_unclaimed_items_get_donation_info(world):
    for event in world.store.all(CalEvent):
        world.store.delete(CalEvent, event.uid)
    run(demo.maya_moves_out(world))
    assert "Ditch the Dumpster" in texts(world, "maya")[-1]


def test_only_the_person_offered_can_answer(world):
    run(demo.maya_moves_out(world))
    world.store.put(User(id="eve", name="Eve"))
    assert not run(moveout.answer(world, demo.ITEM_ID, "eve", True))
    assert world.store.get(Item, demo.ITEM_ID).owner_id == "maya"
