import re
import time

import pytest
from fastapi.testclient import TestClient

from passiton import app as app_module
from passiton import vision
from passiton.models import Handoff, Item, User

from .conftest import SAMPLES
from .test_agents import live_legs


@pytest.fixture(scope="module")
def client():
    with TestClient(app_module.app) as c:
        c.post("/demo/reset")
        yield c


def words(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).replace("&#39;", "'").split())


def test_each_demo_phone_shows_only_its_own_part(client):
    jay = words(client.get("/?s=demo-jay").text)
    assert "Get Maya's calculator 11:45 AM Coor Hall" in jay and "Hand it to Priya" in jay
    priya = words(client.get("/?s=demo-priya").text)
    assert "Maya" in priya and "Coor Hall" not in priya  # Priya never sees Jay's pickup


def test_every_screen_renders(client):
    for path in ("/texts", "/calendar", "/borrow", "/list", "/items/ti84maya", "/items/ti84maya/edit"):
        page = client.get(f"{path}?s=demo-maya")
        assert page.status_code == 200, path
        assert 'class="screen"' in page.text and "pass-it-on.css" in page.text
    assert "Give it a personality" in client.get("/items/ti84maya/edit?s=demo-maya").text
    assert 'class="timeline"' in client.get("/items/ti84maya?s=demo-maya").text


def test_a_demo_handoff_by_code(client):
    hub = app_module.sandbox
    leg = live_legs(hub)[0]
    home = client.post(f"/legs/{leg.id}/confirm?s=demo-jay").text
    assert "Confirmed." in words(home)  # the one-line result after the action
    assert 'class="qr"' in client.get(f"/legs/{leg.id}?s=demo-maya").text
    code = hub.store.all(Handoff, leg_id=leg.id)[0].code
    page = words(client.post(f"/legs/{leg.id}/code?s=demo-jay", data={"code": code}).text)
    assert "It's with you" in page
    assert hub.store.get(Item, leg.item_id).holder_id == "jay"
    assert "Handed over" in words(client.get(f"/legs/{leg.id}?s=demo-maya").text)


def test_a_wrong_code_is_explained(client):
    hub = app_module.sandbox
    leg = next(l for l in live_legs(hub) if l.status in ("planned", "confirmed"))
    client.get(f"/legs/{leg.id}?s=demo-{leg.giver_id}")
    page = words(client.post(f"/legs/{leg.id}/code?s=demo-{leg.holder_id}", data={"code": "abcd"}).text)
    assert "doesn't match" in page


def test_demo_buttons(client):
    for action in ("exam", "late", "moveout"):
        state = client.post(f"/demo/{action}").json()
        assert state["clock"] and state["happening"]
    assert "Want to keep this" in client.get("/?s=demo-priya").text
    assert "Live demo" in client.get("/demo").text


def test_pages_need_a_session(client):
    assert client.get("/texts", follow_redirects=False).status_code == 303
    assert client.get("/photos/ti84maya.jpg").status_code == 404
    assert client.get("/photos/ti84maya.jpg?s=demo-maya").status_code == 200


def sign_in(client, phone="(480) 555-0101", name="shrey bishnoi"):
    page = client.post("/login", data={"phone": phone}).text
    token = re.search(r'href="http[^"]+/m/([^"?]+)', page).group(1)
    assert client.get(f"/m/{token}", follow_redirects=False).status_code == 303
    assert client.get(f"/m/{token}", follow_redirects=False).headers["location"] == "/?expired=1"  # one use
    client.post("/name", data={"name": name})
    return token


def test_sign_in_by_link_then_forget(client):
    sign_in(client)
    me = app_module.real.store.all(User, phone="+14805550101")[0]
    assert me.name == "Shrey"  # first name only
    client.post("/forget")
    assert app_module.real.store.all(User, phone="+14805550101") == []


def test_lend_a_photo_end_to_end(client, monkeypatch):
    async def identify(data, media_type, client=None):
        return vision.Identified(found=True, item_type="TI-84 Plus CE", category="calculator", condition="good",
                                 fingerprint="yellow smiley sticker bottom-right; scratch top-left", value_tier="mid")

    monkeypatch.setattr(vision, "identify", identify)
    sign_in(client, phone="(480) 555-0102", name="maya")
    sent = client.post("/list", files={"camera": ("ti84.jpg", (SAMPLES / "ti84.jpg").read_bytes(), "image/jpeg")},
                       follow_redirects=False)
    looking = sent.headers["location"]
    assert looking.endswith("/looking")
    for _ in range(50):
        page = client.get(looking, follow_redirects=False)
        if page.status_code == 303:
            break
        assert "Looking at your photo" in page.text
        time.sleep(0.05)
    edit = client.get(page.headers["location"]).text
    assert "Yellow smiley sticker bottom-right" in edit and "Is this right?" in edit
    item_id = looking.split("/")[2]  # /list/<item_id>/looking
    home = words(client.post(f"/items/{item_id}/edit", data={"type": "TI-84 Plus CE", "persona": "chill",
                                                              "condition": "good", "return_by": "17:00", "home": "MU",
                                                              "audience": "asu", "min_rating": "ok"}).text)
    assert "Listed. Your TI-84 Plus CE is ready to lend." in home and "Lending" in home
    client.post("/forget")


def test_bad_phone_numbers_are_explained(client):
    assert "doesn" in client.post("/login", data={"phone": "12"}).text
