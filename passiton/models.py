"""Data shapes shared by every module. Times are timezone-aware, in Arizona time (ASU never changes clocks)."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

TZ = ZoneInfo("America/Phoenix")

Persona = Literal["dramatic", "chill", "wholesome"]
Tier = Literal["low", "mid", "high"]
RatingBucket = Literal["new", "ok", "good", "great"]


class Rules(BaseModel):
    """The owner's rules for one item. Their own exams are always blocked; that isn't a setting."""

    return_by: str = "21:00"              # the item is home by this local time on any day it's lent
    home_building: str = "MU"             # where it starts and ends each day: a public campus building
    audience: Literal["friends", "friends_of_friends", "asu"] = "asu"
    min_rating: RatingBucket = "new"
    auto_approve: bool = False


class User(BaseModel):
    id: str
    name: str                             # first name: the only thing other students ever see
    phone: str | None = None
    telegram_chat_id: int | None = None
    rating: int = 1000
    completed: int = 0                    # finished handoffs and returns, for the "new" bucket
    friends: list[str] = Field(default_factory=list)
    demo: bool = False


class CalEvent(BaseModel):
    uid: str
    user_id: str
    title: str
    course: str | None = None
    kind: Literal["exam", "class", "assignment", "other"]
    start: datetime
    end: datetime
    location: str | None = None
    building: str | None = None           # a code from assets/buildings.json, when the location names one


class Item(BaseModel):
    id: str
    owner_id: str
    type: str                             # "TI-84 Plus CE"
    category: str = "other"
    value_tier: Tier = "low"
    condition: str = ""
    fingerprint: str = ""                 # distinguishing marks from the listing photo
    persona: Persona = "wholesome"
    rules: Rules = Field(default_factory=Rules)
    mode: Literal["lend", "give_away"] = "lend"
    photo: str | None = None              # file name under data/photos
    holder_id: str | None = None          # who has it right now; None means the owner
    listed: bool = False                  # the owner checked the card and saved it
    created_at: datetime


class Request(BaseModel):
    id: str
    borrower_id: str
    item_type: str
    window_start: datetime
    window_end: datetime
    building: str
    status: Literal["pending", "planned", "confirmed", "active", "done", "waitlisted", "cancelled"] = "pending"
    item_id: str | None = None
    created_at: datetime
    event_uid: str | None = None          # the borrower's own exam this is for, so a moved exam moves the window
    declined_by: list[str] = Field(default_factory=list)  # items whose owners said no, never tried again


class Leg(BaseModel):
    """One stop on the item's route: holder_id gets it from giver_id at pickup_building, pickup_time."""

    id: str
    item_id: str
    holder_id: str
    giver_id: str
    start: datetime                       # when they need it (their window) or when it's home again
    end: datetime
    pickup_building: str
    pickup_time: datetime
    request_id: str | None = None
    status: Literal["planned", "confirmed", "active", "done", "cancelled"] = "planned"
    confirmed_by: list[str] = Field(default_factory=list)


class Handoff(BaseModel):
    id: str
    leg_id: str
    method: Literal["qr", "code", "photo"] = "qr"
    token: str
    code: str
    confirmed_by_giver: bool = False
    confirmed_by_receiver: bool = False
    photo_match: Literal["match", "mismatch", "skipped"] = "skipped"
    used: bool = False
    expires_at: datetime


class Message(BaseModel):
    """Every text the app sends, kept so the demo phones and tests can see it."""

    id: str
    user_id: str
    text: str
    at: datetime
    channel: str = "outbox"
    item_id: str | None = None
    kind: str = "info"
