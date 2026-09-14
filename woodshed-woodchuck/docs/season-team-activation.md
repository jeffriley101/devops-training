# Season team readiness and activation

These explicit job commands reuse the H1B continuation engine and H2B schema,
snapshot, insertion fence, and verification helpers. Nothing runs from startup,
login, GET requests, current-season lookup, or the existing finalizer command.
No migration is required; the approved revision remains `s9n0o1p2q3r4`.

Supply `DATABASE_URL` explicitly in the job environment (standard Render
`postgresql://` and `postgres://` URLs use psycopg 3), or use `--database-url`.
Credentials are never included in the JSON diagnostics. There is no fallback to
the application's local database. Commands exit nonzero on NOT_READY or errors.

## Read-only preflight

```bash
python -m app.contest_jobs team_preflight
```

Automatic discovery inspects the earliest upcoming enabled durable season and
its unique adjacent predecessor. Dates and timezones come from database rows,
not a second calendar. Ambiguous coverage, skipped seasons, and timezone changes
are refused. Explicit review of a pair is also supported:

```bash
python -m app.contest_jobs team_preflight \
  --source-season back-to-school-2026 --destination-season halloween-2026
```

Preflight uses the same H1B rules but allows a prospective, read-only roster
projection before midnight. Membership timestamps are evaluated against the
destination boundary. This does not create future Teams or memberships and
does not authorize activation: a later switch, deletion, moderation, or other
change can invalidate READY. Connections enforce PostgreSQL REPEATABLE READ,
READ ONLY or SQLite read-only/query_only through the H2A connection helper.

JSON includes keys/IDs, UTC boundary, source Team count, expected roster and
creation counts, represented rows, destination history counts, freeze state,
REVIEW/CONFLICT counts, and sorted machine-readable reason codes. It omits names,
private codes, PINs, report text, and practice notes.

## Boundary activation

```bash
python -m app.contest_jobs team_activate
```

Automatic discovery selects the date-covered enabled destination and its
adjacent predecessor. To pin an operator run to the intended transition:

```bash
python -m app.contest_jobs team_activate \
  --source-season back-to-school-2026 --destination-season halloween-2026
```

`team_activate` is an explicit write command for a trusted operational job; it
does not accept an approved JSON plan as instructions. It acquires H1B's sorted
season/family/team/profile/membership locks, revalidates discovery, and plans
again with the real current time. Before midnight it returns NOT_DUE without
inserts. The Halloween boundary is September 28, 2026 at 00:00 America/Chicago,
05:00 UTC. UTC offsets are always computed with ZoneInfo, including DST.

Unattended execution requires zero REVIEW and zero CONFLICT for the WHOLE
transition. Hidden/under-review Teams, empty public rosters, invalid private
owners, collisions, ambiguous memberships, exact-boundary endings, incompatible
successors, and contradictory destination history block all inserts. Pending
private requests stay historical; eligible private successor codes rotate via
H1B. The destination first week must be open and every destination week must be
unfrozen. Any destination results/snapshots or director-contest complications
also block backdating. Use reviewed H2B maintenance for exceptions.

H1B creates only successor Teams and memberships. Verification inside the same
transaction permits exactly those new rows, checks identity/family/boundary/
opening-week values, compares the protected historical snapshots, and requires
a fresh plan with zero remaining creates. The SQL/ORM insertion fence rejects
other mutations. Any exception or verification failure rolls back everything.
Repeated successful runs return ALREADY_COMPLETE with zero new rows. READY on
activation includes committed creation counts and a passed verification result.
There is no persistent readiness marker: future changes are always rechecked.

Status meanings: READY = projected safe or successfully activated;
NOT_READY = blocked, no activation writes; ALREADY_COMPLETE = compatible rows
already represented with no creates; NOT_DUE = boundary not reached;
NO_TRANSITION = no relevant transition in the selected discovery mode.

## Finalization coordination

Midnight activation does not wait for noon verification. Source memberships
remain historical and unchanged. The source final week can finalize later using
its own memberships, snapshots, and Team IDs. The normal finalizer now takes the
same season fence before week/reward locks, serializing it with activation;
scoring, deadlines, and rewards are unchanged.

If delayed activation reaches the STORED source final-week `finalize_after`
while that week remains unfinalized, activation blocks. Finalize normally first
(strict deadline rules still apply), then rerun preflight/activation:

```bash
python -m app.contest_jobs finalize_due_weeks
```

Inspect due weeks before running that command: it finalizes all due weeks, not
just the source. If the destination is already due/frozen, use an operator
review; this command must not be used to bypass frozen history. Activation never
invokes finalization itself. No noon wait is added to the midnight path.

## Scheduling and rollout before September 28

The repository documents `woodshed-contest-finalizer` behavior but does not
establish its actual live cadence. Do not assume the current Monday-after-noon
schedule is sufficient. Deploy/checkpoint only after review, then explicitly
configure the existing operational service (or its command dispatch) to invoke
preflight before the transition and activation at the boundary, with monitored
retries. Keep the existing due-week finalization invocation independent. These
commands do not install another scheduler or change Render settings.

**Calendar prerequisite:** canonical season bootstrap creates Season records,
not all future ContestWeeks. Normal `ensure_current_contest_data` creates the
current week lazily and commits; it is deliberately not called from this atomic
activation transaction. Preflight MUST find the source final week and an open
destination first week already provisioned. A missing first week returns
`destination_first_week_not_open`, NOT_READY, with no writes. Inspect this before
September 28 and arrange separately reviewed calendar provisioning. The existing
`season_maintenance bootstrap` alone does not provision Halloween weeks, and
source `rollover_season` cannot be used before source finalization. Do not wait
for a student's page load to satisfy this prerequisite. If it remains unmet,
keep traffic paused and resolve it through controlled calendar setup before
activation; this release does not add a week-provisioning command.

A scheduled process alone cannot promise a zero-second empty-Team window:
startup delay or a rejected plan can outlast midnight, while date-covered lookup
continues to advance. For this release, arrange a brief controlled writer/user
pause spanning midnight and keep it until activation verification passes. Run
preflight in advance and resolve every REVIEW/CONFLICT; run activation only at
or after the real boundary. No fake `--now` override is available in the CLI.
An unattended failure must page/reach the operator via nonzero job status and
the structured reason/count log; it must not reopen traffic or partially apply.

After success, check representative students' current Teams, verify zero creates
with an explicit repeat, and resume traffic. Opening-week continuation is the
initial selection and leaves exactly one correction. Standings start fresh.
Do not continuously replay old transitions after their destination freezes:
the frozen-history and deadline guards deliberately remain conservative.
Use explicit pairs for repeat checks; automatic preflight now targets the NEXT
season. Concurrent unrelated historical activity may cause strict snapshot
verification to refuse; retry under controlled conditions, never weaken it.

H2B remains the audited manual fallback. There is no historical chart/award
repair, persistent audit schema, billing change, or automatic web activation.
