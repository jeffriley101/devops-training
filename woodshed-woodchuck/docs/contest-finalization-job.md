# Ordinary weekly contest operations

Prepare [approved calendar weeks](contest-week-provisioning.md) ahead. Use the
existing operational finalizer for weekly results; supervise
[seasonal Team activation](season-team-activation.md) separately. Neither job
calls the other, and ordinary Mondays do not require a seasonal activation pause.

## What the repository establishes

`app/contest_jobs.py` provides a one-shot command that exits when complete:

```bash
python -m app.contest_jobs finalize_due_weeks
```

Run from `woodshed-woodchuck` with the application environment, current compatible
schema and `DATABASE_URL` explicitly targeting the same persistent database as
the web service. Do not expose its value. This command has no `--database-url`,
`--week` or `--now` flag; no browser session or admin token is required in the
trusted operational environment. Existing Contest definitions must be present;
missing definitions fail finalization. Normal contest setup seeds them;
`provision_weeks` and Season bootstrap do not.

The operational service name `woodshed-contest-finalizer` appears in the existing
runbooks. No tracked Render Blueprint, cron expression or scheduler startup
establishes its service type, live command, cadence or alert destination. The
README and older H2B runbook describe Render deployment and previously supplied
web settings; they are not current live verification. Do not infer a working
schedule from the service name or from a successful web deployment.

## Dashboard-confirmed settings

Jeff confirmed these settings in Render:

| Setting | Confirmed value |
| --- | --- |
| Command | `python -m app.contest_jobs finalize_due_weeks` |
| Cron | `10 17,18 * * 1` |
| Timezone | UTC |
| Runs | Mondays at 17:10 and 18:10 UTC. |
| Workspace notification destination | Email. |
| Workspace failure notifications | Enabled; delivery untested. |

These dashboard settings establish the configured command and schedule;
the successful job run's commit, any finalizer notification override and actual
notification delivery remain unverified. Halloween
provisioning and seasonal Team activation remain outstanding.

### Supplied build evidence

The finalizer service's Events page shows its latest successful build as
`f3ff288`, “chore: add keeper artwork and refresh instrument assets,” dated
September 14, 2026. This records the build shown in Jeff's screenshot; it does
**not** establish which commit the previously supplied successful job run used.
Build evidence and execution evidence must remain separate.

## Supplied production execution evidence

Jeff supplied the following Render evidence; no production access was performed
for this documentation update:

| Observation | Evidence |
| --- | --- |
| Scheduled run started | September 14, 2026, 17:10:24 UTC (12:10:24 America/Chicago). |
| Week finalized | Week beginning September 7, 2026. |
| Final summary | `due=1`, `finalized=1`, `failed=0`, `skipped=8`. |
| Render outcome | “Cron job run finished successfully.” Numeric exit code was not shown. |
| Existing future/current weeks | September 14 and September 21 were skipped as not ended. |

This verifies **one successful scheduled production finalization run** and the
existence of those two additional week rows at that time. The command/schedule
are separately dashboard-confirmed above. This run does not establish its
commit, failure-notification delivery, Halloween provisioning or seasonal
Team activation.
It also does not identify how those weeks were created or certify their stored
deadlines. Keep this evidence separate from local fixture validation.

## Weekly boundary and deadlines

`central_week_boundaries` computes the containing Monday using America/Chicago.
At Sunday night → **Monday 00:00 Central**, the next week becomes current by
date. Provisioned weeks are reused. If missing, `ensure_current_contest_data`
can create the current week during normal contest/Team setup and commits it;
read-only date lookups alone do not. Pre-provisioning removes reliance on a
student request. Within a season, this neither recreates Teams nor requires the
previous week's results to be finalized. A season boundary selects the next
Season by date even if its Teams are not ready, hence the separate supervision.

| Event | Default Central time | Meaning |
| --- | --- | --- |
| Week ends / next begins | Monday 00:00 | Prior interval is Monday-inclusive to next-Monday-exclusive. |
| Verification cutoff | Monday 12:00 | Approved verification must meet the stored cutoff for verified scoring. |
| `finalize_after` | Monday 12:05 | Finalizer requires `now` strictly later than this **and** the verification deadline. Exactly 12:05 is too early. |

Stored deadlines always win. September 28, 2026 uses 05:00 UTC for midnight,
17:00 UTC for noon and 17:05 UTC for `finalize_after`. After November 1's DST
change, November 2 uses 06:00, 18:00 and 18:05 UTC respectively. Never shift
stored timestamps or add fixed UTC weeks to calculate a new deadline.

## Simple recurring strategy and recovery

The confirmed schedule is `10 17,18 * * 1` (UTC). In Central daylight time,
these runs are Monday 12:10 and 13:10; in standard time, 11:10 and 12:10.
Thus at least one run is after the default 12:05 deadline in either regime;
the standard-time 11:10 run skips the just-ended week as not yet due. Stored
custom deadlines still govern. Missed/failed work can be retried manually or
on a later scheduled run, which inspects **all stored weeks across seasons**.
[Render cron docs](https://render.com/docs/cronjobs).

The earlier hourly suggestion (`10 * * * *`) remains an optional proposal for
more frequent catch-up, not the live schedule or a change made here.

The job selects open weeks whose end and both deadlines have passed. It locks
and rechecks each week, commits each successful week independently, skips
finalized/not-due weeks, rolls back a failed week, and continues with later due
weeks. Existing result/reward uniqueness guards make retries duplicate-safe.
Non-open `pending` weeks are skipped, not automatically repaired. It never
creates next weeks, activates Teams, closes Seasons or finalizes director-defined
contests.

1. Verify the intended database/release and calendar before changing recurring
   execution. Check the [live settings](#live-settings-to-verify); do not create
   a second scheduler or invoke a public HTTP endpoint.
2. On each expected run, inspect its exit status and final `run_finished` JSON:
   `due`, `finalized`, `skipped`, `failed`. Normal completion has exit 0 and
   `failed: 0`; zero due is valid but does not prove future weeks are provisioned.
3. On failure, inspect `week_failed` and, if present, `week_integrity_error`
   (safe constraint/table/stage fields). Fix the cause and rerun the same command.
   Weeks already committed remain finalized; retries cover the remaining open
   due weeks. Do not reset results, rewards, snapshots or deadlines.
4. If the process dies or output is lost, inspect the next run and retry rather
   than assuming every week rolled back. Startup/connection failures can prevent
   `run_finished`; retain stderr and the process exit status too.

For an existing Render Cron Job, its **Runs** page provides logs and **Trigger
Run** for a manual retry. Wait for any active run first: manually triggering
cancels it; scheduled overlap is deferred by Render's single-run guarantee.
This only covers that one cron service, not other shells/jobs.
[Render run controls](https://render.com/docs/cronjobs).

## Visibility and notifications

Use the existing JSON stdout/stderr plus the operational service's run history.
A healthy service/build is not evidence of a recent successful finalizer run.
`latest_job_outcome` on the existing authenticated contest-admin view is an
in-memory value in the web process: it cannot report a separate cron process's
last run and is lost on restart. Keep existing authentication intact; no new
public endpoint/dashboard is needed.

Jeff confirmed that the workspace notification destination is **Email** and
failure notifications are **enabled**. Delivery is **untested**. The finalizer's
**Settings → Notifications** override remains to be checked; workspace settings
alone do not establish its effective notification behavior or receipt by Jeff.
Render supports failed-cron email notifications. Shell-run supervised
activation requires Jeff to inspect its JSON/exit status directly; do not assume
it generates a cron alert. The application's SMTP chart/invitation delivery is
not wired to these jobs. [Render notification controls](https://render.com/docs/notifications).

A missing/suspended schedule may produce no failed-run notification. Jeff should
check the latest execution timestamp as well as failure alerts. Do not send test
messages or add notification plumbing as part of this documentation change.

## Live settings to verify

The command, cron/timezone, run timestamp, summary, successful Cron Job
outcome and latest successful build shown (`f3ff288`) are supplied, and Jeff
located Maintenance Mode on the Woodshed web
service. Do not request these again. Remaining non-secret fields (or redacted
screenshots), not credentials:

- Exact finalizer service name, repo branch/root and the commit associated with
  the successful job run (not established by the separate build screenshot);
  current enabled/suspended state. The numeric
  exit code for the supplied run remains unobserved, not a blocker to accepting
  Render's successful outcome. Confirm privately that `DATABASE_URL` targets
  the intended shared database; share only confirmation, never the URL.
- Any finalizer service notification override and actual failed-cron email
  delivery. Workspace Email destination and enabled failure notifications are
  already confirmed; do not request them again. Do not share tokens or webhook URLs.
- Web Maintenance Mode operation is untested despite confirmed availability.
  Verify its behavior and the controls/list for background, scheduled and
  private/operator writers; locating the control does not establish their pause.

These are outstanding live checks, not production changes performed here.
The successful production run verifies ordinary finalization at that time;
neither it nor local tests establish readiness for the next seasonal boundary.

## Exceptional history or season work

`python -m app.contest_jobs audit_history --week 2026-07-27` is a diagnostic
simulation with savepoint rollback by default; `--apply` permits deterministic
missing-artifact repair. It is not the routine finalizer retry or the strictly
read-only Team preflight. Review [H2B maintenance](team-continuity-repair.md) for
activation conflicts/frozen history, and [canonical seasons](canonical-seasons.md)
for bootstrap/closure. Rollover requires source finalization and explicit approved
dates; it is not advance preparation. No unapproved Band Camp 2027 end date is
supplied here.
