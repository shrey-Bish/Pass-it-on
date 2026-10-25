"""Reliability rating, Chess.com-style: on-time handoffs and returns raise it, late ones lower it. Plain math.

Everyone starts at 1000 in the "new" bucket until they've done 3 handoffs. Only the bucket ever crosses the gate.
New borrowers start with low-value items: by default a mid-value item needs "ok" and a high-value one needs "good".
"""

from .models import Item, User

START = 1000
ON_TIME, LATE, VERY_LATE = 12, -15, -40
RETURN_BONUS = 8
LATE_AFTER, VERY_LATE_AFTER = 10, 45   # minutes
ORDER = ("new", "ok", "good", "great")
DEFAULT_MIN = {"low": "new", "mid": "ok", "high": "good"}


def bucket(user: User) -> str:
    if user.completed < 3:
        return "new"
    return "great" if user.rating >= 1150 else "good" if user.rating >= 1060 else "ok"


def at_least(user: User, needed: str) -> bool:
    return ORDER.index(bucket(user)) >= ORDER.index(needed)


def may_borrow(user: User, item: Item, override: bool = False) -> bool:
    """The owner can always say yes by hand; this is the automatic check."""
    return override or at_least(user, item.rules.min_rating)


def record(user: User, minutes_late: float, is_return: bool = False) -> int:
    """Updates the user for one finished handoff. Returns the change, so the text can say +12."""
    if minutes_late <= LATE_AFTER:
        change = ON_TIME + (RETURN_BONUS if is_return else 0)
    elif minutes_late <= VERY_LATE_AFTER:
        change = LATE
    else:
        change = VERY_LATE
    user.rating = max(600, user.rating + change)
    user.completed += 1
    return change
