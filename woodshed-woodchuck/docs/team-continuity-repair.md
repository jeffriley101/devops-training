# H2B controlled team continuity maintenance

H2B is explicit operator tooling around H1B. It creates seasonal successor Teams
and boundary memberships only. It has no route, startup hook, scheduler, migration,
or Render automation. H2A remains the read-only inventory for pre-H1A databases.

The September 14 incident has four approved public Teams and eleven boundary
members (1/3/3/4). Attribution-only H2A REVIEW flags do not disqualify these
members. PracticeChart 54 remains NULL. CampPointAwards 442–444 are deferred;
neither they nor any other chart/award may be changed by H2B.

## CLI and evidence

Use a deliberately supplied `DATABASE_URL` from your secure operational process.
There is no .env loading or default local database. `--database-url` is also
accepted, but putting credentials in process arguments/shell history is
discouraged. Standard `postgresql://`, `postgres://`, and
`postgresql+psycopg://` URLs use psycopg 3 internally. TLS/query parameters such
as `sslmode=require` are preserved. SQLite targets must be existing files.

Every command requires a new explicit `--output` file, created exclusively with
mode 0600. Existing plans, reports, symlinks and database files are not overwritten.
Help and invalid/missing subcommands cannot apply anything. Both PLAN and VERIFY
use H2A's read-only connections: PostgreSQL READ ONLY / REPEATABLE READ, SQLite
mode=ro / query_only. No ORM autoflush occurs in either read command.

```sh
.venv/bin/python -m app.team_continuity_repair --help

.venv/bin/python -m app.team_continuity_repair plan \
  --source-season band-camp-2026 \
  --destination-season back-to-school-2026 \
  --output <PLAN_PATH>

.venv/bin/python -m app.team_continuity_repair apply --apply \
  --plan <PLAN_PATH> --plan-sha256 <PLAN_SHA256> \
  --ack-backup-taken --ack-maintenance-mode \
  --ack-writers-paused --ack-finalization-paused \
  --confirm 'APPLY TEAM CONTINUITY' \
  --output <APPLY_REPORT_PATH>

.venv/bin/python -m app.team_continuity_repair verify \
  --plan <PLAN_PATH> --plan-sha256 <PLAN_SHA256> \
  --output <VERIFY_REPORT_PATH>

.venv/bin/python -m app.team_continuity_repair plan \
  --source-season band-camp-2026 \
  --destination-season back-to-school-2026 \
  --output <POST_PLAN_PATH>
```

Placeholders must be replaced; H2B does not obtain production credentials. Read
the JSON as well as the summary. `PLAN SHA-256` is printed on completion. A plan
can report blockers and still be generated successfully; exit 0 from PLAN is
not permission to repair. APPLY rejects any REVIEW/CONFLICT action or global
blocker, so this wrapper never silently applies a safe subset of a conflicted
plan. Exit 1 is failure; exit 2 is invalid CLI arguments.

## What is approved

The SHA-256 hashes canonical JSON of `content`: sorted keys, compact separators,
normalized UTC timestamps, ASCII escapes, no NaN. Display metadata (`generated_at`
and application commit) is outside the hash. File formatting does not affect it.

Content includes format/revision, sanitized target identity (including PostgreSQL
schema), source/destination IDs/keys, UTC boundary and Monday, H1B actions and
classifications, social identity, relevant Team/member rows, eligible profile
status/capabilities, seasons, stored week state/deadlines, and protected-history
counts/hashes. Private join codes are represented only by a digest; names, PINs,
notes, report text, reward source keys and credentials are not exported (Team
display names and operational IDs are intentional). Never publish these reports.

History hashes cover allowlisted structural/scoring fields in TeamFamily,
PracticeChart, CampPointAward, ContestResult, TeamWeekMembershipSnapshot,
RewardGrant, CrownAward, TeamJoinRequest, TeamReport and director-contest tables.
They cover all rows of those tables, deliberately failing closed on other
concurrent history changes too. They are not hashes of every private/free-text
field. Runtime SQL/ORM mutation guards additionally reject historical updates,
deletes, DDL, and inserts outside `teams`/`team_memberships`. Full-row comparisons
in isolated tests verify that excluded private fields also remain unchanged.

APPLY regenerates the plan from the database under H1B locks. JSON is never
replayed as insertion instructions. Any semantic mismatch aborts before H1B
insertion. An old creates-plan is stale after a successful apply and cannot be
applied again. Generate/review the zero-create post-plan for a repeat no-op.
Repeated no-op applies are safe while state remains unchanged.

## Schema, transaction and timing rules

Only the single revision `t0p1q2r3s4t5` is currently approved. The combined
ORM requires the precision migration; `s9n0o1p2q3r4`, other older revisions,
unknown descendants, multiple heads and incomplete schemas fail closed. Required
tables/columns and the named non-null family FK, RESTRICT deletion, family index,
and season/family unique constraint are checked. A future
revision needs explicit compatibility review before changing this allowlist.
Snapshots include fractional `precise_score` and per-week `practice_scoring_mode`;
regenerate/review plans after upgrading. Follow the
[precision release ordering](practice-time-precision.md#release-ordering-and-maintenance-compatibility):
migrate before starting the new production code and coordinate web/finalizer
upgrades with all finalization writers paused/drained.

Seasons must exist, be adjacent, share a timezone, and have no ambiguous season
covering the destination boundary. No Team/member IDs are hard-coded into runtime.

APPLY owns a single transaction. It acquires H1B's deterministic locks: Seasons,
TeamFamilies, Teams, profiles, memberships. PostgreSQL uses READ COMMITTED;
SQLite uses H1B BEGIN IMMEDIATE. H1B itself re-plans under those same locks before
insertion. Post-apply verification runs before commit, and failure rolls back the
entire repair. PostgreSQL sequences can advance on rolled-back inserts; gaps
are normal and do not imply partially committed repair rows.

Operator acknowledgments attest to external conditions; they do **not** turn on
Render maintenance or stop workers. Finalization/calendar/import writers must
actually be paused and drained. H1B locks coordinate participating Team writers;
they do not promise serialization with every arbitrary finalizer or direct SQL
writer. Do not run such work concurrently with repair.

The source final week is found from stored season/week boundaries. At or after
its stored `finalize_after`, an unfinalized source week blocks APPLY. Finalize
normally in isolation, then obtain a fresh inventory/plan. H2B never finalizes,
changes deadlines or closes seasons. At the exact deadline H2B already blocks;
the normal finalizer requires strictly later than its deadlines, so wait until
that boundary passes. Before the deadline, continuity can proceed while the
source remains open. The source deadline is checked again just before commit.

The destination first week must be open/unfrozen. Finalized destination weeks,
results/snapshots, linked frozen artifacts and relevant director contests block
apply. Keep all destination weeks unfrozen during maintenance. The continuing
membership starts at destination local midnight in UTC; selected_week_start is
the Monday containing that date. For this incident: `2026-09-14T05:00:00Z` and
`2026-09-14`. One opening-week correction remains under unchanged H1B rules.

VERIFY uses the original approved plan and accepts only its exact expected
successor/member additions. It checks source rows and protected history against
the original fingerprints, membership boundaries, family/social identity,
unexpected destination history, and zero remaining creates. Run it before
releasing maintenance: subsequent user activity, finalization, or a source
deadline crossing can legitimately make a strict verification fail and require
operator inspection. It is not a perpetual health check.

## Evidence and failure handling

The apply output path is reserved before opening a writer. A prepared report
with affected IDs and verification is flushed/fsynced before database commit.
It is marked `prepared_not_committed`, never as successful payment/repair proof.
After commit the same exclusively created file is updated to `committed`.
No existing external file is overwritten.

Filesystem and PostgreSQL commits cannot be atomic together. If final report
writing fails after database commit, CLI reports `DATABASE COMMITTED` and directs
the operator to VERIFY. A process crash/ambiguous commit can leave a prepared
report; that is **not** a commit receipt. Inspect using VERIFY and a new read-only
PLAN before retry. Do not assume rollback based on a missing final report. If
prepared evidence cannot be written, the database transaction is rolled back.
Raw driver errors are withheld; do not paste connection secrets into reports.

## Controlled production runbook (operator actions only)

Known Render configuration: `jeffriley101/devops-training`, branch `main`, root
`woodshed-woodchuck`; build `pip install -r requirements.txt`; pre-deploy
`alembic upgrade head`; start `uvicorn app.main:app --host 0.0.0.0 ...`;
Auto-Deploy On Commit. These settings were provided by the owner, not changed by
H2B. A normal uncontrolled push is not the repair procedure.

1. Finish independent review and checkpoint the exact release candidate.
2. Render UI: turn Auto-Deploy OFF before releasing the reviewed commit.
3. Take an initial production snapshot/backup.
4. Render UI: enable Maintenance Mode. Pause/drain old app writers, admin/import
   operations, and finalization/calendar jobs. A maintenance page alone is not
   proof that every writer stopped.
5. Run H2A with the deliberately selected database and a new evidence file:
   `python -m app.team_continuity_inventory --output <FINAL_INVENTORY_PATH>`.
   Compare to the reviewed 4-Team/11-member incident; take a quiescent backup.
6. Deliberately deploy the reviewed commit in Render. Pre-deploy runs the full
   chain `o5j6k7l8m9n0 → p6k7l8m9n0o1 → q7l8m9n0o1p2 → r8m9n0o1p2q3 → s9n0o1p2q3r4 → t0p1q2r3s4t5`.
   Old workers must not create Teams after s9 requires family_id. Do not allow
   unrestricted old/new overlap. Keep billing flags disabled.
7. Verify `alembic current` reports only `t0p1q2r3s4t5`, the compatible app is
   running behind maintenance, old workers are gone, and the migration assigned one family per
   existing Team without creating successors or changing historical identity.
8. Inspect stored source Week 7 deadlines/state. If due/unfinalized, run the
   existing normal finalizer in isolation (`python -m app.contest_jobs
   finalize_due_weeks`). This command considers **all** due weeks: inspect that
   list first and do not accidentally finalize destination weeks. If other due
   weeks make that unsafe, stop for a scoped normal-finalization procedure.
   Verify source snapshots/results and pause the finalizer again; rerun H2A.
   Never use `audit_history` as a substitute for the strictly read-only H2A tool.
9. Keep destination Week 8 unfrozen. Run H2B PLAN. Expect four successor creates,
   eleven membership creates, zero REVIEW/CONFLICT/blockers. Review JSON and SHA.
10. Run APPLY with the reviewed file/hash and every acknowledgment. No attribution
    edits are part of this authorization.
11. Run VERIFY with the original plan, then PLAN to a new path: zero creates.
    Smoke-test representative students, Team chooser/BOARD, and roster display
    without mutating historical records or consuming real correction allowances.
12. Resume users/jobs, exit Maintenance Mode, optionally restore Auto-Deploy.
    If source finalization was not yet due, let it run normally after deadlines;
    season closure still requires all source weeks finalized. Continuity is not
    conditional on premature source closure.

## Rollback boundaries

Before migration, maintenance can be abandoned and the old app resumed. After
migration but before continuation, stay in maintenance and forward-deploy
compatible code or use a carefully reviewed downgrade. Old Team constructors
cannot operate safely at s9. Billing audit downgrade guards may refuse, and p6
downgrade can discard required billing history; never downgrade blindly.

Once the first cross-season successor transaction commits, a family references
multiple seasonal Teams: the s9 downgrade guard deliberately refuses. Prefer
forward correction and never delete history to defeat that guard. After new
user activity references successors, restoring an earlier backup can lose valid
activity. The effective simple downgrade cutoff is commit of the first shared
cross-season TeamFamily.

H1C, award attribution correction, automated season readiness, and deployment
automation remain outside H2B.
