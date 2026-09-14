# Canonical durable seasons (Phase 17A)

`app/seasons.py` contains the one bootstrap calendar. Runtime identity and dates
come from `season_covering_date(session, date)`, using durable `Season` records.
Callers convert timestamps to America/Chicago before resolving. An enabled
(`active`) season must cover the date; future, expired, planned and closed rows
cannot become current. Multiple covering active rows are a configuration error,
not an arbitrary winner. Adjacent future seasons may be provisioned as active.
Expiration does not close a season or bypass finalization requirements.

Season ends are **inclusive Sundays**. ContestWeek ends are **exclusive Mondays**.
The approved calendar is centralized in `CANONICAL_SEASONS`; the 2027 Band Camp
has an approved start only, so its end remains null pending a later calendar decision.
BOARD presentation accepts a resolved durable season and owns no dates.

## One-time 2026 development/testing transition

Accumulated Band Camp history is intentionally preserved, including finalized
team results, snapshots, rewards and crowns in the August 24 and August 31 weeks.
For this launch/testing year only, Band Camp runs July 27–September 13, 2026.
Back to School launches September 14 and ends September 27, 2026. Existing weeks
1–7 remain Band Camp records, including the September 7–14 exclusive-boundary week.
The production-shaped repair must report `reparent_weeks: []`; it creates only the
missing Back-to-School weeks September 14–21 and September 21–28.

Later boundaries are unchanged: Halloween September 28–November 1; Holiday
November 2–January 10, 2027; Hibernaculum January 11–March 7; Spring March 8–May 9;
Beach May 10–July 4; the next Band Camp begins July 5, 2027. Future full cycles use
their intended calendar, not a recurring “Band Camp ends September 13” rule.

The earlier proposed August 23/24 boundary is superseded. A database already
repaired to that proposal is a conflicting configuration and still fails closed;
this patch does not automatically reverse ownership changes or rewrite history.

## Bootstrap versus repair

Normal contest setup (`ensure_current_contest_data`; old import name retained for
compatibility) can create a missing date-covered canonical season and current week.
It never truncates existing seasons, reassigns weeks, closes seasons, or moves teams.
Read-only dashboards do not bootstrap data. Provision a fresh database before
serving those pages using the explicit bootstrap operation below.

Maintenance commands default to dry run. `DATABASE_URL` must explicitly identify
the intended environment; do not put credentials in logs or shell history.

```sh
.venv/bin/python -m app.season_maintenance bootstrap
.venv/bin/python -m app.season_maintenance bootstrap --apply
```

Bootstrap validates existing canonical records and creates missing ones. It refuses
overlap or conflicting dates/names. An open-ended legacy Band Camp needs repair,
not implicit startup changes.

## Production-safe legacy repair procedure (not automatic)

1. Run `.venv/bin/python -m app.season_maintenance repair` against the intended
   database with no `--apply`. Review the complete dependency/ownership report.
   For the 2026 production transition, require `reparent_weeks: []` and existing
   weeks 1–7 still owned by Band Camp. Any different plan needs human review.
2. If `safe` is false, **stop**. Do not delete results, clear snapshots, rename teams,
   or move memberships to make it pass. Resolve ambiguous history with its owner.
3. Schedule a maintenance window, pause web writers and finalization jobs, and take
   a verified database backup. Retain before-state season/week IDs and history counts.
4. Repeat the dry run under that maintenance window. Only when safe, run
   `.venv/bin/python -m app.season_maintenance repair --apply`.
   Apply rechecks the plan under a SQLite immediate transaction or PostgreSQL table
   locks; any failure rolls back. Unsupported database locking semantics abort.
5. Rerun the dry run; it should propose no changes. Verify foreign keys, week IDs,
   result/snapshot/grant/crown identity, unchanged historical teams/memberships, and
   agreement across Parent, Band Director, contests, teams, admin and BOARD.
6. Resume writers/jobs only after verification. This repair does not finalize weeks,
   grant rewards, close seasons, or introduce a new team carry-forward policy.

The only automatic existing-row corrections are the known active open-ended
`band-camp-2026` end and wrongly owned, complete Monday-to-Monday weeks. Existing
week IDs, deadlines, statuses, and finalization stamps survive. Missing canonical
seasons and Back-to-School weeks are created idempotently. Frozen weekly student or
instrument results, no-team snapshots, and compatible destination-team snapshots
may survive reparenting unchanged. Frozen old-season team links, incompatible
membership snapshots, unknown/season-dependent contest results, duplicate dates,
cross-boundary weeks, unaudited foreign keys, or director-contest windows beyond
Band Camp cause an abort. Reward/crown linkages are reported and never rewritten.

Source P-Charts and activity point ledgers keep their original earning-time team
attribution, even when that team remains historical Band Camp data. They are not
membership carry-forward; no destination team is inferred. Current-season team
standings use destination-season teams. All student practice/earning records remain.

## Administrative rollover

### Team identity foundation (H1A)

`Team` remains seasonal. `TeamFamily` is persistent identity only (`id`,
`created_at`); each Team has a required family, unique together with its season.
Names, emblems, creator, visibility, director status, join codes and moderation
remain on Team. H1A assigns one distinct family to every existing Team, without
inferring historical lineage from names, creators or emblems. It performs no
cross-season continuation or roster copying. Hall/lifetime grouping is unchanged.

The H1A migration requires paused writers, including old application processes
that cannot supply `family_id`. SQLite uses the dedicated migration connection
with foreign-key enforcement off for parent-table batch recreation, then checks
referential integrity before completion; application FK settings are not changed.
PostgreSQL locks Teams for the migration. Downgrade refuses if multiple seasonal
Teams share a family, because removing the identity would lose continuity.

### Explicit continuity engine (H1B; not activated)

`app/team_continuity.py` provides `plan_team_continuity` (read-only) and
`apply_team_continuity` (fresh locked re-plan, caller-owned commit/rollback).
Both accept source/destination season IDs, not writable dry-run instructions.
They require a clean unit of work. No route, startup hook, or calendar resolver
calls apply. Explicit H2B maintenance and the operational season activation job
reuse this engine; see [season team activation](season-team-activation.md).

Adjacent seasons with the same configured timezone use destination local midnight
as the boundary (September 14, 2026 in Chicago is 05:00 UTC). A source membership
must start strictly before the boundary and end strictly after it or remain
unended. A future boundary is REVIEW: its roster is not yet known. The optional
server-side `now` argument supports deterministic tests, not client authorization.
An exact-boundary ending is REVIEW, never inferred. Inactive students,
overlapping boundary memberships and inconsistent season/team references fail
closed. Membership continuation sets the Monday **containing** destination start,
counts as the initial selection, and leaves one correction that week. Ordinary
switching resumes the following Monday. Source memberships are never ended/edited.

SAFE public teams need active moderation and at least one safely represented or
continuing roster member; an empty team is REVIEW. A null creator is allowed.
Private director teams may be empty but need a current active owner with the
director capability. They get new globally unique codes; pending requests and
reports stay historical. Non-active moderation never automatically continues.

New seasonal Teams share the source family, copy social fields, and receive new
IDs/creation timestamps. Name, emblem, public-creator and same-family identity
collisions are CONFLICT; nothing is renamed or merged. Any destination membership
history wins. An exact same-successor/boundary/Monday row is already represented
(including a later-ended row), not recreated. This is a no-op recognition rule,
not provenance inference or permission to rewrite that row. Team/member decisions
have separate classifications and machine-readable reasons: a SAFE Team can carry
its SAFE members while conflicting members are left alone. If no public roster
can be safely represented, no empty successor is created. Aggregate plan status
also reports member conflicts. No historical families are merged.

PostgreSQL apply requires READ COMMITTED and sorted locks: Seasons, TeamFamilies,
Teams, profiles, memberships. Team creation/selection, private request management,
and moderation acquire the same season fence before decisions. Account deletion
fences all seasons before its cross-season writes; capability revocation locks the
owner profile. SQLite uses BEGIN IMMEDIATE before reads when no physical transaction
is active. Enter apply with a fresh transaction, not an earlier SQLite read snapshot
or unrelated row locks. Constraints remain the backstop; a database error requires
rollback of the complete caller transaction before retry. No external calls occur.

Closed destinations, frozen weekly results/snapshots, and destination director
contests require review. The normal finalizer now takes the shared season fence;
these checks do NOT synchronize arbitrary SQL writers. H2 maintenance still
pauses finalization/calendar writers (including in-flight work)
for backdated application. Same-season ordinary team writers are serialized, but
direct SQL/imports must follow the fence or be paused too. Moderation/capability
changes after a completed continuation retain their existing seasonal behavior;
H1B does not propagate later policy changes through a family.

H2B exposes a read-only plan and separately authorized apply under maintenance.
The explicit operational job now provides read-only prospective preflight and
whole-transition activation at/after local midnight, requiring zero REVIEW and
zero CONFLICT. It does not wait for source closure or not-yet-due finalization.
Source finalization can occur later without rewriting historical memberships.
If the stored source finalization deadline is already due, finalize normally
first. Job scheduling and a boundary traffic pause must be configured separately;
no web request runs activation. See the operational document linked above.
No H1C attribution repair, result/award/snapshot rewrite, Hall identity conversion,
join-request carry, or automatic runtime activation is implemented here.

### Existing rollover behavior (unchanged)

Existing rollover checks still require an ended source and finalized source weeks,
reject overlap and partial weeks, and preserve history transactionally. Rollover can
reuse an exactly configured pre-provisioned destination and its existing weeks.
`active_season` in status means date-covered current season; `rollover_source`
separately identifies the ended source awaiting closure. Prior-week finalization
remains available without labeling an expired season current.

Seasonal rewards, special rules, artwork, team carry-forward, and historical Pro
analytics are outside Phase 17A.
