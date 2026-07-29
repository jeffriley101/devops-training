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
