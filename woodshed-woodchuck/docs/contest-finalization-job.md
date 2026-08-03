# Band Camp finalization job

Run the scheduler-safe finalization command from the application directory:

```bash
python -m app.contest_jobs finalize_due_weeks
```

## Environment

Set `DATABASE_URL` to the same persistent production database used by the web
service. No browser session or `CONTEST_ADMIN_TOKEN` is required for this
internal command. Run the normal Alembic migrations before enabling the job.

For a future Render Cron Job, schedule it for Monday shortly after the current
week's `finalize_after` timestamp (Band Camp uses America/Chicago boundaries).
Do not start this command as an in-process FastAPI background loop.

The command is safe to rerun manually with the same invocation. Finalized weeks
are skipped, due failed weeks are retried, and database uniqueness constraints
prevent duplicate results and rewards. Exit code `0` means every due week
finished successfully or none were due. A nonzero exit code means at least one
due week failed; review the structured logs and rerun after correcting the
cause.

Never run destructive database resets, table recreation, or data-clearing
commands as part of cron setup or recovery.

## Read-only history audit and narrow repair

Audit a specific week before considering any repair. The command is dry-run by
default and reports source-chart and artifact counts without committing changes:

```bash
python -m app.contest_jobs audit_history --week 2026-07-27
```

Only after reviewing that output, apply deterministic missing artifacts with:

```bash
python -m app.contest_jobs audit_history --week 2026-07-27 --apply
```

The apply form respects the original verification/finalization deadlines,
preserves existing result snapshots, and uses the existing unique reward keys.
It cannot make a not-yet-due week finalize. A complete finalized week is a
no-op. Run this only as a local management command with the same protected
database access as the scheduler; it is not exposed as a public route.

## Season readiness and rollover

While signed in, inspect the privacy-safe readiness payload at:

```text
GET /contests/seasons/status
```

Finalize every due week first, then confirm the season end date has passed and
the readiness payload has no blocking reasons. Rollover requires explicit dates;
the job never guesses them. The start must be a Monday, the inclusive end must
be a Sunday, and the range must contain complete weeks.

```bash
python -m app.contest_jobs rollover_season \
  --source-key band-camp-2026 \
  --next-key band-camp-2027 \
  --next-name "Band Camp 2027" \
  --start 2027-07-26 \
  --end 2027-08-08
```

`DATABASE_URL` is the only required deployment environment variable for the
CLI. A successful rollover (including a safe exact rerun) exits `0`. A blocked
or failed rollover exits nonzero and leaves the source season active with no
partial next season. Never manually reset standings or delete historical
seasons, weeks, results, rewards, Camp points, P-Charts, or crown progress.
