# Woodshed Woodchuck (Phase 1)

Woodshed Woodchuck is a music-practice companion demo project.

Phase 1 includes:
- app scaffold
- promo-art-aligned design tokens/components
- welcome screen
- setup flow
- localStorage state initialization
- navigation with setup gating

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

## Visual asset hooks (Phase 1)

The UI contains placeholder hooks ready for promo-art crops:
- `/static/img/woodchuck-hero.png`
- `/static/img/woodchuck-home.png`
- `/static/img/sax-viking-portrait.png`

## Notes

- Persistence currently uses browser localStorage only.
- Quest completion loop, reward economy logic, and store transactions are intentionally deferred to the next phase.
