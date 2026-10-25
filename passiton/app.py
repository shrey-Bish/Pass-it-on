"""FastAPI routes: sign-in by texted link, listing, calendar import, borrowing, confirmations, handoffs, the demo.

Run: .venv/bin/uvicorn passiton.app:app --host 0.0.0.0 --port 8000

The real app and the /demo world use separate databases. A demo phone is signed in by a fixed ?s= token so three
phones can sit side by side on one page; everyone else signs in with a link sent by text. The screens follow the
Claude Design canvas (templates/_ui.html, static/pass-it-on.css): every change is a POST, then one line saying what
happened, then Home.
"""

import asyncio
import hashlib
import io
import logging
import os
import re
import secrets
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import calendar_feed, campus, demo, handoff, llm, moveout, parse, rating, vision, voice
from .agents import LIVE_LEGS, Hub, new_id
from .messenger import Messenger, from_env, notify
from .models import TZ, CalEvent, Item, Leg, Request as BorrowRequest, Rules, User
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("passiton")

DATA = Path(os.environ.get("PASSITON_DATA", ROOT / "data"))
PHOTOS = DATA / "photos"
DEMO_PHOTOS = DATA / "demo-photos"
PHOTOS.mkdir(parents=True, exist_ok=True)
MAX_PHOTO = 8 * 1024 * 1024
MAGIC_VALID = timedelta(minutes=15)
LOOK_STEPS = ("Working out what it is", "Spotting stickers and scratches", "Guessing what it's worth")

app = FastAPI(title="Pass It On")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")

real = Hub(Store(DATA / "app.db"), from_env())
sandbox = Hub(Store(DATA / "demo.db"), Messenger())  # demo students have no phones: texts stay in the outbox


def base_url() -> str:
    return os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")


def clock(t: datetime) -> str:
    return f"{t:%-I:%M %p}"


def day(t: datetime) -> str:
    return f"{t:%a %b %-d}"


def window(start: datetime, end: datetime) -> str:
    return f"{start:%a} {start:%-I:%M}–{clock(end)}"


# ---- who's asking

class Ctx:
    def __init__(self, request: Request):
        self.request = request
        self.s = request.query_params.get("s") or ""
        self.hub, self.user, self.session = real, None, None
        if self.s and (uid := sandbox.store.setting(f"session:{self.s}")):
            self.hub, self.user, self.session = sandbox, sandbox.store.get(User, uid), self.s
        elif (token := request.cookies.get("pio")) and (uid := real.store.setting(f"session:{token}")):
            self.user, self.session = real.store.get(User, uid), token
        self.demo = self.hub is sandbox
        self.photos = DEMO_PHOTOS if self.demo else PHOTOS

    def link(self, path: str) -> str:
        if not self.s:
            return path
        return f"{path}{'&' if '?' in path else '?'}s={quote(self.s)}"

    def photo_url(self, item: Item | None) -> str | None:
        return self.link(f"/photos/{item.photo}") if item and item.photo else None

    def flash(self, text: str) -> None:
        """One line shown once on the next page ("Confirmed", "It's with you")."""
        if self.session:
            self.hub.store.set_setting(f"flash:{self.session}", text)

    def page(self, name: str, **context) -> HTMLResponse:
        toast = None
        if self.session and (toast := self.hub.store.setting(f"flash:{self.session}")):
            self.hub.store.set_setting(f"flash:{self.session}", None)
        return templates.TemplateResponse(self.request, name, {
            "me": self.user, "link": self.link, "demo": self.demo, "s": self.s, "now": self.hub.now(),
            "campus": campus, "clock": clock, "day": day, "toast": toast, **context})

    def back(self, path: str = "/", flash: str | None = None) -> RedirectResponse:
        if flash:
            self.flash(flash)
        return RedirectResponse(self.link(path), status_code=303)


def signed_in(ctx: Ctx) -> RedirectResponse | None:
    return None if ctx.user else RedirectResponse(f"/?next={quote(ctx.request.url.path)}", status_code=303)


def names(hub: Hub) -> dict[str, str]:
    return {u.id: u.name for u in hub.store.all(User)}


def owner_label(hub: Hub, item: Item, me: User) -> str:
    """'your calculator' or "Maya's calculator"."""
    owner = hub.store.get(User, item.owner_id)
    return f"your {voice.thing(item)}" if owner.id == me.id else f"{owner.name}'s {voice.thing(item)}"


def greeting(now: datetime) -> str:
    return "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"


# ---- what each person sees

def handoff_cards(ctx: Ctx) -> list[dict]:
    """This person's own handoffs, in plain words, with what they can do now."""
    hub, me = ctx.hub, ctx.user
    who = names(hub)
    cards = []
    for leg in sorted(hub.store.all(Leg), key=lambda l: l.pickup_time):
        if me.id not in (leg.holder_id, leg.giver_id) or leg.status not in LIVE_LEGS:
            continue
        item = hub.store.get(Item, leg.item_id)
        receiving = leg.holder_id == me.id
        if receiving and item.owner_id == me.id:
            title = f"{who[leg.giver_id]} brings it home"
        elif receiving:
            title = f"Get {owner_label(hub, item, me)}" + ("" if leg.giver_id == item.owner_id else f" from {who[leg.giver_id]}")
        else:
            title = f"Bring it back to {who[leg.holder_id]}" if leg.holder_id == item.owner_id else f"Hand it to {who[leg.holder_id]}"
        cards.append({"kind": "handoff", "id": leg.id, "title": title, "when": clock(leg.pickup_time),
                      "where": campus.name(leg.pickup_building), "confirmed": me.id in leg.confirmed_by,
                      "now": abs(leg.pickup_time - hub.now()) <= timedelta(minutes=45),
                      "just_confirmed": ctx.request.query_params.get("leg") == leg.id,
                      "confirm_url": ctx.link(f"/legs/{leg.id}/confirm"), "go_href": ctx.link(f"/legs/{leg.id}"),
                      "go_label": "I got it" if receiving else "Hand it over"})
    return cards


def stuff_rows(ctx: Ctx) -> list[dict]:
    hub = ctx.hub
    who = names(hub)
    rows = []
    for item in sorted(hub.store.all(Item, owner_id=ctx.user.id), key=lambda i: i.created_at):
        if not item.listed:
            status, line, href = "not_listed", "Finish listing it", f"/items/{item.id}/edit"
        elif item.mode == "give_away":
            status, line, href = "new_home", "Asking classmates who'd like it", f"/items/{item.id}"
        else:
            status, href = "lending", f"/items/{item.id}"
            live = hub.legs(item.id, statuses=("planned", "confirmed", "active"))
            back_by = clock(datetime.combine(hub.now().date(), time.fromisoformat(item.rules.return_by)))
            if item.holder_id:
                line = f"With {who[item.holder_id]} now, home by {back_by}"
            elif live:
                line = f"Out today, home by {back_by}"
            else:
                line = "I'll text you when someone asks"
        rows.append({"id": item.id, "href": ctx.link(href), "photo": ctx.photo_url(item), "name": item.type,
                     "line": line, "status": status})
    return rows


BORROW_STATUS = {"pending": "waiting_owner", "planned": "planned", "confirmed": "confirmed", "active": "with_you",
                 "waitlisted": "waiting_list"}


def borrow_rows(ctx: Ctx) -> list[dict]:
    hub = ctx.hub
    rows = []
    for req in sorted(hub.store.all(BorrowRequest, borrower_id=ctx.user.id), key=lambda r: r.window_start):
        if req.status not in BORROW_STATUS:
            continue
        item = hub.store.get(Item, req.item_id) if req.item_id else None
        mine = [l for l in hub.legs(item.id) if l.holder_id == ctx.user.id and l.request_id == req.id] if item else []
        if req.status == "active" and item:
            nxt = [l for l in hub.legs(item.id) if l.giver_id == ctx.user.id]
            line = f"Hand it on at {clock(nxt[0].pickup_time)}" if nxt else "It's with you"
        elif mine:
            line = f"{mine[0].pickup_time:%a} {clock(mine[0].pickup_time)} · {campus.name(mine[0].pickup_building)}"
        else:
            line = f"{req.window_start:%a} {clock(req.window_start)} · {campus.name(req.building)}"
        rows.append({"id": req.id, "href": ctx.link(f"/borrow/{req.id}"), "photo": ctx.photo_url(item),
                     "name": f"{hub.store.get(User, item.owner_id).name}'s {item.type}" if item else req.item_type.capitalize(),
                     "line": line, "status": BORROW_STATUS[req.status]})
    return rows


# ---- sign in

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    ctx = Ctx(request)
    if not ctx.user:
        q = request.query_params
        return ctx.page("welcome.html", next=q.get("next", ""), sent=q.get("sent"), expired=q.get("expired"),
                        forgotten=q.get("forgotten"))
    if not ctx.user.name:
        return ctx.back("/name")
    hub, me = ctx.hub, ctx.user
    who = names(hub)
    waiting = handoff_cards(ctx)
    for req in hub.store.all(BorrowRequest, status="pending"):
        item = hub.store.get(Item, req.item_id) if req.item_id else None
        if item and item.owner_id == me.id:
            waiting.append({"kind": "ask", "title": f"{who[req.borrower_id]} wants your {item.type}",
                            "when": window(req.window_start, req.window_end), "where": campus.name(req.building),
                            "url": ctx.link(f"/requests/{req.id}/answer")})
    for item in hub.store.all(Item, mode="give_away"):
        if hub.store.setting(f"moveout_offer:{item.id}") == me.id:
            waiting.append({"kind": "ask", "title": f"Want to keep this {item.type}?", "text": "Its owner is moving out.",
                            "yes": "Yes, it's mine", "no": "No thanks", "url": ctx.link(f"/moveout/{item.id}/answer")})
    for exam in hub.offers(me.id):
        waiting.append({"kind": "find", "title": f"{exam.course} exam {exam.start:%a}",
                        "text": "You have an exam and no calculator listed. Want me to find one?",
                        "url": ctx.link(f"/borrow/exam/{exam.uid}")})
    return ctx.page("home.html", active_tab="home", today=f"{hub.now():%a, %b %-d}", hello=greeting(hub.now()),
                    waiting=waiting, stuff=stuff_rows(ctx), borrowing=borrow_rows(ctx),
                    has_calendar=bool(hub.store.all(CalEvent, user_id=me.id)))


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}" if 11 <= len(digits) <= 15 else None


@app.post("/login")
async def login(request: Request, phone: str = Form(...), next: str = Form("")):
    ctx = Ctx(request)
    number = normalize_phone(phone)
    if not number:
        return ctx.page("welcome.html", error="That doesn't look like a phone number. Try (480) 555-0123.", next=next)
    user = (real.store.all(User, phone=number) or [None])[0] or User(id=new_id(), name="", phone=number)
    if real.messenger.channel == "telegram" and not user.telegram_chat_id:
        user.telegram_chat_id = await real.messenger.latest_chat()
    real.store.put(user)
    token = secrets.token_urlsafe(24)
    real.store.set_setting(f"magic:{token}", f"{user.id}|{(datetime.now(TZ) + MAGIC_VALID).isoformat()}")
    target = f"{base_url()}/m/{token}" + (f"?next={quote(next)}" if next.startswith("/") else "")
    await notify(real.store, real.messenger, user, f"Your Pass It On sign-in link (15 min): {target}", kind="login")
    if real.messenger.channel == "outbox":
        # No texting set up yet (local testing only): show the link instead of texting it.
        return ctx.page("welcome.html", sent=True, dev_link=target)
    return RedirectResponse("/?sent=1", status_code=303)


@app.get("/m/{token}")
async def magic(token: str, next: str = ""):
    value = real.store.setting(f"magic:{token}")
    real.store.set_setting(f"magic:{token}", None)  # one use
    if not value:
        return RedirectResponse("/?expired=1", status_code=303)
    user_id, until = value.split("|")
    if datetime.fromisoformat(until) < datetime.now(TZ):
        return RedirectResponse("/?expired=1", status_code=303)
    session = secrets.token_urlsafe(24)
    real.store.set_setting(f"session:{session}", user_id)
    response = RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)
    response.set_cookie("pio", session, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
                        secure=base_url().startswith("https://"))
    return response


@app.get("/name", response_class=HTMLResponse)
async def name_page(request: Request):
    ctx = Ctx(request)
    return signed_in(ctx) or ctx.page("name.html")


@app.post("/name")
async def name_save(request: Request, name: str = Form(...)):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    first = " ".join(name.split()).split(" ")[0][:24]
    if not first:
        return ctx.page("name.html", error="Type your first name.")
    ctx.user.name = first[:1].upper() + first[1:]
    ctx.hub.store.put(ctx.user)
    return ctx.back("/", flash=f"Welcome, {ctx.user.name}.")


@app.post("/logout")
async def logout(request: Request):
    if token := request.cookies.get("pio"):
        real.store.set_setting(f"session:{token}", None)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("pio")
    return response


@app.post("/forget")
async def forget(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    for item in ctx.hub.store.all(Item, owner_id=ctx.user.id):
        (ctx.photos / f"{item.id}.jpg").unlink(missing_ok=True)
    ctx.hub.store.forget(ctx.user.id)
    response = RedirectResponse("/?forgotten=1", status_code=303)
    response.delete_cookie("pio")
    return response


# ---- lend: photo -> looking -> is this right? -> listed

@app.get("/list", response_class=HTMLResponse)
async def list_page(request: Request):
    ctx = Ctx(request)
    return signed_in(ctx) or ctx.page("list.html", active_tab="lend")


@app.post("/list")
async def list_photo(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    # The screen offers the camera tile, a camera button and the photo library: use whichever was picked.
    picked = [v for v in (await request.form()).values() if hasattr(v, "filename") and v.filename]
    if not picked:
        return ctx.page("list.html", active_tab="lend", error="Take a photo, or choose one from your photos.")
    data = await picked[0].read()
    kind = (picked[0].content_type or "").split(";")[0]
    if not kind.startswith("image/") or not data or len(data) > MAX_PHOTO:
        return ctx.page("list.html", active_tab="lend", error="Send a photo (JPG or PNG) under 8 MB.")
    try:
        data = shrink(data)
    except Exception:
        return ctx.page("list.html", active_tab="lend", error="That file isn't a photo I can open. Try another.")
    item = Item(id=new_id(), owner_id=ctx.user.id, type="", photo=None, created_at=ctx.hub.now())
    item.photo = f"{item.id}.jpg"
    ctx.photos.mkdir(parents=True, exist_ok=True)
    (ctx.photos / item.photo).write_bytes(data)
    ctx.hub.store.put(item)
    ctx.hub.store.set_setting(f"look:{item.id}", f"pending|{datetime.now(TZ).isoformat()}")
    asyncio.create_task(look(ctx.hub, item.id, data))
    return ctx.back(f"/list/{item.id}/looking")


async def look(hub: Hub, item_id: str, data: bytes) -> None:
    """Runs while the phone shows "Looking at your photo…", which refreshes until this finishes."""
    try:
        found = await vision.identify(data, "image/jpeg")
    except (anthropic.APIError, llm.ModelError):
        log.exception("Couldn't identify a photo")
        hub.store.set_setting(f"look:{item_id}", "error|I couldn't look at that just now. Try again in a minute.")
        return
    if not found.found:
        hub.store.set_setting(f"look:{item_id}", "error|I couldn't make out one item. Try a closer, brighter photo on a plain surface.")
        return
    if not found.allowed:
        hub.store.set_setting(f"look:{item_id}", f"refused|{found.why_not or 'That can’t be lent on Pass It On.'}")
        return
    item = hub.store.get(Item, item_id)
    item.type, item.category, item.value_tier = found.item_type, found.category, found.value_tier
    item.condition, item.fingerprint = found.condition, found.fingerprint
    item.rules = Rules(min_rating=rating.DEFAULT_MIN[found.value_tier], auto_approve=found.value_tier == "low")
    hub.store.put(item)
    hub.store.set_setting(f"look:{item_id}", "done|")


@app.get("/list/{item_id}/looking", response_class=HTMLResponse)
async def looking(request: Request, item_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    if not (item := my_item(ctx, item_id)):
        return ctx.back("/")
    state, _, detail = (ctx.hub.store.setting(f"look:{item_id}") or "done|").partition("|")
    if state == "done":
        return ctx.back(f"/items/{item_id}/edit")
    if state in ("error", "refused"):
        return ctx.page("lend_error.html", active_tab="lend", item=item, photo=ctx.photo_url(item), error=detail,
                        refused=state == "refused")
    seconds = (datetime.now(TZ) - datetime.fromisoformat(detail)).total_seconds()
    step = min(2, int(seconds // 3.5))
    return ctx.page("lend_looking.html", active_tab="lend", item=item, photo=ctx.photo_url(item), step=step,
                    steps=LOOK_STEPS)


def shrink(data: bytes) -> bytes:
    """Phone photos can be huge and sideways; the model only needs a clear, upright 1600px JPEG."""
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((1600, 1600))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def my_item(ctx: Ctx, item_id: str) -> Item | None:
    item = ctx.hub.store.get(Item, item_id)
    return item if item and ctx.user and item.owner_id == ctx.user.id else None


def marks(item: Item) -> list[str]:
    parts = [p.strip(" .") for p in re.split(r";|\n", item.fingerprint or "") if p.strip(" .")]
    return [p[:1].upper() + p[1:] for p in parts]


CONDITIONS = (("new", "Like new"), ("good", "Good"), ("worn", "Worn"))
VALUES = (("low", "Under $30"), ("mid", "$30–150"), ("high", "Over $150"))
RATINGS = (("new", "Anyone"), ("ok", "Okay+"), ("good", "Good+"), ("great", "Great"))
AUDIENCES = (("asu", "Anyone at ASU"), ("friends_of_friends", "Friends of friends"), ("friends", "Friends"))
PERSONAS = (("dramatic", "Dramatic", "Every handoff is a cliffhanger."), ("chill", "Chill", "Low-key. Never panics."),
            ("wholesome", "Wholesome", "Kind, upbeat, says thank you."))


def condition_key(text: str) -> str:
    lowered = text.lower()
    return "new" if re.search(r"new|mint|excellent", lowered) else "worn" if re.search(r"worn|heavy|poor|damag", lowered) else "good"


@app.get("/items/{item_id}/edit", response_class=HTMLResponse)
async def item_edit(request: Request, item_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    if not (item := my_item(ctx, item_id)):
        return ctx.back("/")
    samples = {key: voice.template("your_pickup", item.model_copy(update={"persona": key, "category": item.category}),
                                   {"owner": ctx.user.name, "other": "Jay", "building": "Coor Hall", "time": "11:45 AM"})
               for key, _, _ in PERSONAS}
    return ctx.page("item_edit.html", item=item, photo=ctx.photo_url(item), marks=marks(item), samples=samples,
                    buildings=campus.buildings(), condition=condition_key(item.condition), conditions=CONDITIONS,
                    values=VALUES, ratings=RATINGS, audiences=AUDIENCES, personas=PERSONAS,
                    value_label=dict(VALUES)[item.value_tier], home_label=campus.name(item.rules.home_building),
                    return_label=clock(datetime.combine(ctx.hub.now().date(), time.fromisoformat(item.rules.return_by))),
                    error=request.query_params.get("error"))


@app.post("/items/{item_id}/edit")
async def item_save(request: Request, item_id: str, type: str = Form(""), fingerprint: str = Form(""),
                    condition: str = Form("good"), value_tier: str = Form(""), persona: str = Form("wholesome"),
                    return_by: str = Form("21:00"), home: str = Form("MU"), audience: str = Form("asu"),
                    min_rating: str = Form("new"), auto_approve: str = Form("")):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    if not (item := my_item(ctx, item_id)):
        return ctx.back("/")
    kind = " ".join(type.split())[:60]
    if not kind or vision.NOT_ALLOWED.search(kind):
        why = "Say what it is, like “TI-84 Plus CE”." if not kind else "Pass It On is for physical things like calculators and gear."
        return ctx.back(f"/items/{item.id}/edit?error={quote(why)}")
    try:
        time.fromisoformat(return_by)
    except ValueError:
        return_by = "21:00"
    item.type = kind
    item.fingerprint = "; ".join(m.strip() for m in re.split(r";|\n", fingerprint) if m.strip())[:400]
    item.condition = dict(CONDITIONS).get(condition, "Good")
    if value_tier in ("low", "mid", "high"):
        item.value_tier = value_tier
    item.persona = persona if persona in ("dramatic", "chill", "wholesome") else "wholesome"
    item.rules = Rules(return_by=return_by, home_building=home if campus.is_building(home) else "MU",
                       audience=audience if audience in dict(AUDIENCES) else "asu",
                       min_rating=min_rating if min_rating in rating.ORDER else "new", auto_approve=bool(auto_approve))
    first_time = not item.listed
    item.listed = True
    ctx.hub.store.put(item)
    ctx.hub.store.set_setting(f"look:{item.id}", None)
    return ctx.back("/" if first_time else f"/items/{item.id}",
                    flash=f"Listed. Your {item.type} is ready to lend." if first_time else "Saved.")


@app.get("/items/{item_id}", response_class=HTMLResponse)
async def item_page(request: Request, item_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    item = ctx.hub.store.get(Item, item_id)
    if not item:
        return ctx.back("/")
    hub, who, now = ctx.hub, names(ctx.hub), ctx.hub.now()
    mine = item.owner_id == ctx.user.id
    legs = [l for l in hub.legs(item.id, statuses=("planned", "confirmed", "active", "done"))
            if l.pickup_time.date() == now.date() or l.status in ("planned", "confirmed", "active")] if mine else []
    stops = []
    for leg in legs:
        stops.append({"time": clock(leg.pickup_time), "from": who[leg.giver_id], "to": who[leg.holder_id],
                      "where": campus.name(leg.pickup_building), "state": "done" if leg.status in ("active", "done") else ""})
        if leg.status == "active" and leg.holder_id == item.holder_id:
            nxt = [l for l in legs if l.status in LIVE_LEGS and l.giver_id == leg.holder_id]
            stops.append({"time": "Now", "label": f"With {who[leg.holder_id]}", "state": "now",
                          "where": f"Until {clock(nxt[0].pickup_time)}" if nxt else "For now"})
    holder = None
    if item.holder_id:
        nxt = [l for l in hub.legs(item.id) if l.giver_id == item.holder_id]
        holder = {"initial": who[item.holder_id][:1], "name": who[item.holder_id],
                  "line": f"Until {clock(nxt[0].pickup_time)} · {campus.name(nxt[0].pickup_building)}" if nxt else "Borrowing it now"}
    status = "new_home" if item.mode == "give_away" else "lending" if item.listed else "not_listed"
    return ctx.page("item.html", active_tab="home", item=item, mine=mine, photo=ctx.photo_url(item), stops=stops, holder=holder,
                    status=status, marks=marks(item), owner=who.get(item.owner_id),
                    home_line=f"Home by {clock(datetime.combine(now.date(), time.fromisoformat(item.rules.return_by)))} at {campus.name(item.rules.home_building)}",
                    served=hub.exams_served(item.id, now - timedelta(days=7)), today=f"{now:%a, %b %-d}")


@app.get("/photos/{name}")
async def photo(request: Request, name: str):
    ctx = Ctx(request)
    if not ctx.user or not re.fullmatch(r"[a-z0-9]{6,16}(-in)?\.jpg", name):
        return Response(status_code=404)
    path = ctx.photos / name
    return Response(path.read_bytes(), media_type="image/jpeg") if path.exists() else Response(status_code=404)


# ---- calendar

@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    hub = ctx.hub
    events = sorted(hub.store.all(CalEvent, user_id=ctx.user.id), key=lambda e: e.start)
    offered = {e.uid for e in hub.offers(ctx.user.id)}
    upcoming = [e for e in calendar_feed.exams(events) if e.end >= hub.now() - timedelta(hours=12)] or calendar_feed.exams(events)
    exams = [{"uid": e.uid, "dow": f"{e.start:%a}", "day": e.start.day, "name": e.title.split(" [")[0] if not e.course else f"{e.course} {e.title.split(' [')[0]}",
              "line": f"{e.start:%-I:%M}–{clock(e.end)} · {campus.name(e.building) if e.building else (e.location or 'No room listed')}",
              "needs": e.uid in offered} for e in upcoming]
    return ctx.page("calendar.html", active_tab="calendar", exams=exams, connected=bool(events),
                    changing=bool(request.query_params.get("change")), error=request.query_params.get("error"))


@app.post("/calendar")
async def calendar_import(request: Request, file: UploadFile | None = File(None), url: str = Form(""),
                          sample: str = Form("")):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    try:
        if sample in ("maya", "jay", "priya", "dev"):
            data = (ROOT / "assets" / "samples" / f"{sample}.ics").read_bytes()
        elif file is not None and file.filename:
            data = await file.read()
        elif url.strip():
            data = await calendar_feed.fetch(url.strip())
        else:
            raise calendar_feed.FeedError("Paste your Canvas calendar link, or upload the .ics file.")
        events = calendar_feed.parse(data, ctx.user.id)
    except calendar_feed.FeedError as e:
        return ctx.back(f"/calendar?change=1&error={quote(str(e))}")
    old = {e.uid: e for e in ctx.hub.store.all(CalEvent, user_id=ctx.user.id)}
    for uid in old:
        ctx.hub.store.delete(CalEvent, uid)
    for event in events:
        ctx.hub.store.put(event)
    # A moved exam moves any request made for it, and the items re-plan.
    for event in events:
        if (before := old.get(event.uid)) and before.start != event.start:
            ctx.hub.store.put(before)
            await ctx.hub.exam_moved(event.uid, event.start - before.start)
    for item in ctx.hub.store.all(Item, owner_id=ctx.user.id):
        if any(r.status in ("planned", "confirmed", "active") for r in ctx.hub.store.all(BorrowRequest, item_id=item.id)):
            await ctx.hub.replan(item.id)
    count = len(calendar_feed.exams(events))
    return ctx.back("/calendar", flash=f"Connected. I found {count} exam{'' if count == 1 else 's'}.")


# ---- borrow

@app.get("/borrow", response_class=HTMLResponse)
async def borrow_page(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    offers = [{"title": f"{e.course} exam {e.start:%a}", "text": "You have an exam and no calculator listed. Want me to find one?",
               "url": ctx.link(f"/borrow/exam/{e.uid}")} for e in ctx.hub.offers(ctx.user.id)]
    return ctx.page("borrow.html", active_tab="borrow", offers=offers)


@app.post("/borrow")
async def borrow(request: Request, text: str = Form(...)):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect

    def again(error: str) -> HTMLResponse:
        return ctx.page("borrow.html", active_tab="borrow", error=error, text=text, offers=[])

    try:
        need = await parse.read(text, ctx.hub.now().date())
    except parse.NotUnderstood as e:
        return again(str(e))
    except (anthropic.APIError, llm.ModelError):
        log.exception("Couldn't read a borrow text")
        return again("I couldn't read that just now. Try again in a minute.")
    if need.window_start < ctx.hub.now():
        return again("That time has already passed. When do you need it next?")
    req = await ctx.hub.request(ctx.user.id, need.item_type, need.window_start, need.window_end, need.building)
    return ctx.back(f"/borrow/{req.id}")


@app.post("/borrow/exam/{uid:path}")
async def borrow_for_exam(request: Request, uid: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    event = ctx.hub.store.get(CalEvent, uid)
    if event and event.user_id == ctx.user.id and event.building:
        req = await ctx.hub.request(ctx.user.id, "calculator", event.start, event.end, event.building, event_uid=event.uid)
        return ctx.back(f"/borrow/{req.id}")
    return ctx.back("/")


@app.get("/borrow/{request_id}", response_class=HTMLResponse)
async def borrow_result(request: Request, request_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    hub, who = ctx.hub, names(ctx.hub)
    req = hub.store.get(BorrowRequest, request_id)
    if not req or req.borrower_id != ctx.user.id:
        return ctx.back("/borrow")
    item = hub.store.get(Item, req.item_id) if req.item_id else None
    for_line = f"For {req.window_start:%a} {clock(req.window_start)} in {campus.name(req.building)}"
    if item is None:
        return ctx.page("borrow_waiting.html", active_tab="borrow", req=req, for_line=for_line,
                        line=f"{req.window_start:%a} {clock(req.window_start)} · {campus.name(req.building)}")
    me = ctx.user.id
    legs = [l for l in hub.legs(item.id, statuses=("planned", "confirmed", "active")) if me in (l.holder_id, l.giver_id)]
    stops = [{"time": clock(l.pickup_time), "from": "You" if l.giver_id == me else who[l.giver_id],
              "to": "You" if l.holder_id == me else who[l.holder_id], "where": campus.name(l.pickup_building)} for l in legs]
    unconfirmed = any(me not in l.confirmed_by for l in legs if l.status in LIVE_LEGS)
    return ctx.page("borrow_found.html", active_tab="borrow", req=req, item=item, photo=ctx.photo_url(item),
                    owner=who[item.owner_id], status=BORROW_STATUS.get(req.status, "planned"), stops=stops,
                    unconfirmed=unconfirmed, for_line=for_line)


@app.post("/borrow/{request_id}/confirm")
async def borrow_confirm(request: Request, request_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    req = ctx.hub.store.get(BorrowRequest, request_id)
    if req and req.borrower_id == ctx.user.id and req.item_id:
        for leg in ctx.hub.legs(req.item_id):
            if ctx.user.id in (leg.holder_id, leg.giver_id):
                ctx.hub.confirm(leg.id, ctx.user.id)
    return ctx.back("/", flash="Confirmed. I'll text you before each handoff.")


@app.post("/requests/{request_id}/answer")
async def answer_request(request: Request, request_id: str, answer: str = Form("")):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    req = ctx.hub.store.get(BorrowRequest, request_id)
    who = ctx.hub.store.get(User, req.borrower_id).name if req else "them"
    await ctx.hub.approve(request_id, ctx.user.id, answer == "yes")
    return ctx.back("/", flash="You said yes. I'll plan the handoff." if answer == "yes" else f"Okay. I'll let {who} know.")


@app.post("/requests/{request_id}/cancel")
async def cancel_request(request: Request, request_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    req = ctx.hub.store.get(BorrowRequest, request_id)
    if req and req.borrower_id == ctx.user.id and req.status not in ("active", "done"):
        item_id, req.status, req.item_id = req.item_id, "cancelled", None
        ctx.hub.store.put(req)
        if item_id:
            await ctx.hub.replan(item_id)
    return ctx.back("/", flash="Done. You're off the list.")


# ---- handoffs

@app.post("/legs/{leg_id}/confirm")
async def confirm_leg(request: Request, leg_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    ctx.hub.confirm(leg_id, ctx.user.id)
    return ctx.back(f"/?leg={leg_id}", flash="Confirmed.")


def next_part(hub: Hub, item: Item, user_id: str) -> Leg | None:
    nxt = [l for l in hub.legs(item.id) if l.giver_id == user_id]
    return nxt[0] if nxt else None


@app.get("/legs/{leg_id}", response_class=HTMLResponse)
async def leg_page(request: Request, leg_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    hub = ctx.hub
    leg = hub.store.get(Leg, leg_id)
    if not leg or ctx.user.id not in (leg.holder_id, leg.giver_id):
        return ctx.back("/")
    item = hub.store.get(Item, leg.item_id)
    giving = leg.giver_id == ctx.user.id
    other = hub.store.get(User, leg.holder_id if giving else leg.giver_id)
    handed = leg.status in ("active", "done")
    common = {"leg": leg, "item": item, "other": other, "photo": ctx.photo_url(item), "marks": marks(item),
              "owner": owner_label(hub, item, ctx.user), "when": clock(leg.pickup_time),
              "where": campus.name(leg.pickup_building)}
    if giving and not handed:
        h = handoff.open_handoff(hub, leg.id, ctx.user.id)
        return ctx.page("handoff_give.html", h=h, qr=ctx.link(f"/legs/{leg.id}/qr.png"), **common)
    if giving:
        return ctx.page("handoff_given.html", **common)
    if not handed:
        return ctx.page("handoff_get.html", error=request.query_params.get("error"), **common)
    nxt = next_part(hub, item, ctx.user.id)
    after = (f"Next, hand it to {hub.store.get(User, nxt.holder_id).name} at {clock(nxt.pickup_time)} at "
             f"{campus.name(nxt.pickup_building)}." if nxt else "It's yours to keep safe for now.")
    if item.owner_id == ctx.user.id:
        after = "It's back home. Thanks for sharing it."
    return ctx.page("handoff_done.html", after=after, me_initial=ctx.user.name[:1], **common)


@app.get("/legs/{leg_id}/qr.png")
async def leg_qr(request: Request, leg_id: str):
    ctx = Ctx(request)
    leg = ctx.hub.store.get(Leg, leg_id)
    if not ctx.user or not leg or leg.giver_id != ctx.user.id:
        return Response(status_code=404)
    h = handoff.open_handoff(ctx.hub, leg.id, ctx.user.id)
    if not h:
        return Response(status_code=404)
    return Response(handoff.qr_png(f"{base_url()}/h/{h.token}"), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/h/{token}", response_class=HTMLResponse)
async def scan(request: Request, token: str):
    ctx = Ctx(request)
    if not ctx.user:
        return RedirectResponse(f"/?next={quote(f'/h/{token}')}", status_code=303)
    leg, error = await handoff.receive(ctx.hub, ctx.user.id, token=token)
    if error:
        return ctx.page("message.html", title="Not this time", body=error, warn=True)
    return ctx.back(f"/legs/{leg.id}")


@app.post("/legs/{leg_id}/code")
async def enter_code(request: Request, leg_id: str, code: str = Form(...)):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    leg, error = await handoff.receive(ctx.hub, ctx.user.id, leg_id=leg_id, code=code.strip())
    if error:
        return ctx.back(f"/legs/{leg_id}?error={quote(error)}")
    return ctx.back(f"/legs/{leg.id}")


@app.post("/legs/{leg_id}/photo")
async def photo_checkin(request: Request, leg_id: str, photo: UploadFile = File(...)):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    leg = ctx.hub.store.get(Leg, leg_id)
    if not leg or leg.holder_id != ctx.user.id:
        return ctx.back("/")
    item = ctx.hub.store.get(Item, leg.item_id)
    try:
        data = shrink(await photo.read())
    except Exception:
        return ctx.back(f"/legs/{leg.id}", flash="That file isn't a photo I can open.")
    (ctx.photos / f"{leg.id}-in.jpg").write_bytes(data)
    listing = ctx.photos / item.photo if item.photo else None
    if not listing or not listing.exists():
        return ctx.back(f"/legs/{leg.id}")
    try:
        check = await handoff.photo_check(ctx.hub, leg.id, ctx.user.id, listing.read_bytes(), "image/jpeg", data, "image/jpeg")
    except (anthropic.APIError, llm.ModelError):
        return ctx.page("message.html", title="Couldn't check the photo", body="Try again in a minute. The handoff itself already counted.", warn=True)
    return ctx.page("handoff_check.html", leg=leg, item=item, match=check.match, why=check.why, marks=marks(item),
                    checkin=ctx.link(f"/photos/{leg.id}-in.jpg"), owner=ctx.hub.store.get(User, item.owner_id).name)


# ---- move-out, texts

@app.post("/moveout")
async def start_moveout(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    await moveout.start(ctx.hub, ctx.user.id)
    return ctx.back("/", flash="I'll find your stuff a new home at ASU.")


@app.post("/moveout/{item_id}/answer")
async def answer_moveout(request: Request, item_id: str, answer: str = Form("")):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    await moveout.answer(ctx.hub, item_id, ctx.user.id, answer == "yes")
    return ctx.back("/", flash="It's yours now." if answer == "yes" else "Okay. I'll ask someone else.")


@app.get("/texts", response_class=HTMLResponse)
async def texts(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    hub, who = ctx.hub, names(ctx.hub)
    thread, last = [], None
    for m in hub.inbox(ctx.user.id):
        if m.kind == "login":
            continue
        item = hub.store.get(Item, m.item_id) if m.item_id else None
        stamp = None
        if last is None or m.at - last > timedelta(minutes=30) or m.at.date() != last.date():
            stamp = f"{m.at:%a} {clock(m.at)}" if last is None or m.at.date() != last.date() else clock(m.at)
        last = m.at
        thread.append({"stamp": stamp, "body": m.text, "mine": False,
                       "face": (ctx.photo_url(item) if item else None) or "/static/brand-mark.svg",
                       "from": f"{who.get(item.owner_id, '')}'s {voice.thing(item)}" if item else "Pass It On"})
    return ctx.page("texts.html", active_tab="texts", thread=thread, texting=real.messenger.channel != "outbox" and not ctx.demo)


# ---- demo

DEMO_ACTIONS = {"next": demo.next_handoff, "exam": demo.exam_plus_hour, "late": demo.jay_late, "moveout": demo.maya_moves_out}


async def ensure_demo() -> None:
    if not sandbox.store.get(Item, demo.ITEM_ID):
        await demo.seed(sandbox, DEMO_PHOTOS)


def happening() -> str:
    """One line for the demo page: what happens next in the story."""
    who = names(sandbox)
    live = sorted((l for l in sandbox.store.all(Leg) if l.status in LIVE_LEGS), key=lambda l: l.pickup_time)
    item = sandbox.store.get(Item, demo.ITEM_ID)
    if item and item.mode == "give_away":
        return "Maya is moving out. Her calculator is looking for its next owner."
    if not live:
        return "The calculator is home. Try “Maya moves out”."
    leg = live[0]
    verb = "brings it home to" if leg.holder_id == item.owner_id else "hands it to"
    return f"{who[leg.giver_id]} {verb} {who[leg.holder_id]} at {campus.name(leg.pickup_building)}, {clock(leg.pickup_time)}."


def roles() -> dict[str, str]:
    item = sandbox.store.get(Item, demo.ITEM_ID)
    out = {}
    for person in ("maya", "jay", "priya"):
        if item and person == item.owner_id:
            out[person] = "owner"
            continue
        if item and item.holder_id == person:
            nxt = next_part(sandbox, item, person)
            out[person] = f"has it until {clock(nxt.pickup_time)}" if nxt else "has it"
            continue
        gets = [l for l in sandbox.legs(demo.ITEM_ID) if l.holder_id == person]
        out[person] = f"next at {clock(gets[0].pickup_time)}" if gets else ""
    return out


@app.get("/demo", response_class=HTMLResponse)
async def demo_page(request: Request):
    await ensure_demo()
    return templates.TemplateResponse(request, "demo.html", {
        "sessions": {p: demo.SESSION[p] for p in ("maya", "jay", "priya")}, "now": sandbox.now(), "clock": clock,
        "day": day, "happening": happening(), "roles": roles(), "names": names(sandbox), "link": lambda p: p})


@app.post("/demo/{action}")
async def demo_action(action: str):
    if action == "reset":
        await demo.seed(sandbox, DEMO_PHOTOS)
    elif action in DEMO_ACTIONS:
        await ensure_demo()
        await DEMO_ACTIONS[action](sandbox)
    return demo_state()


@app.get("/demo/version")
async def demo_version_route():
    return demo_state()


def demo_state() -> dict:
    return {"version": demo_version(), "clock": f"{sandbox.now():%a %b %-d}, {clock(sandbox.now())}",
            "happening": happening(), "roles": roles()}


def demo_version() -> str:
    """Changes whenever anything a demo phone shows changes, so the phones know to refresh."""
    state = sandbox.store.db.execute("SELECT count(*), group_concat(length(data)) FROM docs").fetchone()
    return hashlib.sha1(f"{state}{sandbox.store.setting('clock')}".encode()).hexdigest()[:12]


@app.get("/healthz")
async def healthz():
    return {"ok": True, "texting": real.messenger.channel}
