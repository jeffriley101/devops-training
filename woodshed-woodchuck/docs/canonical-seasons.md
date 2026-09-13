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

Existing rollover checks still require an ended source and finalized source weeks,
reject overlap and partial weeks, and preserve history transactionally. Rollover can
reuse an exactly configured pre-provisioned destination and its existing weeks.
`active_season` in status means date-covered current season; `rollover_source`
separately identifies the ended source awaiting closure. Prior-week finalization
remains available without labeling an expired season current.

Seasonal rewards, special rules, artwork, team carry-forward, and historical Pro
analytics are outside Phase 17A.
