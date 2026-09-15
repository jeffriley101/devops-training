# Advance ContestWeek provisioning

`provision_weeks` is an explicit calendar job. It defaults to a database-enforced
read-only plan; `--apply` inserts only missing weeks in one transaction. No
migration is required: it uses the existing schema at `s9n0o1p2q3r4` and the
`(season_id, week_start)` unique constraint. It neither creates/enables Seasons
nor changes Teams, deadlines, results, rewards, snapshots, or other history.

## Plan, apply, verify

Set `DATABASE_URL` explicitly to the intended database in the process environment.
Provisioning and Team jobs also accept `--database-url`; they have no local DB
fallback. The finalizer uses `DATABASE_URL`, so keep it set for every command.

For Back to School → Halloween, provision only the source final week (September
21–28) and destination first week (September 28–October 5):

```bash
python -m app.contest_jobs provision_weeks \
  --source-season back-to-school-2026 --destination-season halloween-2026

python -m app.contest_jobs provision_weeks \
  --source-season back-to-school-2026 --destination-season halloween-2026 --apply

python -m app.contest_jobs provision_weeks \
  --source-season back-to-school-2026 --destination-season halloween-2026
```

Alternatively, prepare all weeks in explicitly named canonical seasons:

```bash
python -m app.contest_jobs provision_weeks \
  --season back-to-school-2026 --season halloween-2026
python -m app.contest_jobs provision_weeks \
  --season back-to-school-2026 --season halloween-2026 --apply
```

These are alternative scopes; do not combine the season and transition flags.
A transition must be canonically adjacent. Whole-season preparation requires
an approved end date. A transition into open-ended Band Camp 2027 can prepare
its first seven-day week without inventing a season end or later weeks.

Check the JSON `scope`, `weeks`, `conflicts`, and `activation_prerequisites`.
`READY` concerns calendar provisioning only. Missing Season records, canonical
metadata mismatches, overlapping Season coverage, wrong week ownership, malformed
boundaries, and overlapping week rows cause `BLOCKED` and nonzero exit with no
inserts. Provisioning requires existing canonical Season records; resolve missing
records with the separately reviewed season bootstrap workflow. Never repurpose
incident calendar repair or rollover to force this transition.

A matching week means the same owner and exact date interval. Its stored
deadlines, status, timestamps, and history remain unchanged, including finalized
rows. `stored_deadlines_differ` highlights custom deadlines without rewriting
them. The bounded transition inspects its two intervals, including overlapping
rows owned by other seasons; it does not repair or certify other historical
weeks. Frozen destination history can still block Team activation.

After apply, expect `APPLIED` and the committed `created` count; repeat plan
must show zero `missing`, zero conflicts, and all weeks `unchanged`. Repeated
apply returns `ALREADY_COMPLETE` with zero creates. Retain job output externally
as operational evidence; no audit records are added to the database.

## Required lifecycle order and timezones

Run prospective Team preflight after calendar provisioning:

```bash
python -m app.contest_jobs team_preflight \
  --source-season back-to-school-2026 --destination-season halloween-2026
```

Preflight remains read-only. Resolve every REVIEW/CONFLICT and other blocker.
Source must be enabled or closed and destination enabled (`active`). READY is
prospective and does not authorize a later changed roster. Source-final-week
presence, stored deadlines, an open destination first week, and all existing
frozen-history checks still apply.

At or after **2026-09-28 00:00 America/Chicago (05:00 UTC)**:

```bash
python -m app.contest_jobs team_activate \
  --source-season back-to-school-2026 --destination-season halloween-2026
```

Verify the activation report's verification result and zero remaining creates;
repeat this explicit activation command for `ALREADY_COMPLETE`. Keep the
[activation runbook's boundary writer/user pause](season-team-activation.md)
until verification passes. Activation before the boundary remains `NOT_DUE`.
There is no CLI clock override.

Later, after the source's stored deadlines:

```bash
python -m app.contest_jobs finalize_due_weeks
```

This finalizes **all due weeks**, so inspect the intended database's due-week
state before running it. Default source deadlines are September 28 at 12:00 and
12:05 Central (17:00 and 17:05 UTC); both must be strictly passed. Existing custom
deadlines take precedence. If activation is delayed until source finalization is
due, finalize normally first, then rerun preflight/activation. Destination frozen
history still requires operator review; provisioning never clears it.

Canonical dates come from `app/seasons.py`; scheduling comes from
`contest_week_schedule`: Monday-to-Monday half-open dates, verification at the
ending Monday's noon Central, finalization five minutes later. ZoneInfo computes
UTC offsets for each deadline. For example, the Halloween final week crosses
the November 1 DST change and its November 2 noon deadline is 18:00 UTC. Do not
calculate deadlines by adding fixed UTC weeks.

## Transactions, retries, and scheduling

Apply replans from current state after sorted shared Season locks. SQLite uses
`BEGIN IMMEDIATE`; PostgreSQL uses READ COMMITTED and a short
`SHARE ROW EXCLUSIVE` lock on `contest_weeks` to serialize interval checks with
legacy/lazy writers too. This can briefly delay unrelated calendar/finalization
writes. The unique constraint provides an additional duplicate safeguard.
All inserts and post-insert validation commit together. Conflicts, lock errors,
constraint failures, or verification failures roll back the operation. Retry
the same bounded command after transient contention; persistent conflicts need
review, never deadline/history edits. A previously printed plan is informational;
apply always replans and does not accept plan JSON as authority.

No scheduler is installed or activated by this code. An operator still needs to
review/release the change, verify the intended database and canonical Season
records, run plan/apply/preflight in advance, arrange boundary activation and its
traffic pause, and configure monitored retries plus independent due-week
finalization. A noon-only finalizer schedule cannot perform midnight activation.
Monitor nonzero exits and structured results; confirm successful execution rather
than merely the existence of a schedule.

Validation for this change uses disposable SQLite and loopback PostgreSQL
fixtures, including the full provisioning → preflight → activation → later
source-finalization sequence. Local test readiness does **not** mean production
weeks are provisioned, a schedule exists, or production preflight is clean.
No production access or scheduler changes are part of this implementation.
