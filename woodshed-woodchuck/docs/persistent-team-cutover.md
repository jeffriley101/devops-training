# Persistent Team authority v1

This release implements Option B. One explicitly approved existing Team row
per TeamFamily becomes the operating Team. Its ID, Family, origin-season
metadata, moderation, visibility, creator, emblem, permanent name claim,
reports, and join code are retained. No seasonal successor Team or membership
is created. Seasons provide artwork, calendar labels, and historical grouping;
ContestWeek governs weekly competition.

## Schema deployment is not cutover

Revision `p20team001`, based on `f19arcade001`, defaults every authority marker
to false and every existing ContestWeek to `legacy_seasonal_v1`. Revision
`p21team001` adds a staged plan/hash/effective boundary to the disabled singleton
`persistent_team_control`, plus an explicit frozen-roster marker on ContestWeek.
Neither migration identifies operating rows, stages a plan, or activates
membership authority. Current authority remains legacy until the separately
reviewed boundary operation.

The compatible calendar and PTA operators accept exactly one installed revision,
`p21team001` or its reviewed additive extension `c22class001`. PLAN, APPLY,
ACTIVATE and VERIFY all retain structural validation; the installed stamp alone
does not grant access. Existing required columns, staging/authority checks,
partial unique indexes, restrictive origin-season foreign keys and exact
membership-interval trigger checks remain in force. The c22 extension additionally
requires all eight Classroom tables with their required column types/nullability,
primary keys, CHECK constraints, restrictive foreign keys (including composite
scope), unique constraints and indexes with their partial predicates. Older or
unknown revisions, arbitrary descendants, multiple heads, missing revision rows,
and incomplete or falsely stamped c22 schemas refuse. There is no bypass flag.

This support is independent of Classroom enablement. `c22class001` installs empty
disabled relationship tables without selecting PTA authority or changing existing
rows. Historical `team_preflight`/`team_continuity_repair` keep their separate
`d17contest001` restriction and refuse p21 and c22; `team_activate` stays retired.

Compatible operator code must be available **before upgrading a staged p21
database to c22**. An old pinned binary still refuses c22, including ACTIVATE and
VERIFY, while the due-boundary runtime fence can remain active. A source fix alone
does not update separately installed operator binaries. Coordinate their approved
rollout before the schema upgrade; do not clear the stage or move the boundary to
work around an incompatible operator. This patch does not approve deployment.

Dormant deployment is not behavior-neutral: origin-season deletion is restricted
for historical rows too; guarded writers take the authority fence; and ordinary
seasonal Team activation is retired. The lower-level seasonal continuity copy
service also refuses a database containing the singleton seeded by p20, even
while persistent authority is disabled. Read-only historical inventory remains
available; the repair planner and `team_preflight` retain their restricted schema
approval. The new schema must precede the new runtime.

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
current open week and a clean later Monday for persistent weekly rules. The
closing and starting ContestWeeks must already exist and be included in the
reviewed inventory; PLAN/APPLY/ACTIVATE do not create them. An
ambiguous approved Family is a refusal, never an automatically chosen Team.
Historical-only Families may remain without operating authority. Their legacy
rows remain visible in the reviewed inventory and cannot compete as current
authority merely because a seasonal membership is unended.

Every approved membership must start strictly before the future Central Monday
boundary. It becomes carried membership for that new ContestWeek and consumes
no correction. Selection changes in the closing week remain closing-week
history. They invalidate a staged approval rather than causing automatic
substitution or a fabricated transition ledger. Review a fresh exact-ID plan
after such a change; activation rechecks that approved state under its fence.

The manifest exports selected Team/membership/request rows, hashed join-code
state, protected-table counts/hashes, target identity without credentials,
planned future-week IDs, and a canonical SHA256. Private chart notes, PIN hashes,
and cleartext codes are not exported. PLAN uses repeatable-read/read-only
PostgreSQL or a query-only existing SQLite file; it has no writable fallback.

An installed revision and an immutable plan's PTA contract are separate facts.
New p21 plans keep their existing format. New c22 plans record
`content.revision = c22class001` and
`content.pta_contract_revision = p21team001`; they do not claim that the installed
schema is p21. The original canonical hash, exact selected IDs, database/schema
target and authority fingerprint remain the approval. Compatibility does not
authorize editing the approved file or substituting a fresh hash.

| Approved artifact | Compatible operator behavior after an additive p21 -> c22 migration |
| --- | --- |
| Unstaged p21 plan supplied to APPLY | Refuse `plan_schema_revision_changed` before writes; create a new c22 PLAN and obtain fresh approval. |
| Exact p21 plan already staged on p21 | ACTIVATE and immediate VERIFY retain its original file, hash, boundary and stored stage if approved authority and all existing checks pass. |
| Activation and receipt already created on p21 | VERIFY accepts the original plan/hash/receipt only if actual activation and every originally protected evidence row remain exactly unchanged. |
| New c22 plan | PLAN -> APPLY -> ACTIVATE -> VERIFY uses the same exact-ID approval, target, acknowledgments, boundary, locking and rollback requirements as p21. |

Existing receipts retain their original protected-table coverage. They do not
retroactively attest to Classroom tables installed later. Compatible APPLY and
ACTIVATE separately snapshot and compare all eight Classroom tables within their
mutation transactions and roll back an unexpected change; ordinary calendar apply
does the same. This preservation evidence does not rewrite a plan, stored stage
or receipt, and operator actions grant no Classroom authority or access.

Boundary revalidation distinguishes approved authority from ordinary new earning
activity. A change to identity, ownership, moderation, membership,
pending-request, account-status, or reviewed week inventory requires a newly
reviewed plan. The authority fingerprint also includes each Season's ID, key,
start/end dates, timezone, and status because legacy Team selection depends on
that calendar. Season display names and audit timestamps are outside this
authority fingerprint; the full mutation evidence still includes those rows.
Regenerate and review plans prepared before this Season fingerprint was added.
An older staged approval fails revalidation even if its selected IDs still match.
Normal BOOK/BOARD activity
while the plan is staged may continue under legacy rules and does not authorize
any new membership or a repair to old NULL attribution. Protected history is
compared within each mutation transaction so the operation cannot silently
rewrite those rows.

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

## Staging and the single effective boundary

APPLY stages an approved future Monday and its manifest/hash. It does not promote
Teams, memberships, or requests; activate control; or stamp persistent week
rules. Legacy join/switch behavior therefore remains real legacy behavior before
the boundary. Changing approved membership after staging causes later activation
to refuse; the tool never substitutes a newly discovered row.

The boundary is midnight Monday in `America/Chicago`, converted to UTC using the
timezone's offset on that date. At that instant, new Team/competition operations
fail closed with a temporary 503 until the explicitly invoked ACTIVATE operation
succeeds. Staging is not a scheduler and season activation never invokes cutover.
If the approved state is stale, keep the boundary blocked until a
new exact-ID plan is reviewed and staged; do not resume competing seasonal writes.
Activation must occur before the next Monday. A staged boundary that expires
requires separate operational review; the tool does not backdate activation over
post-boundary activity or silently move the approved boundary.

BOOK/BOARD writes acquire the control mutex before choosing an effective instant.
The shared service fixes that instant for one logical operation. Nested service
calls and calls using the default clock retain the admitted instant in the same
transaction and savepoint scope. Practice-date validation, Team attribution,
BOOK `created_at`, and BOARD `occurred_at` and
`created_at` use that same instant. A Sunday operation admitted before midnight may
commit after midnight, but remains Sunday evidence and cannot submit Monday
practice. ACTIVATE waits for the writer to commit or roll back before inspecting
its evidence. A caller blocked on the mutex is admitted only after the lock is
acquired; a fresh sensitive operation then receives 503 if activation is pending.
Commit, rollback, or a change of savepoint scope invalidates the cached instant;
a subsequent operation must pass admission again. A service caller supplying a
different explicit `at` starts another logical operation within its caller-owned
transaction. It revalidates the boundary and activation time under the mutex
already held. This supports batches with different earned dates and multiple
Team transitions without letting a Monday operation inherit Sunday's legacy
admission. An explicit time before activation cannot re-enter legacy authority
afterward.

Finalization, applied historical repairs, and director finalization also
validate any explicit operation time through admission. Repair
rewards retain the original ContestWeek `finalized_at` as their earning time;
that historical timestamp does not serve as permission to perform the repair.
BOARD RewardGrants use the same earning instant as their CampPointAward.

The gate follows the persisted operation's competition scope:

- A P-Chart with either `include_contests` or `include_team_contests` true is
  sensitive, even when `team_id` is NULL. Creation and verification responses use
  the shared service gate, including non-HTTP callers.
- A chart with both flags false is independent private evidence. Pristine and
  private family practice are normalized to that scope. Their creation and
  review still acquire the common mutex for lock ordering, but remain available
  while activation is pending. Such charts do not count as post-boundary
  competition evidence in ACTIVATE's preflight or as qualifying Team reward
  evidence. Team practice qualification requires approved BOOK evidence with
  both inclusion flags true. Family practice, parent, and
  private director pages request practice metrics without current Team context;
  their calendar grouping uses only the presentation season and remains available.
- Team membership/identity changes, account deletion, BOARD awards, contest
  provisioning, and finalization remain authority-sensitive. Current displays
  that consult Team authority also return 503 while the boundary is pending.
  Historical readers that use only stored results/snapshots do not promote
  authority or acquire a new weekly rule by reading them.

Mutation commands require the approved file/hash and explicit maintenance/backup
acknowledgments. Those acknowledgments attest to operator actions; they do not
pause the application or jobs themselves.
The supported writers' boundary correctness comes from their mutex and effective
instant, so draining them before Monday is not the concurrency mechanism. The CLI
retains conservative maintenance acknowledgments. A quiet maintenance window is
advisable for collecting and verifying an exact receipt before ordinary activity
changes its evidence. Do not attest that writers/jobs are paused unless they are.

ACTIVATE has no application-configured lock timeout. A stuck database transaction
can block it indefinitely. Identify the blocking transaction (for example with
PostgreSQL `pg_blocking_pids` and `pg_stat_activity`) and have its owner roll it
back, or have the database operator terminate that backend. Retry ACTIVATE only
after the blocker is resolved. Keep the verification window quiet until the
activation receipt has been verified. The timeout settings used by disposable
test databases are not production safeguards.

```sh
python -m app.persistent_team_cutover apply \
  --database-url-env CUTOVER_DATABASE_URL \
  --plan-file reviewed-plan.json --approved-sha256 <reviewed-sha256> \
  --backup-taken --writers-paused --finalization-paused --maintenance-mode \
  --confirmation 'STAGE PERSISTENT TEAM AUTHORITY' > stage-receipt.json

# At or after the reviewed Monday; VERIFY before resuming writers.
python -m app.persistent_team_cutover activate \
  --database-url-env CUTOVER_DATABASE_URL \
  --plan-file reviewed-plan.json --approved-sha256 <reviewed-sha256> \
  --backup-taken --writers-paused --finalization-paused --maintenance-mode \
  --confirmation 'ACTIVATE PERSISTENT TEAM AUTHORITY' > activation-receipt.json

python -m app.persistent_team_cutover verify \
  --database-url-env CUTOVER_DATABASE_URL \
  --plan-file reviewed-plan.json --approved-sha256 <reviewed-sha256> \
  --receipt-file activation-receipt.json
```

Both staging and activation lock the control singleton before authority and
contest tables. PostgreSQL requires an actual READ COMMITTED transaction before
the first authority read; autocommit and stronger isolation settings are refused.
The table locks use SHARE ROW EXCLUSIVE mode and exclude practice charts,
activity awards, profiles, and ordinary reward/economy tables. Plain reads can
continue. Unrelated profile/economy writers do not need to wait for blanket table
locks on those records.

Supported writers follow the common order: control mutex, season fence when
needed, profile/state locks, then chart, membership, verification, or reward
writes. Private director review acquires the control mutex before its consent,
permission, and connection locks, and before calling the shared review service.
The service APIs own the gate; adding an HTTP guard alone is insufficient for a
new writer. A caller that already owns a profile or data lock must not acquire
the control mutex afterward.

ACTIVATE is forbidden before the approved Monday. At or after that instant it
re-reads the current state under the same fence and revalidates exact approval.
Before promoting anything, it preserves the closing legacy week's original
end-roster evidence in TeamWeekMembershipSnapshots, including an explicit marker
when the roster is empty. It then promotes only approved authority markers,
activates control at the approved effective boundary, and stamps the corresponding
clean ContestWeek and planned prospective weeks with `persistent_v1`, in one
transaction. The selected membership IDs/start/end times remain unchanged, and
no student-choice transition is generated.

Before commit, each operation compares its full evidence rows against the exact
allowed changes. Narrower table locking does not narrow that comparison. An
unrelated write visible between the before/after snapshots can therefore cause
`unexpected_row_change` and a complete rollback. Retry after the conflicting work
finishes; an authority change still requires fresh approval. Lock/serialization
failures also roll back the complete operation rather than retrying an internal
fragment. Receipts record the before/after evidence. A later ordinary write can
make an exact receipt comparison fail even when activation committed correctly;
this is why operators should keep the verification window quiet.

VERIFY independently checks actual activation, approved
authority, week rules, and closing-roster evidence in addition to the receipt;
a receipt describing an unchanged, inactive database is insufficient. Repeating
an activated plan cannot reset authority or duplicate roster evidence.

VERIFY is an exact activation/receipt check, not an evergreen health check.
Additional weeks, membership changes or ordinary practice/reward activity may
invalidate its exact comparison, including after a c22 migration.
`post_activation_state_changed: do_not_reapply` and other relevant refusals remain
valid outcomes; never recreate the old receipt or automatically activate again to
make verification pass. A schema-only additive upgrade cannot excuse a real
change to protected rows. Keep separate preservation evidence for new Classroom
tables rather than claiming that a pre-c22 receipt covered them.

## Attribution and competition use one authority

Current Team identity, effective membership, join/switch/leave, BOOK/BOARD
earning attribution, weekly roster selection, and weekly competition all change
at the same approved boundary. An admitted operation uses its fixed effective
instant to choose the side of that boundary; commit time does not move its
earning evidence into the next week. New sensitive operations at or after the
boundary use persistent authority or fail closed while activation is pending.
Existing NULL attribution remains NULL. The September 28, 2026 week keeps its
legacy reader; finalized weeks and their rules/results/snapshots are untouched.

For effective membership, intervals are `[started_at, ended_at)`: the old
membership is not effective at an explicit switch instant, and the replacement
is effective at that instant. The closing legacy roster is captured before any
such persistent mutation and read from its frozen evidence even if finalization
runs later. A switch at exact Monday therefore cannot remove a closing-week
recipient or emblem. This preserves the legacy reader's original sampling rule;
it does not reinterpret the closing week using the new half-open weekly reader.

A Monday P-Chart may describe Sunday's practice. That new contribution belongs
to the prior ContestWeek: use its frozen roster if available (including original
finalized snapshots), or its persistent end-of-week membership immediately before
Monday. A new Monday Team selection cannot supply a Team for Sunday's competition.
An empty frozen roster produces NULL attribution, and retries keep the original
stored chart attribution. BOARD activities require the current server date.

Lifetime totals aggregate qualifying immutable earning references through
existing TeamFamily links across weeks. Operating display identity and current
visibility/moderation are explicit, independent of presentation seasons.
Today's membership never rewrites an earlier contribution, snapshot, result,
Medal Board record, or Hall record. Public Hall aggregates also remain visible
for historical-only Families. Historical publication restrictions apply first.
A public operating Team may supply the displayed name, emblem, and Team ID. If
that successor is private or hidden, the published achievement retains its
historical public Team identity. A public successor cannot republish a result
whose historical Team is private or hidden. These are presentation decisions;
stored result and earning-attribution IDs remain unchanged.
Stored Team medal aggregates retain the existing Team-identity publication policy.
Individual and instrument Hall results continue to apply their own contributor
privacy checks; showing a permitted Team aggregate does not publish those results.

## Rollback

Before staging, additive schema rollout can be downgraded if no persistent
authority or NULL-origin prospective rows exist. The migrations refuse downgrade
while a plan is staged or once authority, frozen-roster evidence, prospective
rules, or transitions are in use. Do not clear the stage or move its boundary to
bypass a stale-plan refusal; obtain a newly reviewed plan instead.

After activation, do not resume old seasonal writers, copy successors, replay
contributions, erase membership changes, or restore old codes. Pause affected
Team operations and deploy a persistent-aware repair/hotfix. Preserve the
manifest, receipt, and backup for review. There is deliberately no automatic
destructive rollback command. Historical attribution repair is outside this
release.

Classroom remains default OFF. Independent approval of new-mailbox ownership
proof, real Program-provisioning authorization, restoration/retention handling,
feature enablement and deployment remains outstanding. Local compatibility
rehearsals do not establish Classroom, all of S1 or Production release readiness;
codes, trials, enrollment, reporting and Guest Classroom work are outside this
operator patch.

## Supplied inventory caveats

The supplied five candidate source Teams and twenty Back-to-School memberships
must be approved by exact IDs in a fresh manifest; they are not migration
constants. Legacy unended Band Camp memberships remain historical. The genuine
profile-24 St. Louis to The Teachers transition, unresolved report on Team 10,
and all nine existing unattributed BOARD awards remain unchanged. No Halloween
successors or membership copies are required or created.
