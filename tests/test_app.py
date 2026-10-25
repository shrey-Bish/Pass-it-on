import re

import pytest
from fastapi.testclient import TestClient

from passiton import app as app_module
from passiton.models import Handoff, Item, Leg, User

from .test_agents import live_legs


@pytest.fixture(scope="module")
def client():
    with TestClient(app_module.app) as c:
        c.post("/demo/reset")
        yield c


def words(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_each_demo_phone_shows_only_its_own_part(client):
    jay = words(client.get("/?s=demo-jay").text)
    assert "Get Maya&#39;s calculator 11:45 AM" in jay and "Hand it to Priya" in jay
    priya = words(client.get("/?s=demo-priya").text)
    assert "Maya" in priya and "Coor Hall" not in priya  # Priya never sees Jay's pickup


def test_a_demo_handoff_by_code(client):
    hub = app_module.sandbox
    leg = live_legs(hub)[0]
    assert client.post(f"/legs/{leg.id}/confirm?s=demo-jay").status_code == 200
    assert client.get(f"/legs/{leg.id}?s=demo-maya").status_code == 200
    code = hub.store.all(Handoff, leg_id=leg.id)[0].code
    page = client.post(f"/legs/{leg.id}/code?s=demo-jay", data={"code": code}).text
    assert "It&#39;s with you" in page
    assert hub.store.get(Item, leg.item_id).holder_id == "jay"


def test_demo_buttons(client):
    for action in ("exam", "late", "moveout"):
        assert client.post(f"/demo/{action}").status_code == 200
    assert "Want to keep this" in client.get("/?s=demo-priya").text


def test_pages_need_a_session(client):
    assert client.get("/texts", follow_redirects=False).status_code == 303
    assert client.get("/photos/ti84maya.jpg").status_code == 404
    assert client.get("/photos/ti84maya.jpg?s=demo-maya").status_code == 200


def test_sign_in_by_link_then_forget(client):
    page = client.post("/login", data={"phone": "(480) 555-0101"}).text
    link = re.search(r'href="(http[^"]+/m/[^"]+)"', page).group(1)
    token = link.rsplit("/m/", 1)[1]
    assert client.get(f"/m/{token}", follow_redirects=False).status_code == 303
    assert client.get(f"/m/{token}", follow_redirects=False).headers["location"] == "/?expired=1"  # one use
    client.post("/name", data={"name": "shrey bishnoi"})
    me = app_module.real.store.all(User, phone="+14805550101")[0]
    assert me.name == "Shrey"  # first name only
    client.post("/forget")
    assert app_module.real.store.all(User, phone="+14805550101") == []


def test_bad_phone_numbers_are_explained(client):
    assert "doesn" in client.post("/login", data={"phone": "12"}).text
