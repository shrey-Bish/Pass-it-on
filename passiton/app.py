"""FastAPI routes: sign-in by texted link, listing, calendar import, borrowing, confirmations, handoffs, the demo.

Run: .venv/bin/uvicorn passiton.app:app --host 0.0.0.0 --port 8000

The real app and the /demo world use separate databases. A demo phone is signed in by a fixed ?s= token so three
phones can sit side by side on one page; everyone else signs in with a link sent by text.
"""

import hashlib
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

from . import calendar_feed, campus, demo, handoff, llm, moveout, parse, rating, scheduler, vision, voice
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

app = FastAPI(title="Pass It On")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")

real = Hub(Store(DATA / "app.db"), from_env())
sandbox = Hub(Store(DATA / "demo.db"), Messenger())  # demo students have no phones: texts stay in the outbox


def base_url() -> str:
    return os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")


# ---- who's asking

class Ctx:
    def __init__(self, request: Request):
        self.request = request
        self.s = request.query_params.get("s") or ""
        self.hub, self.user = real, None
        if self.s and (uid := sandbox.store.setting(f"session:{self.s}")):
            self.hub, self.user = sandbox, sandbox.store.get(User, uid)
        elif (token := request.cookies.get("pio")) and (uid := real.store.setting(f"session:{token}")):
            self.user = real.store.get(User, uid)
        self.demo = self.hub is sandbox
        self.photos = DEMO_PHOTOS if self.demo else PHOTOS

    def link(self, path: str) -> str:
        if not self.s:
            return path
        return f"{path}{'&' if '?' in path else '?'}s={quote(self.s)}"

    def page(self, name: str, **context) -> HTMLResponse:
        return templates.TemplateResponse(self.request, name, {
            "me": self.user, "link": self.link, "demo": self.demo, "s": self.s, "now": self.hub.now(),
            "campus": campus, "clock": clock, "day": day, **context})

    def back(self, path: str = "/") -> RedirectResponse:
        return RedirectResponse(self.link(path), status_code=303)


def clock(t: datetime) -> str:
    return f"{t:%-I:%M %p}"


def day(t: datetime) -> str:
    return f"{t:%a %b %-d}"


def signed_in(ctx: Ctx) -> RedirectResponse | None:
    return None if ctx.user else RedirectResponse(f"/?next={quote(ctx.request.url.path)}", status_code=303)


# ---- what each person sees

def my_parts(ctx: Ctx) -> list[dict]:
    """This person's own legs, in plain words: who, where, when, and what they can do now."""
    hub, me = ctx.hub, ctx.user
    parts = []
    for leg in sorted(hub.store.all(Leg), key=lambda l: l.pickup_time):
        if me.id not in (leg.holder_id, leg.giver_id) or leg.status in ("cancelled", "done"):
            continue
        item = hub.store.get(Item, leg.item_id)
        receiving = leg.holder_id == me.id
        other = hub.store.get(User, leg.giver_id if receiving else leg.holder_id)
        if receiving and item.owner_id == me.id:
            what = f"{other.name} brings it home"
        elif receiving:
            what = f"Get {owner_label(hub, item, me)}" + ("" if other.id == item.owner_id else f" from {other.name}")
        else:
            what = f"Hand it to {other.name}" if leg.holder_id != item.owner_id else f"Bring it back to {other.name}"
        parts.append({"leg": leg, "item": item, "what": what, "receiving": receiving, "other": other,
                      "confirmed": me.id in leg.confirmed_by, "active": leg.status == "active",
                      "soon": leg.status in LIVE_LEGS and abs(leg.pickup_time - hub.now()) <= timedelta(minutes=45)})
    return parts


def owner_label(hub: Hub, item: Item, me: User) -> str:
    owner = hub.store.get(User, item.owner_id)
    thing = voice.thing(item)
    return f"your {thing}" if owner.id == me.id else f"{owner.name}'s {thing}"


STATUS_WORDS = {"pending": "Waiting for the owner", "planned": "Planned. Confirm your part", "confirmed": "Confirmed",
                "active": "With you", "done": "Done", "waitlisted": "On the waiting list", "cancelled": "Cancelled"}


# ---- sign in

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    ctx = Ctx(request)
    if not ctx.user:
        return ctx.page("welcome.html", next=request.query_params.get("next", ""), sent=request.query_params.get("sent"))
    if not ctx.user.name:
        return ctx.back("/name")
    hub, me = ctx.hub, ctx.user
    approvals = []
    for req in hub.store.all(BorrowRequest, status="pending"):
        item = hub.store.get(Item, req.item_id) if req.item_id else None
        if item and item.owner_id == me.id:
            approvals.append({"req": req, "item": item, "who": hub.store.get(User, req.borrower_id)})
    moveout_offers = [i for i in hub.store.all(Item, mode="give_away") if hub.store.setting(f"moveout_offer:{i.id}") == me.id]
    requests = sorted(hub.store.all(BorrowRequest, borrower_id=me.id), key=lambda r: r.window_start)
    return ctx.page("home.html", parts=my_parts(ctx), approvals=approvals, items=hub.store.all(Item, owner_id=me.id),
                    requests=[r for r in requests if r.status != "cancelled"], offers=hub.offers(me.id),
                    moveout_offers=moveout_offers, status_words=STATUS_WORDS,
                    owners={u.id: u.name for u in hub.store.all(User)})


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
        return ctx.page("welcome.html", error="That doesn't look like a phone number. Try 480 555 1234.", next=next)
    user = (real.store.all(User, phone=number) or [None])[0]
    if user is None:
        user = User(id=new_id(), name="", phone=number)
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
    return ctx.back("/")


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


# ---- list an item

@app.get("/list", response_class=HTMLResponse)
async def list_page(request: Request):
    ctx = Ctx(request)
    return signed_in(ctx) or ctx.page("list.html")


@app.post("/list")
async def list_photo(request: Request, photo: UploadFile = File(...)):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    data = await photo.read()
    kind = (photo.content_type or "").split(";")[0]
    if kind not in ("image/jpeg", "image/png", "image/webp") or not data or len(data) > MAX_PHOTO:
        return ctx.page("list.html", error="Send a photo (JPG or PNG) under 8 MB.")
    if kind != "image/jpeg" or len(data) > 1_500_000:
        data, kind = shrink(data), "image/jpeg"
    try:
        found = await vision.identify(data, kind)
    except (anthropic.APIError, llm.ModelError):
        log.exception("Couldn't identify a photo")
        return ctx.page("list.html", error="I couldn't look at that just now. Try again in a minute.")
    if not found.found:
        return ctx.page("list.html", error="I couldn't spot one item in that photo. Try a closer, brighter one.")
    if not found.allowed:
        return ctx.page("list.html", error=found.why_not or "That can't be lent on Pass It On.")
    item_id = new_id()
    (ctx.photos).mkdir(parents=True, exist_ok=True)
    (ctx.photos / f"{item_id}.jpg").write_bytes(data)
    item = Item(id=item_id, owner_id=ctx.user.id, type=found.item_type, category=found.category,
                value_tier=found.value_tier, condition=found.condition, fingerprint=found.fingerprint,
                photo=f"{item_id}.jpg", created_at=ctx.hub.now(),
                rules=Rules(min_rating=rating.DEFAULT_MIN[found.value_tier], auto_approve=found.value_tier == "low"))
    ctx.hub.store.put(item)
    return ctx.back(f"/items/{item_id}/edit")


def shrink(data: bytes) -> bytes:
    """Phone photos can be huge; the model only needs a clear 1600px JPEG."""
    import io

    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((1600, 1600))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def my_item(ctx: Ctx, item_id: str) -> Item | None:
    item = ctx.hub.store.get(Item, item_id)
    return item if item and ctx.user and item.owner_id == ctx.user.id else None


@app.get("/items/{item_id}/edit", response_class=HTMLResponse)
async def item_edit(request: Request, item_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    if not (item := my_item(ctx, item_id)):
        return ctx.back("/")
    samples = {p: voice.template("your_dropoff", item.model_copy(update={"persona": p}),
                                 {"owner": ctx.user.name, "other": "Priya", "building": "Durham Language & Literature",
                                  "time": "2:45 PM"}) for p in ("dramatic", "chill", "wholesome")}
    return ctx.page("item_edit.html", item=item, samples=samples, buildings=campus.buildings())


@app.post("/items/{item_id}/edit")
async def item_save(request: Request, item_id: str, type: str = Form(...), fingerprint: str = Form(""),
                    persona: str = Form("wholesome"), return_by: str = Form("21:00"), home: str = Form("MU"),
                    audience: str = Form("asu"), min_rating: str = Form("new"), auto_approve: str = Form("")):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    if not (item := my_item(ctx, item_id)):
        return ctx.back("/")
    try:
        time.fromisoformat(return_by)
    except ValueError:
        return_by = "21:00"
    item.type = " ".join(type.split())[:60] or item.type
    item.fingerprint = " ".join(fingerprint.split())[:400]
    item.persona = persona if persona in ("dramatic", "chill", "wholesome") else "wholesome"
    item.rules = Rules(return_by=return_by, home_building=home if campus.is_building(home) else "MU",
                       audience=audience if audience in ("asu", "friends_of_friends", "friends") else "asu",
                       min_rating=min_rating if min_rating in rating.ORDER else "new", auto_approve=bool(auto_approve))
    item.listed = True
    ctx.hub.store.put(item)
    return ctx.back(f"/items/{item.id}")


@app.get("/items/{item_id}", response_class=HTMLResponse)
async def item_page(request: Request, item_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    item = ctx.hub.store.get(Item, item_id)
    if not item:
        return ctx.back("/")
    mine = item.owner_id == ctx.user.id
    legs = ctx.hub.legs(item.id, statuses=("planned", "confirmed", "active", "done")) if mine else []
    names = {u.id: u.name for u in ctx.hub.store.all(User)}
    return ctx.page("item.html", item=item, mine=mine, legs=legs, names=names,
                    holder=names.get(item.holder_id or item.owner_id),
                    served=ctx.hub.exams_served(item.id, ctx.hub.now() - timedelta(days=7)))


@app.get("/photos/{name}")
async def photo(request: Request, name: str):
    ctx = Ctx(request)
    if not ctx.user or not re.fullmatch(r"[a-z0-9]{6,16}\.jpg", name):
        return Response(status_code=404)
    path = ctx.photos / name
    return Response(path.read_bytes(), media_type="image/jpeg") if path.exists() else Response(status_code=404)


# ---- calendar

@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    events = sorted(ctx.hub.store.all(CalEvent, user_id=ctx.user.id), key=lambda e: e.start)
    return ctx.page("calendar.html", exams=calendar_feed.exams(events),
                    classes=[e for e in events if e.kind == "class"], describe=calendar_feed.describe,
                    imported=request.query_params.get("imported"))


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
            raise calendar_feed.FeedError("Upload your .ics file or paste the Canvas feed link.")
        events = calendar_feed.parse(data, ctx.user.id)
    except calendar_feed.FeedError as e:
        return ctx.page("calendar.html", error=str(e), exams=[], classes=[], describe=calendar_feed.describe)
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
        if any(r.status in LIVE_LEGS + ("active",) for r in ctx.hub.store.all(BorrowRequest, item_id=item.id)):
            await ctx.hub.replan(item.id)
    return ctx.back(f"/calendar?imported={len(calendar_feed.exams(events))}")


# ---- borrow

@app.get("/borrow", response_class=HTMLResponse)
async def borrow_page(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    return ctx.page("borrow.html", offers=ctx.hub.offers(ctx.user.id))


@app.post("/borrow")
async def borrow(request: Request, text: str = Form(...)):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    try:
        need = await parse.read(text, ctx.hub.now().date())
    except parse.NotUnderstood as e:
        return ctx.page("borrow.html", error=str(e), text=text, offers=ctx.hub.offers(ctx.user.id))
    except (anthropic.APIError, llm.ModelError):
        log.exception("Couldn't read a borrow text")
        return ctx.page("borrow.html", error="I couldn't read that just now. Try again in a minute.", text=text,
                        offers=ctx.hub.offers(ctx.user.id))
    if need.window_start < ctx.hub.now():
        return ctx.page("borrow.html", error="That time has already passed. When do you need it next?", text=text,
                        offers=ctx.hub.offers(ctx.user.id))
    await ctx.hub.request(ctx.user.id, need.item_type, need.window_start, need.window_end, need.building)
    return ctx.back("/")


@app.post("/borrow/exam/{uid:path}")
async def borrow_for_exam(request: Request, uid: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    event = ctx.hub.store.get(CalEvent, uid)
    if event and event.user_id == ctx.user.id and event.building:
        await ctx.hub.request(ctx.user.id, "calculator", event.start, event.end, event.building, event_uid=event.uid)
    return ctx.back("/")


@app.post("/requests/{request_id}/answer")
async def answer_request(request: Request, request_id: str, yes: str = Form("")):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    await ctx.hub.approve(request_id, ctx.user.id, yes == "1")
    return ctx.back("/")


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
    return ctx.back("/")


# ---- plan, handoffs

@app.post("/legs/{leg_id}/confirm")
async def confirm_leg(request: Request, leg_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    ctx.hub.confirm(leg_id, ctx.user.id)
    return ctx.back("/")


@app.get("/legs/{leg_id}", response_class=HTMLResponse)
async def leg_page(request: Request, leg_id: str):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    leg = ctx.hub.store.get(Leg, leg_id)
    if not leg or ctx.user.id not in (leg.holder_id, leg.giver_id):
        return ctx.back("/")
    item = ctx.hub.store.get(Item, leg.item_id)
    giving = leg.giver_id == ctx.user.id
    h = handoff.open_handoff(ctx.hub, leg.id, ctx.user.id) if giving and leg.status in LIVE_LEGS else None
    other = ctx.hub.store.get(User, leg.holder_id if giving else leg.giver_id)
    return ctx.page("leg.html", leg=leg, item=item, giving=giving, h=h, other=other,
                    scan_url=f"{base_url()}/h/{h.token}" if h else None, done=request.query_params.get("done"),
                    check=request.query_params.get("check"), owner=owner_label(ctx.hub, item, ctx.user))


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
        return ctx.page("message.html", title="Not this time", body=error)
    return ctx.back(f"/legs/{leg.id}?done=1")


@app.post("/legs/{leg_id}/code")
async def enter_code(request: Request, leg_id: str, code: str = Form(...)):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    leg, error = await handoff.receive(ctx.hub, ctx.user.id, leg_id=leg_id, code=code.strip())
    if error:
        leg = ctx.hub.store.get(Leg, leg_id)
        item = ctx.hub.store.get(Item, leg.item_id) if leg else None
        if not leg or not item:
            return ctx.back("/")
        return ctx.page("leg.html", leg=leg, item=item, giving=False, h=None, error=error, done=None, check=None,
                        other=ctx.hub.store.get(User, leg.giver_id), owner=owner_label(ctx.hub, item, ctx.user))
    return ctx.back(f"/legs/{leg.id}?done=1")


@app.post("/legs/{leg_id}/photo")
async def photo_checkin(request: Request, leg_id: str, photo: UploadFile = File(...)):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    leg = ctx.hub.store.get(Leg, leg_id)
    if not leg or leg.holder_id != ctx.user.id:
        return ctx.back("/")
    item = ctx.hub.store.get(Item, leg.item_id)
    data = shrink(await photo.read())
    listing = (ctx.photos / item.photo).read_bytes() if item.photo and (ctx.photos / item.photo).exists() else None
    if listing is None:
        return ctx.back(f"/legs/{leg.id}?done=1")
    try:
        check = await handoff.photo_check(ctx.hub, leg.id, ctx.user.id, listing, "image/jpeg", data, "image/jpeg")
    except (anthropic.APIError, llm.ModelError):
        return ctx.page("message.html", title="Couldn't check the photo", body="Try again in a minute. The handoff itself already counted.")
    return ctx.back(f"/legs/{leg.id}?done=1&check={'match' if check.match else 'mismatch'}")


# ---- move-out, texts

@app.post("/moveout")
async def start_moveout(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    await moveout.start(ctx.hub, ctx.user.id)
    return ctx.back("/")


@app.post("/moveout/{item_id}/answer")
async def answer_moveout(request: Request, item_id: str, yes: str = Form("")):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    await moveout.answer(ctx.hub, item_id, ctx.user.id, yes == "1")
    return ctx.back("/")


@app.get("/texts", response_class=HTMLResponse)
async def texts(request: Request):
    ctx = Ctx(request)
    if redirect := signed_in(ctx):
        return redirect
    return ctx.page("texts.html", messages=list(reversed(ctx.hub.inbox(ctx.user.id))))


# ---- demo

DEMO_ACTIONS = {"next": demo.next_handoff, "exam": demo.exam_plus_hour, "late": demo.jay_late, "moveout": demo.maya_moves_out}


async def ensure_demo() -> None:
    if not sandbox.store.get(Item, demo.ITEM_ID):
        await demo.seed(sandbox, DEMO_PHOTOS)


@app.get("/demo", response_class=HTMLResponse)
async def demo_page(request: Request):
    await ensure_demo()
    return templates.TemplateResponse(request, "demo.html", {"sessions": demo.SESSION, "now": sandbox.now(),
                                                             "clock": clock, "day": day})


@app.post("/demo/{action}")
async def demo_action(action: str):
    if action == "reset":
        await demo.seed(sandbox, DEMO_PHOTOS)
    elif action in DEMO_ACTIONS:
        await ensure_demo()
        await DEMO_ACTIONS[action](sandbox)
    return {"clock": f"{day(sandbox.now())}, {clock(sandbox.now())}", "version": demo_version()}


@app.get("/demo/version")
async def demo_version_route():
    return {"version": demo_version(), "clock": f"{day(sandbox.now())}, {clock(sandbox.now())}"}


def demo_version() -> str:
    """Changes whenever anything a demo phone shows changes, so the phones know to refresh."""
    state = sandbox.store.db.execute("SELECT count(*), group_concat(length(data)) FROM docs").fetchone()
    return hashlib.sha1(f"{state}{sandbox.store.setting('clock')}".encode()).hexdigest()[:12]


@app.get("/healthz")
async def healthz():
    return {"ok": True, "texting": real.messenger.channel}
