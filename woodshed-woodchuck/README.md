# Woodshed Woodchuck (Phase 2)

Woodshed Woodchuck is a music-practice companion demo project.

Phase 2 includes:

- instrument-based daily quest pool
- local-date quest selection
- practice logging on the Quest page
- partial practice logs that accumulate toward the daily target
- once-per-day quest completion
- credits + streak updates when target minutes are met
- supportive Sax Viking messaging for below-target logs
- Home page reflection for quest status, credits, and streak
- browser localStorage migration from Phase 1 state

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

## Quest and streak rules

- Quest is selected from the musician's instrument pool per local calendar day.
- Logs with minutes below target are saved and receive supportive feedback.
- Quest completion requires cumulative daily logs meeting `targetMinutes`.
- Credits and streak update only on the first completed quest of the day.
- Extra same-day practice logs are allowed, but do not award duplicate credits.
- Completion is limited to once per local day.

## Visual asset hooks

The UI uses these project art assets:

- `/static/img/woodchuck-hero.png`
- `/static/img/woodchuck-home.png`
- `/static/img/sax-viking-portrait.png`

## Notes

- Persistence uses browser localStorage only.
- Backend database, auth, store purchases, account logic, and payment logic are intentionally deferred.
- Store purchase/equip logic is a future phase.
