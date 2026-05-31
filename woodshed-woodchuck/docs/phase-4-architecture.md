# Woodshed Woodchuck Phase 4 — Architecture Doc Creation Step

Purpose: create the Phase 4 architecture document before coding.

Current branch should be:

    ww-phase-4-woodshed-core

Project folder:

    ~/Training_scripts/woodshed-woodchuck

## Step 1 — Go to the project folder

Run:

    cd ~/Training_scripts/woodshed-woodchuck

## Step 2 — Create the docs folder

Run:

    mkdir -p docs

## Step 3 — Open the Phase 4 architecture doc in Vim

Run:

    vim docs/phase-4-architecture.md

## Step 4 — Paste this Markdown into Vim

Use insert mode, paste the content below, then save and quit.

Vim reminder:

    i
    paste content
    Esc
    :wq

Paste this content into the file:

# Woodshed Woodchuck Phase 4 Architecture

## Phase 4 goal

Make The Woodshed real for the Summer Beta.

Woodshed Woodchuck should become a supportive music-practice companion for young musicians: a Finch-like app for musicians where real music time becomes P-Book pages, credits, badges, encouragement, and useful practice records.

Core loop:

Music time -> P-Book page -> credits -> badge/reward -> encouragement -> return later.

## Product focus

Phase 4 focuses on the product core:

1. P-Book as source of truth
2. Credit economy
3. Practice timer
4. Weekly rhythm rewards
5. Basic badges
6. Summertime Practice beta event
7. Export improvements
8. Parent/teacher trust
9. Artist handoff preparation

Phase 4 should stay localStorage-only.

Do not add backend, accounts, database, payment system, teacher dashboard, microphone tuner, or paid membership implementation in Phase 4.

## P-Book source of truth

PracticeLog should be the source of truth for:

- practice history
- P-Book summary cards
- total minutes
- practice days
- pages in the book
- credits earned from P-Book activity
- weekly rhythm rewards
- badges
- export reports
- Summertime Practice progress

Avoid duplicating derived totals in state unless needed for display or compatibility.

## Practice entry model

Phase 4 entries should move toward this shape:

    {
      id,
      dateKey,
      createdAt,
      minutes,
      instrument,
      activityType,
      note,
      source,
      creditsAwarded
    }

Where activityType is:

    "playing" | "thinking"

Playing time includes home practice, school band, lessons, rehearsals, concerts, tests, performances, busking, warmups, scales, songs, and any time the student plays.

Thinking time means thinking about music with The Viking Sax: listening, reflecting, composing, planning practice, studying music, or mental practice.

All instruments contribute to one shared Woodchuck bank.

## Credit rules

Initial Phase 4 rules:

- Playing time: 1 credit per 5 minutes
- Thinking time: 1 credit per 10 minutes
- Each P-Book submission: +5 credits
- Regular P-Book credits are capped at 50 credits per 24-hour period
- Weekly rhythm rewards:
  - 4 practice days in a week: +25 credits
  - 7 practice days in a week: +50 credits and badge
- Badges can award bonus credits
- Store spends credits but does not calculate credits

Credit logic should live in helper functions, not scattered across button handlers.

Possible helper names:

    calculateEntryCredits(entry)
    getCreditsEarnedInLast24Hours(practiceLog, now)
    applyDailyCreditCap(rawCredits, practiceLog, now)
    getPracticeDaysThisWeek(practiceLog)
    checkAchievements(state)

## P-Book Phase 4 UI

Add:

- summary cards:
  - total minutes
  - practice days
  - pages in book
  - credits earned from P-Book
- activity type selector:
  - Playing Time
  - Thinking About Music with The Viking Sax
- instrument field or current profile instrument default
- practice timer:
  - Start timer
  - Stop timer
  - Confirm adding elapsed time to P-Book
- reward feedback after logging
- "Any time you play counts" copy

Manual entry should remain available.

## Export

Phase 4 export should support:

- all entries
- this week
- this month

Export format should be clear for a band director.

Basic required fields:

- student / Woodchuck name
- instrument
- date range
- total minutes
- entries

Notes may be included when present.

PDF export is parked for later.

## Weekly rhythm

Use weekly rhythm language instead of guilt-heavy daily streak language.

Preferred UI language:

- This Week's Rhythm
- Practice Days This Week
- Strong Music Week
- Full Music Week

Existing streak state can remain temporarily for compatibility, but new UI should focus on weekly rhythm.

## Badges

Start small.

Initial badge candidates:

- First Page in the Book
- 3 Practice Days
- 4-Day Music Week
- Full 7-Day Music Week
- 100 Minutes Logged
- First Viking Sax Reflection

Badges should be derived from P-Book behavior where possible.

## Summertime Practice beta event

Create one simple hardcoded event for beta:

    {
      id: "summer-practice-2026",
      title: "Summertime Practice",
      description: "Any time you play counts. Keep your sound alive over summer break."
    }

Do not build a full event admin system yet.

The event should support:

- event card
- summer practice message
- possible badge/reward hooks

## Parent and teacher trust

Because the app is intended for young musicians, keep the beta local-first and low-risk.

Beta trust posture:

- no account required
- use a nickname for the Woodchuck
- practice book is stored on this device
- export opens the user's own copy/email tools
- no child data collection backend in Phase 4
- external support/payment links should be clearly adult-supervised

Add parent/teacher copy in README and possibly app UI.

## Parked for later

Do not build these in Phase 4:

- backend persistence
- login/accounts
- teacher dashboard
- paid membership implementation
- real microphone tuner
- PDF export
- full store expansion
- outfit image overlays
- full event admin system
- analytics

## Phase 4 build order

1. Add this architecture doc
2. Add P-Book summary cards
3. Add central credit helper/rules
4. Add activity type and updated P-Book credit calculation
5. Add 24-hour credit cap
6. Add timer with confirm-to-log
7. Add weekly rhythm and small badges
8. Add export range options
9. Add Summertime Practice beta card
10. Add artist handoff doc
11. Update README

## Step 5 — Confirm Git sees the file

After saving the file, run:

    git status --short

Expected output should include something like:

    ?? docs/

Paste the output back into ChatGPT.
