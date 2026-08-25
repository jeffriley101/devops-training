# Woodshed Woodchuck

Woodshed Woodchuck is a full-stack music-practice application for student musicians.

The application combines practice tracking, persistent accounts, challenges, contests,
rewards, practice verification, and interactive music tools in a responsive experience
for desktop and mobile devices.

## Technology

Woodshed Woodchuck is built with:

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Jinja2
- JavaScript
- HTML / CSS
- Git / GitHub
- Render

The current application uses server-backed persistence rather than relying on browser
storage as its primary state system.

## Current capabilities

The application includes:

- persistent Woodchuck accounts and profiles
- cross-device application state
- practice logging and practice-history workflows
- practice verification
- student and trusted-adult workflows
- seasonal activities and contests
- standings, rewards, crowns, and progression
- persistent inventory and owned-item state
- interactive music-practice tools
- responsive desktop and mobile interfaces
- server-side email workflows
- administrative support
- automated testing across core application behavior

## Main application areas

### SHED

The SHED is the Woodchuck's personal space.

Students can interact with practice tools, view and arrange owned items, and customize
their environment as they progress.

### BOOK

The BOOK contains practice-related workflows, including recording and reviewing practice
activity and sharing practice information for verification when appropriate.

### BOARD

The BOARD contains seasonal activities, contests, standings, and progression features.

Activity and contest state is stored server-side.

### SHOP

The SHOP allows students to use earned in-app currency for available items and gear.

Purchases and inventory are associated with the student's persistent account.

## Accounts and persistence

Woodshed Woodchuck began as a browser-based prototype but has since moved to a
server-backed architecture.

Current persistence includes:

- user profiles
- authenticated sessions
- cross-device state
- inventory
- practice records
- progression
- contest participation and results
- rewards

PostgreSQL is the primary persistence layer.

SQLAlchemy is used for application data access and models, while Alembic manages
database schema migrations.

## Practice verification

Woodshed Woodchuck supports practice-verification workflows involving trusted adults.

The public documentation describes these capabilities at a high level while intentionally
omitting internal verification and administrative implementation details.

## Contests and progression

The application includes persistent seasonal competition and progression systems.

Current capabilities include:

- seasonal activities
- multiple contest categories
- standings
- rewards and crowns
- persistent progression
- historical results
- administrative support for contest operations

Exact scoring, reward-control, and internal administrative logic are intentionally not
documented publicly.

## Testing

The project includes automated tests covering major application areas, including:

- accounts
- state synchronization
- practice workflows
- contests
- standings
- rewards
- email behavior
- account deletion
- regression-sensitive application behavior

## Local development

From the repository root:

```bash
cd woodshed-woodchuck
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## Deployment

The application is deployed on Render using FastAPI, Uvicorn, and PostgreSQL.

A typical application startup command is:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Production deployment also depends on the configured PostgreSQL environment and current
database migrations.

## Project status

Woodshed Woodchuck is an actively developed application.

It has grown substantially beyond its original browser-only prototype and now includes
persistent accounts, PostgreSQL-backed state, database migrations, verification
workflows, seasonal competition, rewards, interactive tools, and production deployment.

This README intentionally describes the public architecture and capabilities without
documenting proprietary gameplay logic, administrative internals, or implementation
details that do not need to be public.
