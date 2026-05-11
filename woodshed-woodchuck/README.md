# Woodshed Woodchuck (Phase 2)

Woodshed Woodchuck is a music-practice companion demo project.

Phase 2 includes:
- instrument-based daily quest pool
- local-date quest selection
- practice logging on Quest page
- once-per-day quest completion
- credits + streak updates when target minutes are met
- supportive Sax Viking messaging for below-target logs
- home-page quest status reflection

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

## Quest and streak rules (Phase 2)

- Quest is selected from the instrument pool per local calendar day.
- Logs with minutes below target are saved and receive supportive feedback.
- Quest completion requires cumulative daily logs meeting `targetMinutes`.
- Credits and streak update only on first completed quest of the day.
- Completion is limited to once per local day.

## Notes

- Persistence uses browser localStorage only.
- Store purchase/equip logic is intentionally deferred to Phase 3.
