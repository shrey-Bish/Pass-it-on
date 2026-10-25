# Pass It On

**Your stuff has an agent now.** ASU students lend things like graphing calculators and lab goggles to classmates.
Each item gets its own agent: it plans its route through the day around everyone's exams, texts whoever is holding it
in character, and confirms every handoff with a one-time QR code.

Cubbie Hackathon, Track 2 (Agent-to-Agent Coordination). Built by **Enrollment Goblins**: Shrey Bishnoi and Sumanth
Akella.

<a href="https://drive.google.com/file/d/1udpUBQXdrY9GO4o798WYyoYqQGYMYvMZ/view"><img src="docs/img/video-poster.jpg" width="270" alt="Watch the 1-minute video"></a>

▶ **[Watch the 1-minute video](https://drive.google.com/file/d/1udpUBQXdrY9GO4o798WYyoYqQGYMYvMZ/view)** (Google Drive)

## What works today

- **List an item from a photo.** The AI identifies the item, its condition and its marks (a sticker, a scratch), and
  plain code refuses notes, keys, IDs and medicine.
- **Canvas calendar import (.ics).** Exams become the owner's blocked times and the borrower's need windows.
- **Borrow by text.** "need a TI-84 for my stats exam Thu 2pm in Durham Hall" becomes a time window and a building.
- **The route planner** (plain code, no AI). It respects walking time between ASU buildings, everyone's classes, the
  owner's own exams and the return time, and it waitlists overlapping requests, first come, first served.
- **The privacy gate.** Only `item_id`, `item_type`, `window_start`, `window_end`, `building_code`, `rating_bucket` and
  `confirm` cross between agents. Every block is logged, without values.
- **Handoffs.** A one-time QR code, a 4-digit fallback, and an optional photo check-in that flags a mismatch to the owner.
- **The rest of the day.** In-character texts, re-planning when an exam moves (only the affected people hear about
  it), at most 3 playful nudges, reliability ratings, move-out mode with a next owner or donation info, and a weekly
  recap.
- **`/demo`:** three phones (Maya, Jay, Priya) running the real app, with buttons to move an exam, make someone late, or
  move out.

## Run it

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill it in; never commit it
.venv/bin/uvicorn passiton.app:app --host 0.0.0.0 --port 8000
```

Then open http://localhost:8000/demo. Send a test text with `.venv/bin/python -m passiton.messenger test`.

## Tests

```
.venv/bin/pytest                         # offline: planner, gate, handoffs, agents, voice, move-out, pages
RUN_LIVE=1 .venv/bin/pytest tests/test_live.py    # calls the real model on the sample photos
```

Everything in `assets/samples/` is made up: the item photos are drawn, and the calendars belong to fictional students.
