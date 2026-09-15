# Supervised seasonal Team activation

Use the existing explicit jobs after [calendar preparation](contest-week-provisioning.md).
Run from `woodshed-woodchuck` in the application environment with an explicit
`DATABASE_URL` (or `--database-url`); there is no local-database fallback.
The current schema guard requires `s9n0o1p2q3r4`; this workflow adds no migration.
These CLI jobs install no scheduler or startup hook. Existing authenticated
admin finalization remains separate from seasonal activation.

## Commands and safeguards

Prospective **read-only** preflight, before the boundary:

```bash
python -m app.contest_jobs team_preflight \
  --source-season back-to-school-2026 --destination-season halloween-2026
```

Explicit supervised activation, only at/after the boundary and the verified
pause below:

```bash
python -m app.contest_jobs team_activate \
  --source-season back-to-school-2026 --destination-season halloween-2026
```

Without keys, preflight discovers the earliest upcoming enabled adjacent pair;
activation discovers the current destination. Use explicit keys for review and
repeat checks. Source must be `active`/`closed`, destination `active`, with
unambiguous adjacent coverage and compatible timezones.

Preflight uses a database-enforced read-only snapshot and projects boundary
memberships. Its JSON includes keys/IDs, UTC boundary, expected Team/member
counts, REVIEW/CONFLICT/frozen counts and `reason_codes`. READY is prospective:
roster/moderation changes can invalidate it before activation.

Activation locks and replans, requiring zero REVIEW/CONFLICT for the **whole**
transition. It retains collision, ambiguous membership, private-owner/code,
moderation, boundary, source-deadline, director-contest and frozen-history guards.
The source final week must exist; the destination first week must be open and
all destination weeks/history unfrozen. Hidden/under-review Teams and incompatible
successors need review, not partial activation.

Only successor Teams/memberships can be inserted. The transaction verifies the
exact continuation delta and unchanged protected history, including source
memberships; exceptions/refusals roll back. Successful repeats create nothing.
Source finalization remains a later independent job.

## Supervised boundary procedure

Prepare the [complete source/destination calendar](contest-week-provisioning.md)
in advance. Run the explicit preflight above and retain expected Team/member
counts. No automatic seasonal activation is proposed: Jeff supervises this
window; ordinary weekly finalization follows its own recurring invocation.

### Pause coverage: platform prerequisites, not an application feature

There is **no application maintenance flag, request gate, active-session drain,
or general draft autosave**. H2B's `--ack-writers-paused`,
`--ack-finalization-paused`, and `--ack-maintenance-mode` are attestations,
not controls. The older [H2B runbook](team-continuity-repair.md) mentions Render
Maintenance Mode and owner-reported deployment settings. Jeff has now located
Maintenance Mode on the Woodshed web service: **availability is confirmed,
operation is untested**, and background-writer pause is not established.

Render provides Maintenance Mode for paid web services under **Settings →
Maintenance Mode**. It returns a maintenance response to public requests while
the app stays running; private-network and SSH access remain available.
Jeff's confirmed control still needs an operational rehearsal; its presence
alone does not demonstrate this behavior on the live service or stop background
writers. [Render maintenance documentation](https://render.com/docs/maintenance-mode).

| Writers/access to coordinate | Existing protection and remaining gap |
| --- | --- |
| Public student, verifier and director requests; Team creation, selection, approvals, moderation and account deletion | Participating Team mutations share Season locks, but locks do not pause users. Block public traffic for the short boundary window and let accepted requests finish. |
| Charts/verification, BOARD/Arcade points, rewards, profile changes, director contests and calendar bootstrap | Not all use the Team lock protocol. Some apparent reads lazily create contest data. Activation compares protected history across the database, so unrelated writes can also cause refusal. A Team-page-only restriction is insufficient. |
| Finalizer cron, other workers, operator/admin/import/repair jobs and direct/private database writers | Public Maintenance Mode does not suspend them. Identify and pause their actual triggers, let active jobs finish, and prevent manual runs. No application command performs this. |

**Execution blocker:** a verified public-traffic pause plus writer drain and an
in-progress-work plan have not been established by repository evidence. The
smallest proposed solution is an agreed save-before-window procedure using
Render's confirmed Maintenance Mode control and the existing controls for
each background writer. Verify these controls and rehearse save/failure/retry on
a non-production environment first. If those controls are unavailable, choose a
separately approved platform-level pause before attempting activation. Do not
substitute acknowledgment flags, a request-gate implementation, forced restarts,
or an assumption that midnight has no users.

### Protect timers and unsaved charts

The operational job does not send a browser reload or logout. A loaded page can
continue running during an outage, but its save requests can fail. Browser
suspension, reload, navigation or closure can still lose work:

- **Ordinary timer:** its start timestamp is saved in same-tab `sessionStorage`;
  restoring computes wall-clock elapsed time, capped at two hours. It does not
  pause for maintenance. Stopping transfers rounded minutes into the form and
  clears that timestamp; it does **not** save a chart.
- **Ordinary unsaved chart:** fields and the pending retry key normally live in
  page memory. The only specific draft save is when following Manage Verifiers,
  with a 30-minute same-tab restore; it is not general autosave. On a failed
  submission, keep the page open and retry there after service returns. Avoid
  repeated new submissions if the earlier save's outcome is uncertain; inspect
  BOOK for the saved chart.
- **Pristine:** detected seconds and retry key live in memory until Save & Finish
  succeeds. Pause stops timing, not data loss. Failed saving exposes Retry Save;
  keep that tab open, do not start another session or approve leaving. Its
  before-unload warning is not durable recovery.

Ask users ahead of the window to stop and **successfully save/confirm in BOOK
before the pause**, then avoid starting new work until resume. Do not clear
browser storage, force logout or tell them to reload an unsaved form. If someone
is still unsaved, the procedure cannot guarantee preservation: resolve the save
before blocking requests. Merely leaving a tab open is not a durable backup.

This matters at midnight: ordinary charts keep the submitted practice date,
but server Team attribution uses membership in the season current at submission;
Pristine also uses the submission's Central date. A pre-midnight timer does not
reserve source-season attribution. Finish/save beforehand rather than assuming
a cross-boundary retry will retain the old date/Team. This run changes neither
attribution nor practice-time precision.

### Run, verify, resume, recover

1. **Before the boundary:** resolve calendar/preflight blockers; confirm the
   release, intended database, recent backup, actual pause controls and writer
   list. Coordinate the save window and keep unrelated deployments/manual jobs
   out of it. Complete student saves while requests still work.
2. **Pause:** use the verified platform public-traffic control, pause background
   triggers and drain accepted requests/jobs. Confirm public requests are blocked,
   no jobs are running and private/operator writers are idle. No repository
   drain command exists; the cron's pause/resume control and treatment of active
   runs still need live verification. If that cannot be established, stop here.
3. **Activate:** at/after **September 28, 2026, 00:00 America/Chicago (05:00 UTC)**,
   use the exact explicit `team_activate` command above in the authenticated
   operational shell. Retain stdout/stderr and exit status. No `--now` override
   exists. Never bypass REVIEW/CONFLICT, frozen history or boundary checks.
4. **Verify while paused:** require `READY` or `ALREADY_COMPLETE`,
   `verification.passed: true`, expected creation/roster counts and zero
   `remaining_team_creates`/`remaining_membership_creates`. Repeat that explicit
   activation command for `ALREADY_COMPLETE` and zero new rows. Exit zero alone
   is insufficient: `NOT_DUE` and `NO_TRANSITION` also exit zero.
5. **Resume:** only after verified success, release the pause and re-enable the
   recorded background triggers. Check representative existing students' current
   Teams/BOOK/BOARD without changing selections or spending correction allowances.
   Preserve sessions. Continued membership leaves one opening-week correction.
6. **Failure:** `NOT_READY`, exceptions or verification refusal stop the whole
   activation transaction. Keep the established pause, inspect `reason_codes`,
   resolve the cause and rerun explicit preflight/activation. If output/connection
   was lost, the commit outcome is unknown: inspect preflight, then repeat the
   guarded activation while still paused. Do not delete successors or restore
   old deadlines to force success. Keep Jeff informed of a prolonged pause;
   resuming after midnight without readiness is not a safe fallback.

If the source's stored `finalize_after` is reached while unfinalized, activation
refuses. Run the [ordinary finalizer](contest-finalization-job.md) alone only
**after both stored deadlines are strictly passed**, inspect all due weeks first,
then pause it again and rerun preflight/activation. Source memberships remain
unchanged so later source finalization uses historical source Teams and snapshots.
A due/frozen destination or unresolved conflict needs reviewed maintenance;
H2B is a separate reviewed fallback, not a way around these guards. Do not replay
old transitions after destination history freezes.

## Deferred improvements

Seamless season transitions should eventually coordinate date-based Season
selection with Team readiness, while retaining transaction, conflict,
frozen-history and deadline safeguards. That implementation is deferred.
**Practice-time precision remains the next separate code task.** No scheduler,
maintenance framework, new endpoint or dashboard is introduced here. For current
job visibility and outstanding live settings, use the
[finalizer operations runbook](contest-finalization-job.md#visibility-and-notifications).
