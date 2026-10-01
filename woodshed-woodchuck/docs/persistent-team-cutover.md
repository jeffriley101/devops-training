# Persistent Team authority v1

This release implements Option B. One explicitly approved existing Team row
per TeamFamily becomes the operating Team. Its ID, Family, origin-season
metadata, moderation, visibility, creator, emblem, permanent name claim,
reports, and join code are retained. No seasonal successor Team or membership
is created. Seasons provide artwork, calendar labels, and historical grouping;
ContestWeek governs weekly competition.

## Schema deployment is not cutover

Revision `p20team001`, based on `f19arcade001`, defaults every authority marker
to false and every existing ContestWeek to `legacy_seasonal_v1`. The singleton
`persistent_team_control` is disabled. Migration neither identifies operating
rows nor activates membership authority. Current runtime remains legacy until
the separately reviewed authority operation.

`Team.is_operating` has unique partial indexes for Family, normalized name,
emblem, and public creator. `TeamMembership.is_persistent` distinguishes
current authority from unended historical seasonal evidence; a partial unique
index admits at most one unended persistent membership per profile. A database
trigger also rejects overlapping finite effective intervals; empty intervals
remain legitimate history. PostgreSQL serializes the check by profile and
requires READ COMMITTED for persistent mutations so lock waiters read fresh
state. SQLite compares normalized UTC timestamp text without losing precision.
`TeamJoinRequest.is_persistent` similarly governs pending current requests.
Season columns are retained as nullable origin metadata, with `RESTRICT`
foreign keys so deleting a presentation season cannot delete its operating
Team or memberships. Director-event season grouping is optional.

SQLite batch schema changes use a dedicated migration connection: save and
disable foreign-key enforcement before the physical transaction, run all DDL
and data copying atomically, verify every foreign key before commit, and restore
the original enforcement setting. This prevents table recreation from deleting
or nulling historical child references. PostgreSQL migration handling is
unchanged.

`TeamMembershipTransition` records actual student join/switch/leave choices.
Promotion, week rollover, presentation rollover, administrative removal, and
account deletion do not create student-choice records. Carried membership
does not consume correction allowance. Effective authority uses half-open
membership intervals and explicit persistent markers.

## Inventory and approval

Use the standalone module `app.persistent_team_cutover`; normal season
maintenance/activation does not invoke it. It has no implicit Team selection,
latest-season fallback, name merging, Family inference, or Production IDs.

The operator must supply all approved operating Team IDs and exact approved
unended membership IDs on those Teams. The review includes every Family,
legacy unended membership, ownership/capability, permanent name claim,
identity/moderation state, report, pending request, contribution reference,
week, snapshot, and result. Every pending request requires its own explicit
approved ID. Valid private/director authority and existing codes are preserved;
the operation does not rotate codes.

Preflight refuses missing IDs, duplicate Family authority, duplicate profile
authority, prospective identity collisions, inactive owners/members,
inconsistent claims/private authority, omitted requests, invalid schemas,
already-active authority, ambiguous weeks, prospective frozen evidence, and
nonfinalized director contests requiring separate review. It requires one
current open week and a clean later Monday for persistent weekly rules. An
ambiguous approved Family is a refusal, never an automatically chosen Team.
Historical-only Families may remain without operating authority. Their legacy
rows remain visible in the reviewed inventory and cannot compete as current
authority merely because a seasonal membership is unended.

Promotion must also prove that weekly correction allowance is unchanged.
Every approved membership must start strictly before the current Central week
boundary and record a selection week earlier than that ContestWeek. Preflight
inspects all legacy rows, including ended rows and students now without a Team,
and refuses any current-week selection/start/end evidence. It does not invent
transition-ledger history or reset a consumed correction. If such evidence
exists, wait for a clean later ContestWeek and regenerate/review the inventory.
APPLY rechecks this guard after acquiring its fence and sampling the UTC clock.

The manifest exports selected Team/membership/request rows, hashed join-code
state, exact row counts/full-column hashes for protected tables, target
identity without credentials, planned future-week IDs, and a canonical SHA256.
Private chart notes, PIN hashes, and cleartext codes are not exported. Any
protected-row change invalidates approval, including the existing NULL BOARD
attribution or an unresolved TeamReport. PLAN uses repeatable-read/read-only
PostgreSQL or a query-only existing SQLite file; it has no writable fallback.

Example review commands, with IDs and date supplied by the reviewed inventory:

```sh
python -m app.persistent_team_cutover plan \
  --database-url-env CUTOVER_DATABASE_URL \
  --team-ids <approved-team-ids> \
  --membership-ids <approved-membership-ids> \
  --rules-from-week-start <future-Monday> > reviewed-plan.json
```

If pending requests exist, additionally supply `--join-request-ids`. A plan is
evidence for review, not authorization to apply it. Production execution is a
separate operator action and must not occur merely because this branch is
deployed.

## Reviewed apply and verification

After backup and actual maintenance of all relevant writers/finalizers, APPLY
requires the exact approved file/hash, all four explicit acknowledgments, and
the confirmation phrase. Those flags attest to maintenance; they do not pause
the application or jobs themselves.

```sh
python -m app.persistent_team_cutover apply \
  --database-url-env CUTOVER_DATABASE_URL \
  --plan-file reviewed-plan.json --approved-sha256 <reviewed-sha256> \
  --backup-taken --writers-paused --finalization-paused --maintenance-mode \
  --confirmation 'APPLY PERSISTENT TEAM AUTHORITY' > apply-receipt.json

python -m app.persistent_team_cutover verify \
  --database-url-env CUTOVER_DATABASE_URL \
  --plan-file reviewed-plan.json --approved-sha256 <reviewed-sha256> \
  --receipt-file apply-receipt.json
```

APPLY locks the control singleton first and protects the inspected authority
and history tables. It regenerates preflight inside the transaction and
requires the exact approved hash. It promotes only approved markers, activates
the control at the current UTC time, and stamps explicitly planned clean future
weeks with `persistent_v1`. The selected membership IDs/start/end times stay
unchanged. No student transition is generated.

Before commit, the operation compares every protected row against the exact
allowed marker/control/week-version changes. Any other difference rolls back
the whole transaction. The receipt contains before/after hashes/counts and
attests zero new Teams/memberships. VERIFY checks that receipt read-only before
writers resume. Retrying the applied plan refuses; it does not reset authority.

## Current authority versus weekly rules

Activating current persistent membership and future weekly scoring are separate
decisions. New BOOK/BOARD activity after activation snapshots the effective
operating Team ID. Existing NULL attribution remains NULL. The current open
September 28, 2026 week retains its legacy reader and prior evidence. Finalized
weeks and their rules/results/snapshots are untouched.

Persistent weekly rules start on the approved later Monday. Existing clean
future weeks are explicitly listed/stamped by cutover; later provisioning uses
the activated boundary. Weekly eligibility/scoring uses that ContestWeek's
dates, and finalization freezes the appropriate roster in existing snapshots.
An old week is never silently reinterpreted through today's membership.

Lifetime totals aggregate qualifying immutable earning references through
existing TeamFamily links across weeks. Operating display identity and current
visibility/moderation are explicit, independent of presentation seasons.
Today's membership never rewrites an earlier contribution, snapshot, result,
Medal Board record, or Hall record.

## Rollback

Before activation, additive schema rollout can be disabled and downgraded if
no persistent authority or NULL-origin prospective rows exist. The migration
refuses downgrade once authority, prospective rules, or transitions are in use.

After activation, do not resume old seasonal writers, copy successors, replay
contributions, erase membership changes, or restore old codes. Pause affected
Team operations and deploy a persistent-aware repair/hotfix. Preserve the
manifest, receipt, and backup for review. There is deliberately no automatic
destructive rollback command. Historical attribution repair is outside this
release.

## Supplied inventory caveats

The supplied five candidate source Teams and twenty Back-to-School memberships
must be approved by exact IDs in a fresh manifest; they are not migration
constants. Legacy unended Band Camp memberships remain historical. The genuine
profile-24 St. Louis to The Teachers transition, unresolved report on Team 10,
and all nine existing unattributed BOARD awards remain unchanged. No Halloween
successors or membership copies are required or created.
