"""One-time handoff codes: a QR the giver shows, a 4-digit fallback, and an optional photo check-in.

Custody moves only when the person receiving it scans the giver's QR (or types the code) while signed in as that
person, before it expires. Each code works once, and the 4-digit code allows a few tries, not 10,000.
"""

import io
import secrets
from datetime import timedelta

import qrcode

from . import vision
from .agents import LIVE_LEGS, Hub, new_id
from .models import Handoff, Item, Leg, User

VALID = timedelta(minutes=30)
MAX_CODE_TRIES = 5


def open_handoff(hub: Hub, leg_id: str, user_id: str) -> Handoff | None:
    """The giver opens the handoff on their phone. Reuses an open one, so refreshing doesn't change the code."""
    leg = hub.store.get(Leg, leg_id)
    if leg is None or leg.giver_id != user_id or leg.status not in LIVE_LEGS:
        return None
    now = hub.now()
    for h in hub.store.all(Handoff, leg_id=leg_id):
        if not h.used and h.expires_at > now:
            return h
    h = Handoff(id=new_id(), leg_id=leg_id, token=secrets.token_urlsafe(16), code=f"{secrets.randbelow(10_000):04d}",
                confirmed_by_giver=True, expires_at=now + VALID)
    hub.store.put(h)
    return h


async def receive(hub: Hub, user_id: str, *, token: str | None = None, leg_id: str | None = None,
                  code: str | None = None) -> tuple[Leg | None, str | None]:
    """The receiver scans the QR (token) or types the code. Returns (leg, None) or (None, a plain reason)."""
    now = hub.now()
    if token:
        found = [h for h in hub.store.all(Handoff, token=token)]
        method = "qr"
    else:
        tries_key = f"code_tries:{leg_id}"
        tries = int(hub.store.setting(tries_key, "0"))
        if tries >= MAX_CODE_TRIES:
            return None, "Too many tries. Ask them to show the QR code instead."
        hub.store.set_setting(tries_key, str(tries + 1))
        found = [h for h in hub.store.all(Handoff, leg_id=leg_id) if code and secrets.compare_digest(h.code, code)]
        method = "code"
    h = found[0] if found else None
    if h is None:
        return None, "That code doesn't match. Check it with the person handing it over."
    if h.used:
        return None, "That code was already used."
    if h.expires_at <= now:
        return None, "That code expired. Ask them to open the handoff again."
    leg = hub.store.get(Leg, h.leg_id)
    if leg is None or leg.holder_id != user_id:
        return None, "This handoff is for someone else."
    h.used, h.confirmed_by_receiver, h.method = True, True, method
    hub.store.put(h)
    return await hub.transfer(leg.id), None


async def photo_check(hub: Hub, leg_id: str, user_id: str, listing: bytes, listing_type: str, photo: bytes,
                      photo_type: str) -> vision.PhotoCheck:
    """The receiver snaps the item. A mismatch is flagged to the owner, privately; it doesn't undo the handoff."""
    leg = hub.store.get(Leg, leg_id)
    item = hub.store.get(Item, leg.item_id)
    check = await vision.compare(listing, listing_type, photo, photo_type, item.fingerprint)
    for h in hub.store.all(Handoff, leg_id=leg_id):
        h.photo_match = "match" if check.match else "mismatch"
        h.method = "photo" if h.method == "qr" and not h.used else h.method
        hub.store.put(h)
    if not check.match:
        await hub.say(item.owner_id, "checkin_mismatch", item, {"other": hub.store.get(User, user_id).name},
                      kind="alert")
    return check


def qr_png(url: str) -> bytes:
    image = qrcode.make(url, box_size=10, border=2)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
