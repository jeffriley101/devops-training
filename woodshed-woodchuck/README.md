# Woodshed Woodchuck

Woodshed Woodchuck is a FastAPI music-practice companion game for young musicians.

The app helps students create a Woodchuck, log practice, complete flexible quests, earn credits, buy simple gear, and export practice records for a band director or parent.

Current features include:

- Woodchuck naming and profile setup
- band-instrument-focused setup flow
- Woodshed scene shell with clickable room objects
- P-Book / P-Chart practice logging
- optional practice notes
- copy/export P-Chart
- email P-Chart handoff through the user's mail app
- flexible Quest Book with choose/skip quest options
- instrument-specific Viking Sax advice
- credits, streaks, and local progress tracking
- active Store with buy/equip state for hats and hoodies
- browser localStorage persistence and migration from earlier state

## Local run

From repository root:

```bash
cd woodshed-woodchuck
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000`.

## Render startup shape

Use the standard Render/FastAPI startup command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Practice, quest, and store rules

### P-Book / P-Chart

- Students can log practice date, minutes, and an optional note.
- Each P-Book submission creates a new practice page.
- P-Book entries award practice credits.
- Recent practice pages display in the app.
- P-Chart can be copied for sharing.
- P-Chart can be sent through the user's email app with teacher and parent fields.
- Notes are included in the export when present.

### Quest Book

- Quest options come from the selected instrument pool.
- Students can choose another quest or skip to a different quest.
- Quest practice logs accumulate toward the active target.
- Quest completion requires cumulative daily logs meeting `targetMinutes`.
- Credits and streak update only on the first completed quest of the day.
- Extra same-day practice logs are allowed, but do not award duplicate quest credits.
- Completion is limited to once per local day.

### Store

- Students earn credits through practice.
- Store items can be purchased with credits.
- Duplicate purchases are blocked.
- Owned items can be equipped.
- Equipped hat/hoodie state persists in `localStorage`.
- Clothing state exists, but visual clothing overlays are deferred until proper art assets exist.

## Visual asset hooks

Current assets:

- `/static/img/woodchuck-hero.png`
- `/static/img/woodchuck-home.png`
- `/static/img/sax-viking-portrait.png`

Planned / desired future assets:

- `/static/img/woodshed-room-bg.png`
- `/static/img/woodchuck-base.png`
- `/static/img/woodchuck-saxophone.png`
- `/static/img/woodchuck-clarinet.png`
- `/static/img/woodchuck-flute.png`
- `/static/img/woodchuck-drums.png`
- `/static/img/woodchuck-tuba.png`

Future outfit assets may include hat and hoodie overlays, but exact implementation should wait for final art.

## Notes

- Persistence uses browser `localStorage` only.
- Refreshing or reopening the same browser should preserve state.
- Different browsers/devices start fresh unless future backend/account sync is added.
- Backend database, auth, account logic, payment logic, subscriptions, teacher dashboards, and server-sent email are intentionally deferred.
- Real tuner/metronome tools, achievements, printable/PDF reports, and visual clothing overlays are future phases.
