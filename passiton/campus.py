"""ASU Tempe buildings and walking time between them. Plain math, no maps service.

Walking minutes = straight-line distance x 1.3 (paths aren't straight) / 80 m per minute, rounded up.
"""

import functools
import json
import math
import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
DETOUR = 1.3
METERS_PER_MINUTE = 80


@functools.cache
def buildings() -> dict[str, dict]:
    data = json.loads((ASSETS / "buildings.json").read_text())
    return {b["code"]: b for b in data["buildings"]}


def name(code: str) -> str:
    return buildings()[code]["name"]


def is_building(code: str | None) -> bool:
    return isinstance(code, str) and code in buildings()


def walk_minutes(a: str, b: str) -> int:
    if a == b:
        return 0
    p, q = buildings()[a], buildings()[b]
    lat1, lat2 = math.radians(p["lat"]), math.radians(q["lat"])
    dlat, dlon = lat2 - lat1, math.radians(q["lon"] - p["lon"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    meters = 2 * 6_371_000 * math.asin(math.sqrt(h))
    return max(1, math.ceil(meters * DETOUR / METERS_PER_MINUTE))


def find_building(text: str | None) -> str | None:
    """'PSH 151', 'Durham Hall 120' or 'Hayden Library' -> a building code. Anything else -> None."""
    if not text:
        return None
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]*", text):
        if token.upper() in buildings():
            return token.upper()
    lowered = text.lower()
    for code, b in buildings().items():
        if any(alias in lowered for alias in b["aliases"]) or b["name"].lower() in lowered:
            return code
    return None
