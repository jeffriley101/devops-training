# H2A: read-only team continuity inventory

H2A inspects the Band Camp 2026 → Back to School 2026 incident. It creates a
diagnostic report only. It does not authorize or perform repair, migrate a
database, invoke the H1B engine, or change P-Chart attribution.

The tool supports production **before H1A**: it reflects the target schema and
selects explicitly allowlisted columns using SQLAlchemy Core. It imports neither
the application's ORM models nor its database configuration. No TeamFamily
table or family_id column is required. It can run from this reviewed checkout
without deploying H1A/H1B or upgrading the target database.

## Invocation

Use an explicitly supplied DATABASE_URL in the operator's environment. There is
no default local database fallback and no .env loading. Obtain credentials using
the normal secure operational process; a database role restricted to SELECT is
recommended. Avoid typing passwords into shared shell history.

From the Woodshed Woodchuck repository:

```sh
.venv/bin/python -m app.team_continuity_inventory --help
.venv/bin/python -m app.team_continuity_inventory --output /secure/path/new-team-inventory.json
```

The second command requires DATABASE_URL to already be set deliberately. It
prints the sanitized target before inspection. --database-url is also supported,
but environment configuration avoids exposing a password in process arguments.

PostgreSQL URLs may use postgres://, postgresql://, or postgresql+psycopg://.
The host and database must be explicit. Standard TLS parameters, options
(including search_path), application_name, connect_timeout, channel_binding and
gssencmode are supported; query parameters cannot override target/credentials.

For an existing isolated SQLite file:

```sh
.venv/bin/python -m app.team_continuity_inventory --database-url sqlite:////absolute/path/test.db --output /secure/path/new-test-report.json
```

Default --format both sends the human summary to stderr and JSON to stdout
unless --output is supplied. --format json or --format summary can limit the
display. An explicit --output always writes JSON. Output files are created with
exclusive creation and mode 0600: existing files, including the SQLite database,
cannot be overwritten. No report file is written without an explicit path.

There is **no apply, repair, or write option**. Exit 0 means the inventory
completed, even if it found conflicts. Exit 1 means inspection/output failed.
Exit 2 means invalid CLI arguments. Neither successful execution nor
SAFE_CANDIDATE authorizes repair.

## Read-only enforcement

PostgreSQL uses Psycopg transaction characteristics set before handing the
connection to SQLAlchemy: READ ONLY and REPEATABLE READ. The command verifies
transaction_read_only and transaction_isolation before introspection and
inventory. SQLite opens an existing file with URI mode=ro, enables query_only,
and starts a read snapshot. Missing files are not created. Unsupported modes or
failed read-only verification abort; no ordinary read/write fallback exists.
Connections are rolled back and closed on both success and failure.

All inventory queries are fixed Core SELECTs or schema introspection. There are
no row locks, ORM sessions/autoflush, DML, DDL, temporary tables, or repair calls.
Normal database/server logging and read locks can still occur; no application
data is changed. Prefer a quiet operational window to keep the inventory short.
H2A does not need H1B's maintenance writer pause because it performs no repair.

## What the report contains

- Sanitized connection identity, inventory UTC timestamp, local git commit,
  database Alembic revisions, and table/column availability.
- Source/destination dates, statuses and timezone; date-covered season checks,
  destination/source weeks and counts of finalized artifacts.
- Source and destination Teams, counts by type, family IDs when available,
  membership/reference counts, moderation state and join-code presence only.
- All source memberships, exact boundary flags, overlapping roster anomalies,
  destination active **and ended** memberships, requests and public creation.
- Diagnostic name/emblem/creator similarities, possible manual successors,
  same-family checks on H1A schemas, and integrity conflicts.
- Director eligibility, private rosters, pending requests, director contests
  touching the boundary, entries and result IDs.
- Destination charts/awards with null, matching, missing, or cross-season Team
  references; late-submitted source charts and include_team_contests when known.
- Weekly results/snapshots, linked reward grants and crown awards, and director
  results. No scores, snapshots or attribution are recomputed or changed.

The boundary is 2026-09-14 00:00 America/Chicago, **05:00 UTC**. Confirmed boundary
members start strictly before it and are unended or end strictly after it.
Exact-boundary endings are separately counted and require REVIEW; they are not
counted as confirmed continuing members. Naive stored timestamps follow the
application's UTC convention. Practice dates are local calendar dates; award and
submission timestamps are converted from UTC. The current date/week is not used
to substitute a different incident.

Team counts named pending_requests and unresolved_reports have those specific
filters; reference counts cover all rows pointing to the Team, including
historical references outside this date window. Per-student destination chart
and award IDs resolve to the safe metadata in the attribution sections.

## Classification and limitations

SAFE_CANDIDATE means an observed source Team/member is a plausible continuation
candidate. It is always diagnostic, on either schema. Missing optional sections
or relevant columns suppress candidate conclusions: counts become null where
unavailable and the schema section explains what could not be checked. Required
pre-H1A tables/columns missing cause a clear failure. An absent season row is
reported as missing, not manufactured.

REVIEW includes moderation, owner/student eligibility, ambiguous boundaries or
overlaps, pending requests, director contests, missing attribution and possible
manual recreation. A possible manual successor is explicitly labeled
**POSSIBLE MANUAL SUCCESSOR — REVIEW REQUIRED**. Similar names, emblems and
creators do not establish lineage, including when H1A assigned separate families.

CONFLICT includes contradictory destination history, hard collisions,
inconsistent season/team references, contradictory same-family objects, and
frozen destination artifacts. INFORMATIONAL covers clean history that is not a
continuation candidate. Team classifications conservatively include member
issues; unlike H1B apply, this report does not choose an executable subset.

The report distinguishes pre_team_family and team_family_available. A partially
present family schema requires review. H1A's one-family-per-existing-Team
backfill is never treated as evidence that two separately created families match.

Summaries state observed counts, availability, classification totals, and a repair
assessment. Unknown optional dependencies are not proof that backdating is safe.
Frozen artifact counts are observed counts; check inventory_complete before
interpreting them as exhaustive. A snapshot cannot establish production history
before the stored data, resolve business conflicts, or authorize future repair.

Private join codes are reduced to booleans in SQL and never fetched as values.
Student names, Woodchuck IDs, PINs, email addresses, practice notes/details,
report free text, private contest titles/descriptions, and raw grant source keys
are not selected into the report. Operational IDs and Team names remain in the
report, so store it privately. URL passwords, query options and raw driver errors
are not printed. Retain the JSON for review; compare runs after accounting for
inventory_timestamp and local_git_commit.

H2B/H3 repair/activation tooling and H1C attribution changes are not implemented
by H2A. The command prominently states:

**READ-ONLY INVENTORY — NO REPAIR PERFORMED**
