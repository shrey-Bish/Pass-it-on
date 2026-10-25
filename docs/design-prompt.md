# Claude Design prompt: Pass It On mobile UI

Paste everything below the line into Claude Design.

---

Design the mobile web app for **Pass It On**, tagline **"Your stuff has an agent now."**

**What it is.** ASU students lend things like graphing calculators, lab goggles and blazers to classmates. Each item gets its own little agent. It plans its own route through the day around everyone's exams (Maya keeps it until 11:45, then Jay has it for his noon exam, then Priya for her 3:00 exam, then it's back with Maya by 5:00). It texts whoever is holding it in character, and each handoff is confirmed by scanning a QR code.

**Who uses it.** Students on their phones, often walking between classes, one-handed, glancing for 3 seconds. Every screen must make sense at first look.

**Feel.** Simple, clear, calm and good-looking. The UI is quiet and tidy. The fun comes from the item's texts and its personality picker, not from decoration. Use plain words everywhere: "Hand it over", "I got it", "Find one". Never "agent", "custody", "leg", "request status" or any other jargon.

**Constraints**
- Mobile web, no app install. Design at 390 × 844, and make sure it works at 360 px wide.
- It's built as server-rendered HTML with one CSS file (no React), so keep components simple: cards, buttons, pills, inputs, a timeline, chat bubbles.
- One accent colour. A light theme first (a dark theme is optional).
- Tap targets at least 44 px. Body text at least 16 px. Good contrast.
- A bottom tab bar with: Home, Lend, Borrow, Texts, Calendar.
- Current tokens, which you may refine: background #f6f4ef, card #ffffff, ink #1d1c19, muted #6c685f, line #e6e1d6, accent green #1f7a55 (green, as in "passed on"), soft accent #e2f0e8, warning #a8501a. Font: Inter.
- Show privacy quietly where it matters: "Classmates only see your first name", "Only the time and building are shared, never why".

**Screens (use this real content)**

1. **Welcome (signed out).** "Your stuff has an agent now." One sentence explaining it, a phone number field, the button "Text me a sign-in link", and a secondary "Try the demo".
2. **Home.** A "Waiting on you" section of action cards, then "Your stuff", then "Borrowing".
   - "Dev wants your TI-84 Plus CE" · Thu 12:30–1:20 PM · Wexler Hall · [Yes] [No]
   - "Get Maya's calculator" · 11:45 AM · Coor Hall · [Confirm] [I got it]
   - "Hand it to Priya" · 1:20 PM · Life Sciences A · [Confirm] [Hand it over]
   - "Want to keep this TI-84 Plus CE?" Its owner is moving out. · [Yes, it's mine] [No thanks]
   - "STP 226 exam Thu": You have an exam and no calculator listed. Want me to find one? · [Find one]
   - Item cards with a status pill: Lending / Not listed yet / Finding a new home.
   - Borrowing cards with status: Waiting for the owner / Planned, confirm your part / Confirmed / With you / On the waiting list.
3. **Lend.** "Take a photo" with the camera, then a loading state, "Looking at your photo…", which takes about 10 seconds.
4. **"Is this right?"** The photo, what it is ("TI-84 Plus CE"), its marks ("yellow smiley sticker bottom-right; small scratch top-left of the screen bezel"), condition and value. Then the rules: home by 5:00 PM, starts and ends at Memorial Union, who can borrow (anyone at ASU / friends of friends / friends), lowest reliability rating, and "Say yes for me when the rating fits". Then a **personality picker** with three cards (Dramatic / Chill / Wholesome), each showing a sample line in that voice. Make this the delightful screen.
5. **Item page.** The photo, who has it now, today's route as a vertical timeline (11:45 Maya → Jay · Coor Hall; 1:20 Jay → Priya · Life Sciences A; 4:20 Priya → Maya · Life Sciences A), "This week: 2 exams", and the buttons Edit / I'm moving out.
6. **Handoff.**
   - The giver's screen: a big QR code, the fallback code **4821** in large digits, and "1:20 PM at Life Sciences A. Ask Priya to scan this with their camera."
   - The receiver's screen: "Scan their QR with your camera, or type the code", plus a 4-digit input.
   - The success state, "It's with you", with an optional "Snap a check-in photo". Show both results: "Photo matches" and "That photo doesn't match the listing. I've let the owner know."
7. **Borrow.** One text box with the placeholder "need a TI-84 for my stats exam Thu 2pm in Durham Hall", the button "Find one", and the small notes "Only the time and building are shared, never why" and "Check your exam allows that calculator model."
8. **Texts.** Chat bubbles from the items, in character. Emoji are welcome here.
   - "It's Maya's calculator 🧮! Our story continues: meet Maya at Coor Hall at 11:45 AM."
   - "Nudge 1 of 3: I'm still with you, and Priya is waiting at Life Sciences A. Please rescue me!"
   - "I'm back home! I went to 2 exams today. 2 people didn't have to buy one."
   - "Maya graduated. I live with Priya now 💛"
9. **Calendar.** "Paste your Canvas calendar link" or upload an .ics file. A list of exams under the heading "your stuff stays with you during these". "Nobody else ever sees your calendar."
10. **Demo page (desktop, for judges).** Three phone frames side by side (Maya, Jay, Priya) running the app, a clock ("Thu Oct 29, 11:45 AM"), and the buttons: Skip to the next handoff · Move Jay's exam +1 h · Jay is running late · Maya moves out · Start over.

**States to show:** empty, loading (the AI is looking), a plain-words error, and success.

**UX, transitions and animation**
- **Fewest taps.** Each card has one obvious main action. Every flow takes three steps or fewer:
  - Lend: photo → "Is this right?" → listed.
  - Borrow: type what you need → found, or on the waiting list.
  - Handoff: open → scan → done.
  - After any action, say what happened in one line ("Confirmed", "It's with you") and return Home.
- **Smooth page changes.** The app loads a new page on each tap, so use CSS cross-document View Transitions (`@view-transition { navigation: auto; }`):
  - A gentle fade/slide between pages.
  - Shared elements morph between screens: the item photo from its card into the item page, and the tab bar indicator sliding between tabs.
  - Pure CSS. No JavaScript framework.
- **Motion that makes the item feel alive:**
  - While the AI looks at a photo: a soft scanning shimmer passes over the photo, with "Looking at your photo…".
  - The personality picker: the chosen card lifts slightly and its sample line types in.
  - Confirm: the button turns into a check, and the card settles into "Confirmed".
  - A successful handoff: a small "hop", where the item icon moves from the giver's name to the receiver's, then the page says "It's with you".
  - The route timeline: the dot for where the item is now gently pulses. Finished stops fade back.
  - Texts: new bubbles slide up and fade in, one after another.
  - Pills and status changes cross-fade their colour instead of jumping.
- **Rules for motion:**
  - 150–300 ms, ease-out, nothing bouncy or slow.
  - Never block a tap while something animates.
  - Everything respects `prefers-reduced-motion`, which turns animations into simple fades or none.
  - Show the motion as short notes or frames next to the screens, and include the CSS keyframes in the deliverable.

**Avoid:** dashboard looks, charts, heavy gradients, more than one accent colour, emoji in the UI chrome (they're fine inside the item's texts), tiny grey text, and jargon.

**Deliver**
- High-fidelity mobile screens for 1–9 and the demo page.
- A one-page component sheet (card, action card, buttons, pills, inputs, tab bar, QR block, code digits, timeline, chat bubble) with the tokens: colours, type scale, spacing, radius.
- HTML/CSS we can port into Jinja templates that share one CSS file, including the View Transition rules and keyframes.
