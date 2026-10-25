"""Calls the real model on the sample photos and a sample text. Costs API calls, so it only runs with RUN_LIVE=1:

    RUN_LIVE=1 .venv/bin/pytest tests/test_live.py
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from passiton import parse, vision

from .conftest import SAMPLES, at, run

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call the model")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def photo(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


def test_identifies_the_calculator_and_its_sticker():
    found = run(vision.identify(photo("ti84.jpg"), "image/jpeg"))
    assert found.found and found.allowed and found.category == "calculator" and "84" in found.item_type
    assert "sticker" in found.fingerprint.lower()


def test_identifies_the_goggles():
    found = run(vision.identify(photo("goggles.jpg"), "image/jpeg"))
    assert found.category == "lab_gear" and "goggle" in found.item_type.lower()


def test_check_in_photo_matches_the_same_calculator_only():
    marks = "yellow smiley-face sticker bottom-right; small scratch top-left of the screen bezel"
    same = run(vision.compare(photo("ti84.jpg"), "image/jpeg", photo("ti84-checkin.jpg"), "image/jpeg", marks))
    other = run(vision.compare(photo("ti84.jpg"), "image/jpeg", photo("ti84-other.jpg"), "image/jpeg", marks))
    assert same.match and not other.match


def test_reads_a_borrow_text():
    need = run(parse.read("need a TI-84 for my stats exam Thu 2pm in Durham Hall", at(8).date()))
    assert need.building == "LL" and need.window_start == at(14) and "84" in need.item_type
