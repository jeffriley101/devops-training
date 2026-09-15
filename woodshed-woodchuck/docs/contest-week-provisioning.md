# Advance ContestWeek provisioning

Prepare approved weeks ahead, run [ordinary finalization](contest-finalization-job.md)
through the existing operational service, and supervise
[seasonal Team activation](season-team-activation.md). These are separate jobs.
Local tests do not establish production provisioning, readiness, or scheduling.
Jeff's [September 14 production run evidence](contest-finalization-job.md#supplied-production-execution-evidence)
confirms that September 14 and September 21 week rows existed then; it does not
establish Halloween coverage or that `provision_weeks` created those rows.

## Prerequisites

Run from `woodshed-woodchuck` with the application environment and an explicit
`DATABASE_URL` for the intended database. Never paste its value into logs.
Provisioning also accepts `--database-url`; it has no local-database fallback.
The deployed code must include provisioning (`bc766ff`), practice-time precision,
and the schema-compatibility update. This combined code requires exactly one
Alembic head, `t0p1q2r3s4t5`, and its required columns/TeamFamily constraints.
The previous `s9n0o1p2q3r4` schema is unsupported: even read-only planning selects
`ContestWeek.practice_scoring_mode`; the combined result ORM also selects
`ContestResult.precise_score`. Older, unknown, multiple, or incomplete schema
states refuse before writes. Do not stamp a revision to bypass migration.

In production, apply the precision migration **before starting the new code**.
Pause/drain finalization writers and coordinate the web/finalizer upgrade;
resume only when all entry points use the compatible release. Follow the
[precision release ordering](practice-time-precision.md#release-ordering-and-maintenance-compatibility).
This compatibility update adds no further migration.

Season records must already match `app/seasons.py`: canonical keys, names, dates,
and `America/Chicago`. Provisioning does not create or enable Seasons. Resolve
missing records through the [canonical bootstrap runbook](canonical-seasons.md),
reviewing its plan first; do not use incident repair or rollover to force this
transition. Team activation additionally requires source `active`/`closed`,
destination `active`, and a clean Team preflight. Ordinary finalization also
requires the existing Contest definitions; week provisioning does not seed them.

## Recommended: prepare both complete seasons

The command supports **all seven weeks**: Back to School September 14–27 (two)
and Halloween September 28–November 1, 2026 (five). Dates here are inclusive;
stored week ends are exclusive Mondays. Run plan, review, apply, then plan again:

```bash
python -m app.contest_jobs provision_weeks \
  --season back-to-school-2026 --season halloween-2026
python -m app.contest_jobs provision_weeks \
  --season back-to-school-2026 --season halloween-2026 --apply
python -m app.contest_jobs provision_weeks \
  --season back-to-school-2026 --season halloween-2026
```

For only the activation minimum—source September 21–28 and destination September
28–October 5—use this bounded alternative, adding `--apply` only after review,
then repeating without it to verify:

```bash
python -m app.contest_jobs provision_weeks \
  --source-season back-to-school-2026 --destination-season halloween-2026
```

Do not combine the two scope forms. Whole-season preparation needs an approved
end date. Band Camp 2027 still has no approved end: do not invent one or provision
its whole season. An adjacent Beach → Band Camp 2027 transition can provision
only the source final and destination first week. Unresolved future calendar
changes, including Hibernaculum proposals, remain unchanged.

## Results, preservation, and retries

- Plan is database-enforced read-only. Review `scope`, `weeks`, `conflicts`, and
  `activation_prerequisites`. `READY` means calendar preparation is safe, not
  that Team activation is ready.
- `BLOCKED`/nonzero means resolve missing/conflicting Seasons, wrong ownership,
  malformed or overlapping weeks before retrying. Application makes no partial
  inserts. Full-season mode also rejects weeks assigned outside that season;
  transition mode inspects only its two intervals and overlaps with them.
- Successful apply returns `APPLIED`, committed `created` count, new row IDs,
  and `missing: 0`. Repeat plan must have zero missing/conflicts and seven
  unchanged rows for the complete pair. Repeat apply returns `ALREADY_COMPLETE`
  and zero creates. Keep the JSON and process exit status as operational evidence.
- Matching ownership/date intervals remain unchanged, including finalized rows,
  stored deadlines, timestamps, scoring provenance, fractional/frozen results,
  rewards, crowns and membership snapshots.
  `stored_deadlines_differ` reports custom deadlines; never normalize them.

Dates/deadlines come from the canonical rules and `contest_week_schedule`, with
ZoneInfo conversion per week. See [weekly timing](contest-finalization-job.md#weekly-boundary-and-deadlines)
for DST and strictly-after finalization rules.

Apply replans under sorted Season locks. SQLite uses `BEGIN IMMEDIATE`;
PostgreSQL uses READ COMMITTED plus a short `SHARE ROW EXCLUSIVE` lock on
`contest_weeks`, including coordination with legacy/lazy calendar writers.
The existing per-season/start unique constraint provides another duplicate
safeguard. Inserts and verification commit together; lock/constraint errors or
failed verification roll back. Retry the same scope after transient contention.
After a lost connection or missing final report, plan again before deciding
whether the previous apply committed. Never repair history to force a retry.

Next, follow the activation runbook's preflight and supervised boundary procedure.
Provisioning neither pauses users nor activates Teams nor awards anything, and
it installs no schedule. See the finalizer runbook's
[live settings checklist](contest-finalization-job.md#live-settings-to-verify)
before arranging operational execution.
