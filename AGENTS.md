# AGENTS.md: "Pass It On" (Cubbie Hackathon, Track 2)

This file is the full brief for the coding agent working in this folder. Read it all before writing code.
If `Build a Cubbie Skill — Hackathon Guide.pdf` is here, it covers **Track 1 only**; skim it for context about Cubbie.

---

## 1. The hackathon

- Event: Cubbie Hackathon (hackathon.cubbie.ai), run alongside DevHacks S3. Cubbie is an AI assistant that lives in
  students' iMessage.
- Team: **Enrollment Goblins**, Shrey Bishnoi (GitHub `shrey-Bish`) and Sumanth Akella.
- Track 1 (submitted): **Seat Snatcher**, https://github.com/shrey-Bish/seat-snatcher.
- Track 3 (being built in parallel by Shrey): **"Did anyone else?"**, a class group-chat bot that pools identical
  grading deductions into one request to the TA. Don't overlap with it.
- **This folder is Track 2.** Exact deadline: confirm in the hackathon Discord (#announcements). It was extended to
  "tomorrow" (Sunday Oct 25, 2026); treat it as tight.

### Track 2 brief (verbatim from the site)

> **Agent-to-Agent Coordination.** Build personal AI agents for campus life that work with each other. Agents
> representing different students exchange the information that matters and work toward a shared outcome —
> scheduling a study session, splitting a trip, planning around classes and events.
>
> How it works: Open only to teams that submitted Track 1. Doesn't need to be built on Cubbie. Any messaging app:
> iMessage, Slack, WhatsApp, Discord, or another. Any open-source framework.
>
> What to submit: A working prototype tied to a real campus scenario. Multi-agent? Show how agents handle
> conflicting constraints, what they share, and when they ask their students to confirm. A clear reason students
> would say "We need this".
>
> Judging: Judged privately on what you show us. Full criteria are announced before kickoff.

From the organizers on Discord: a standalone app is fine, as long as it's tied to a real campus scenario. Cubbie has
**no public API** for agent-to-agent messaging, so we build it ourselves. Submit a **GitHub repo**; a website and a
demo video (any video link) are optional but help judges.
**Humans submit the form. The coding agent never submits anything, posts publicly, or pushes without asking.**

### What else is out there (checked Oct 24, 10 PM)

- The three Track 2 entries all have agents privately negotiate *preferences*: *down?* (the plan everyone's actually
  down for), *Spring Sync* (pick a section with a friend, with a privacy gate), and *Group Project Prenup* (roles and
  meeting times). We must feel different: **our agents move a real physical object through the real world.**
- Organizer example ideas to avoid resembling: Study Session Sync, Trip Split, Around Class (club social planning).
- Student lending apps exist (e.g. *UniLend*, Cal Hacks 11.0: a list-and-borrow site, no agents, no prize), and so do
  "libraries of things." **Nobody gives the item its own agent, routes it around real exam schedules, or confirms
  handoffs with a scan or photo.** That's our pitch.
- We avoided seat swapping on purpose: in 2025 the University of Washington put a hold on the student behind
  "HuskySwap" (a seat-swap app) over registration rules.

---

## 2. The product

**Name:** Pass It On  **Tagline:** "Your stuff has an agent now."

### The moment (every class group chat, every exam week)

> "anyone have a graphing calculator i can borrow for tomorrow 😭"

Meanwhile, dozens of calculators sit unused in backpacks. The same goes for a blazer for someone's first career-fair
interview, lab goggles, a camera for a class project, or a hand truck on move-in day.

### The three kinds of agents

1. **Owner's agent**: knows what the student owns and their rules: "never during my exams" (pulled from their
   calendar automatically), "back by Thursday", "friends of friends only", minimum borrower rating for valuable items.
2. **Borrower's agent**: knows when its student needs something, from a Canvas calendar feed or a text like "need a
   TI-84 for my stats exam Thu 2pm in Durham Hall". It can also offer proactively: "You have an exam Thursday and no
   calculator listed. Want me to find one?"
3. **Item agent**: one per shared item. It plans the item's route through the day, texts whoever is holding it, in
   character, and re-plans when something changes.

### End-to-end flow

1. **List an item.** The owner texts a photo. Claude vision identifies the item and its condition, and records
   distinguishing marks (a scratch, a sticker) for photo check-ins later. The owner sets rules or accepts defaults,
   and picks a personality (dramatic / chill / wholesome).
2. **Requests arrive.** Borrower agents send only a need window (start, end) and a building. Nothing else.
3. **The item agent plans the route.** Example: Maya keeps it until 11:45 → Jay for his 12:00 exam → Priya for her
   3:00 exam → back to Maya by 5:00. Handoffs go in gaps between holders' classes, at buildings on their way, with
   walking time accounted for. Each person sees only their own part and confirms.
4. **Handoff.** The giver's phone shows a one-time QR code, and the receiver scans it with their camera, which
   transfers custody. Fallback: a 4-digit code ("Tell Jay the code 4821"). Optional: a **photo check-in**, where the
   receiver snaps the item and Claude compares it with the listing photo. A mismatch is flagged to the owner.
   A printed sticker is optional, only for items that are lent constantly.
5. **The item texts the holder in character.** *"Hey, it's Maya's calculator 🧮. Priya needs me at Durham Hall at
   2:45. You're 4 minutes away after class. Mind dropping me off?"*
6. **Re-routing.** An exam moves, a holder runs late, or someone cancels: the item agent re-plans and asks only the
   affected people to confirm.
7. **Late returns get Duolingo-style nudges**: funny, escalating, never shaming, at most 3, then the owner is told.
   *"Day 2 in your backpack. I can hear your roommate's music. Please take me home."*
8. **Back home.** The owner gets a recap. Weekly: *"Your calculator went to 9 exams. 9 people didn't have to buy one."*
9. **Reliability rating** (Chess.com-style): on-time handoffs and returns raise it. New borrowers start with low-value
   items. Owners can require a minimum rating for valuable ones.
10. **Move-out mode** (environment + kindness): when the owner moves out or graduates, the item agent finds its
    **next owner** instead of its next borrower. *"Maya's moving out. I'd like to stay at ASU. Priya started CHM 113
    this month and needs a calculator. Want me?"* Unclaimed items are pointed to ASU's Ditch the Dumpster donation
    bins or Goodwill. Final text: *"Maya graduated. I live with Priya now 💛."*

### Conflicting constraints (show these; Track 2 asks for them)

| Conflict | How the agents resolve it |
|---|---|
| Two borrowers need it at overlapping times | The item can't be in two places: the agents look for a second lender of the same item type. If there's none, the first confirmed request wins, and the other is waitlisted and told privately |
| Owner's own exams and blocked times | Hard constraints, never broken |
| A handoff doesn't fit both holders' free gaps plus walking time | Shift it earlier or later, or route through a third person on the way. Affected people confirm |
| Return deadline vs. the last borrower's exam end | That borrower isn't added unless the return still fits |
| Trust: a valuable item, a new borrower | Rating thresholds per item value. Owner can override |
| An exam moves (calendar change) | Automatic re-plan. Only affected people are asked |
| Holder is late | Nudges, then re-route. Owner informed after 3 nudges |
| Move-out: several students want the item | A deterministic rule (soonest course need, then earliest request). Nobody's reason is ever revealed |

### What agents share (the privacy gate)

- Between agents, only allowlisted fields cross: `item_id`, `item_type`, `window_start`, `window_end`,
  `building_code`, `rating_bucket`, `confirm` flags. **No free text, no full schedules, no grades, no reasons.**
  `gate.py` enforces this in code and logs every block.
- The item's in-character texts are generated **per recipient, from structured state only**. They never include
  another student's details beyond a first name and a building.
- Handoffs happen only in public campus buildings, by default during the day. Never home addresses.

### When it asks students to confirm

1. The owner approves each borrower, or sets an auto-approve rule.
2. The borrower confirms the planned pickup and drop-off.
3. Both sides confirm the physical handoff (scan, code or photo).
4. Anyone affected by a re-route confirms the change.
5. In move-out mode, the new owner confirms.

---

## 3. Stack and layout

- **Python 3.11+**, **FastAPI** + Jinja templates (a phone-friendly web app, no install), `sqlite3`, `pytest`,
  `qrcode` for one-time handoff codes, `icalendar` for Canvas calendar feeds, `anthropic` SDK.
- **Texts:** Twilio SMS. A trial account can text numbers you verify, which is enough for the demo (check Twilio's
  current trial rules). Telegram bot is the fallback. Keep a `Messenger` interface so either works.
- **Calendars:** Canvas gives each student a calendar feed link (.ics) on its Calendar page. Import it to find exams
  (owner blocked times, borrower need windows). Google Calendar is optional.
- **Walking time:** `assets/buildings.json` has ASU Tempe building codes with coordinates taken from ASU's public
  campus map (label them approximate if unsure). Walking minutes = straight-line distance × 1.3 / walking speed.
  Google Maps is optional.
- **Claude** (env `ANTHROPIC_MODEL`, default `claude-sonnet-5-5`) does only these:
  - identify items from photos and compare check-in photos
  - turn borrow texts into structured requests
  - write in-character messages
  **The scheduler, gate, ratings and state machine are deterministic and LLM-free.**
- Auth: magic links by text. No passwords.
- Secrets in `.env` (gitignored): `ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`,
  optional `TELEGRAM_BOT_TOKEN`. Ship `.env.example`. Never print or commit secrets, and never ask the humans to
  paste secrets into chat.

```
pass-it-on/
  README.md            judge-facing (see section 6)
  AGENTS.md / CLAUDE.md
  .env.example  requirements.txt  .gitignore
  passiton/
    app.py           FastAPI routes: item pages, handoff scan, confirm, demo page
    agents.py        owner / borrower / item agent logic (structured messages only)
    gate.py          allowlist between agents; logs blocks
    scheduler.py     routes over time windows + walking time; re-planning
    handoff.py       one-time QR tokens, 4-digit codes, photo check-in
    vision.py        Claude: identify item, compare check-in photo
    parse.py         Claude: borrow text -> Request
    voice.py         Claude: in-character texts from structured state; nudge limits
    calendar_feed.py Canvas .ics import -> exams / blocked times
    rating.py        reliability rating
    moveout.py       next-owner matching + donation fallback
    messenger.py     Twilio / Telegram adapters
    store.py         SQLite; /forget; retention
    demo.py          seeded demo world (Maya, Jay, Priya, Dev) + scripted events
  assets/buildings.json  assets/samples/ (synthetic item photos, sample .ics files)
  templates/  static/
  tests/
  docs/architecture.svg  docs/demo-script.md
  site/index.html
```

## 4. Data model

```python
class Item(BaseModel):
    id: str; owner_id: str; type: str            # "TI-84 Plus CE"
    value_tier: Literal["low", "mid", "high"]
    fingerprint: str                              # distinguishing marks from the listing photo
    persona: Literal["dramatic", "chill", "wholesome"]
    rules: dict                                   # return_by, audience, min_rating, auto_approve
    mode: Literal["lend", "give_away"] = "lend"

class Request(BaseModel):
    id: str; borrower_id: str; item_type: str
    window_start: datetime; window_end: datetime; building: str
    status: Literal["pending", "planned", "confirmed", "active", "done", "waitlisted", "cancelled"]

class Leg(BaseModel):                             # one stop on the item's route
    item_id: str; holder_id: str
    start: datetime; end: datetime
    pickup_building: str; pickup_time: datetime

class Handoff(BaseModel):
    leg_id: str; method: Literal["qr", "code", "photo"]
    token: str; confirmed_by_giver: bool; confirmed_by_receiver: bool
    photo_match: Literal["match", "mismatch", "skipped"]
```

## 5. Demo, README, site, video

- **`/demo` page**: three phones side by side (Maya the owner, Jay, Priya), each an iframe of the real app, logged in
  as a demo user. Buttons: *Move Jay's exam +1 h*, *Jay is running late*, *Maya moves out*. A judge can run the whole
  story alone in under 2 minutes.
- **Live physical demo (video)**: a real calculator passes between Shrey, Sumanth and a friend, with phone-QR scans
  and one photo check-in. Partway through, an exam moves and the item re-routes itself. End on: *"Your calculator went
  to 9 exams. 9 people didn't have to buy one."* Then the move-out scene: *"Maya graduated. I live with Priya now 💛."*
- **README**: the moment → the idea (items have agents) → try it (`/demo` link) → architecture diagram → a "What Track
  2 asks for / where it is" table (conflicting constraints, what's shared, when it confirms, why students need it) →
  guardrails → tests → "Built for Cubbie": when Cubbie supports agent-to-agent, each student's Cubbie becomes their
  agent and items join the conversation.
- **Site**: one page with the same story, the demo link and the video.
- **Video**: 45–60 s, student voice, starts on a real-looking "anyone have a calculator?? 😭" message.

## 6. Guardrails (non-negotiable)

- No real student data in the repo: synthetic photos, names, schedules.
- No money, deposits or payments. Lending and giving only.
- Physical items only. No notes, answers or course materials (academic integrity), and no medicine, alcohol, IDs,
  keys or anything unsafe. The borrower checks whether a calculator model is allowed in their exam; the agent reminds
  them.
- Handoffs in public campus buildings only. No home addresses.
- Nudges are funny, never shaming, at most 3. No public call-outs.
- `/forget` deletes a student's data.

## 7. Build order (time-boxed)

| # | Milestone | Done when |
|---|---|---|
| M0 | Scaffold, venv, `.env.example`, FastAPI up, test text to a verified phone | A real text arrives |
| M1 | List item: photo → item card + fingerprint; owner rules; Canvas .ics import for blocked times | Sample photo + sample .ics → correct item and blocked times |
| M2 | Borrow requests + `scheduler.py` + `gate.py` + plan confirmations + tests | 3 requests → valid route that respects walking time and owner blocks; tests prove only allowlisted fields cross |
| M3 | Handoffs: phone QR one-time token, 4-digit fallback, photo check-in | Scanning on a real phone transfers custody; mismatched photo is flagged |
| M4 | In-character texts, re-route on change, late nudges, rating | "Exam +1 h" re-plans and texts only affected people |
| M5 | Move-out mode + weekly recap | Give-away finds next owner; unclaimed → donation info |
| M6 | `/demo` page, README, architecture.svg, site, demo script | Judge-ready repo |
| M7 | Full test pass, record physical demo, humans submit | Submitted |

Stretch, only after M7: leagues and kindness streaks, AirTag/Find My for valuable items, Google Calendar, printed
"scan to borrow me" stickers.

## 8. Scoring we're aiming at (our own rubric)

Need 25% · Track fit 25% · "How did we not think of this" 20% · Real integrations 15% · Buildable by tomorrow 15%.
Current estimate ≈9.4; target 9.5. What gets us there:

1. **Proof of need**: real (blurred) screenshots of "anyone have a calculator?" posts from class chats, plus a quick
   survey of 15–20 ASU students. Put the numbers in the README. *(Humans do this.)*
2. **The physical moment**: the real handoff on video, with the re-route.
3. **The item's personality**: the shareable moment.
4. **The privacy gate in code with tests**, including an agent trying to learn another student's full schedule.
5. **Judges can try it alone** at `/demo`.
6. **Move-out mode** for the environment and emotion ending.
