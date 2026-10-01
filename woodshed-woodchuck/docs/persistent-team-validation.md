# Persistent Team authority review record

Branch: `fix/persistent-team-authority-v1`.
Exact base: `9a388d6e8c6b212fcbd801dc7823256b10c1a3d7`.
Migration: `p20team001`, following `f19arcade001`.
Validation date: October 1, 2026.

## Scope and operating model

Option B retains existing Team, membership, Family, earning references, results,
and snapshots. Explicit operating/persistent markers distinguish current
identity and effective membership from unended legacy seasonal evidence.
Partial uniqueness and interval guards prevent competing persistent authority.
A disabled migration-seeded singleton separates schema deployment from cutover.
Origin seasons remain nullable metadata with RESTRICT deletion behavior.

Actual join/switch/leave transitions govern weekly corrections. Switch ends the
current interval and starts the replacement atomically. Leave creates no
replacement. Carried membership consumes no correction; leave/rejoin and
administrative removal cannot replenish an already used allowance. Week and
presentation changes create no membership copies.

BOOK/BOARD use earning-time effective authority. Lifetime contributions aggregate
through Family; current display and moderation use the operating Team. Legacy
weeks retain `legacy_seasonal_v1` / `contest_finalizer_v1`. Prospectively approved
weeks use `persistent_v1` / `contest_finalizer_persistent_v1`. The currently open
September 28 week is not retagged. Unknown/incompatible reconstruction fails
closed, and finalized snapshots/results remain stored evidence.

The standalone [cutover runbook](persistent-team-cutover.md) describes exact-ID
preflight, reviewed state/hash, maintenance acknowledgments, one atomic marker
transition, protected-row assertions, and before/after receipt verification.
Preflight rejects current-week legacy choices or endings, including students now
without Teams, rather than resetting allowance or inventing historical events.
No operational cutover was executed. Apply tests used disposable local fixtures.

## Validation results

All test commands used sanitized environment variables, an in-memory or disposable
SQLite database, and an independently initialized local PostgreSQL 16 cluster.
PostgreSQL tests used unique disposable schemas in the guarded local test database.
No Production URL or credential was requested or accessed.

| Run | Result |
| --- | --- |
| Persistent authority, HTTP attribution, private/director HTTP, competition, cutover clocks, cutover, migration, public Hall | 158 passed, 6 skipped |
| Final bootstrap-fence change: authority, HTTP, private HTTP, cutover clocks, existing Teams and director Teams | 65 passed, 4 skipped, 5 exact-base failures |
| Calendar/schema compatibility, retired activation, Family, private-practice follow-up | 128 passed, 11 skipped, 4 exact-base failures |
| Final affected BOARD rewards and director dashboard | 150 passed |
| Broad existing Team/contest/BOARD/practice/dashboard/deletion/privacy/finalizer regressions | 448 passed, 67 skipped, 112 exact-base failures |
| Python syntax for changed files, JavaScript syntax, git diff --check | Passed |

The broad failure IDs match the union of the exact-base regression runs exactly:
40 failures / 278 passes / 65 skips in the first group; 72 failures / 174 passes /
2 skips in the second group (which additionally included the original public Hall
suite). No new failure IDs remained. The final transition recheck's five failures
are in that same baseline set. Four calendar lifecycle variants were separately
reproduced on exact-base main at the same reward-count assertion (`1 > 1`). Their
retirement-related test names were updated, but the unrelated assertion/policy
was not changed. Existing fixture/session/age failures were left untouched.

The focused run preceded the final bootstrap-fence adjustment. The subsequent
transition recheck and clock suite validated that adjustment, including its two
new SQLite/PostgreSQL cases. PostgreSQL exercises real conflicting switches,
switch/deletion races, finite-interval races, waiting cutover requests, and
concurrent cutover. Expected SQLite row-lock concurrency cases are skipped.

## Required behavior coverage

| Requirement | Focused evidence |
| --- | --- |
| 1–4: presentation/week continuity, five existing Teams, legacy unended rows ignored | authority boundary test; exact five-Team/twenty-membership cutover fixture |
| 5, 23: one current membership and concurrency | unique-index/interval tests; PostgreSQL competing switches, finite intervals, deletion race |
| 6: historical switch retained | populated migration and cutover full-column comparisons |
| 7–9: join, atomic switch, leave | authority transitions, rollback injection, actual HTTP leave |
| 10–11: carried correction and leave/rejoin limit | correction tests; private administrative removal; pre-cutover choice refusal |
| 12–15: BOOK/BOARD attribution, no membership, historical NULL preserved | HTTP tests for hours/care/marching/trivia and BOOK; existing nine-NULL fixture assertions |
| 16–17: lifetime Family total and operating display | competition lifetime/moderation and current Hall tests |
| 18–20: frozen history, legacy current week, prospective rules | versioned finalizer retry/snapshot tests; populated migration; real calendar compatibility |
| 21: private/director authorization | join-request approval/removal, ownership/codes, mixed origin contests, optional Hall label |
| 22: account deletion | marked authority termination, unchanged legacy/report evidence, PostgreSQL deletion race |
| 24: ambiguous cutover refused | exact-ID, stale hash, schema tampering, correction history, frozen/future evidence refusals |
| 25: no normal activation/cutover invocation | retired activation without opening DB; no runtime cutover import test |

Additional clock/fence tests cover a request waiting for activation, legacy
bootstrap committing before mutation, authentication refreshed after waiting,
and trivia retry reacquiring the fence before profile/state locks.

## Reproduction test groups

Run from `woodshed-woodchuck` using the repository virtual environment. Set
`DATABASE_URL=sqlite://`, `PYTHONDONTWRITEBYTECODE=1`, a local test-only
`SESSION_SECRET`, and the guarded `WW_BILLING_TEST_POSTGRES_URL` for a disposable
local test database. Do not inherit Production/service variables. Use
`pytest -q -p no:cacheprovider` with these groups:

- Focused: all `tests/test_persistent_team_*.py` and `tests/test_hall_public.py`.
- Calendar/Family/privacy: `test_contest_schema_compatibility.py`,
  `test_contest_week_provisioning.py`, `test_season_team_activation.py`,
  `test_season_team_activation_postgres.py`, `test_team_families.py`,
  `test_private_practice_followup.py`.
- Affected recheck: `test_inc002_board_rewards.py`, `test_director_dashboard.py`.
- Broad regressions: `test_teams.py`, `test_director_teams.py`,
  `test_team_contests.py`, `test_account_deletion.py`, `test_inc002_board_rewards.py`,
  `test_age_screening.py`, `test_director_dashboard.py`, `test_band_director_metrics.py`,
  `test_trusted_verifier_dashboard.py`, `test_contest_jobs.py`, `test_contest_admin.py`,
  `test_contests.py`, `test_team_practice_rating.py`, `test_pristine_practice.py`,
  `test_private_practice.py`, `test_phase8_book.py`, `test_practice_time_precision.py`,
  `test_practice_scoring_repairs.py`, `test_phase9_contest_integrity.py`,
  `test_contest_historical_rule_provenance.py`, `test_team_name_claims.py`,
  `test_team_moderation.py`, `test_team_standings_grouping.py`, `test_board_standings.py`,
  `test_instrument_medal_privacy.py`, `test_band_director_roster.py`,
  `test_band_director_utilities.py`, `test_verifier_relationships.py`.

## Safety and deployment boundary

The original main worktree remains clean. No Production access or write,
operational cutover, deploy, merge, Render change, billing change, or KWS
Production call occurred. Supplied nine NULL BOARD rows and the unresolved Team
report were not touched. Disposable tests additionally prove those fixture rows
are unchanged. No Halloween successor Teams or copied seasonal memberships were
created by the implementation/cutover path. Historical diagnostic fixture tests
remain separate from normal operations.

Before any later deployment, review the branch and migration. Before any later
cutover, obtain a fresh exact-ID manifest and prove the clean correction window,
maintenance controls, and backup. The release itself does not enable authority.
After activation, old seasonal writers are not a valid rollback. The downgrade
refuses used authority; pause Team operations and use a persistent-aware forward
repair while preserving evidence.

## Files changed

- `app/account_deletion.py`
- `app/account_routes.py`
- `app/age_privacy.py`
- `app/band_director_context.py`
- `app/band_director_dashboard.py`
- `app/contest_admin.py`
- `app/contest_seasons.py`
- `app/contest_week_provisioning.py`
- `app/contests.py`
- `app/director_dashboard.py`
- `app/hall_public.py`
- `app/models.py`
- `app/persistent_team_cutover.py`
- `app/practice_chart_routes.py`
- `app/season_maintenance.py`
- `app/season_team_activation.py`
- `app/team_authority.py`
- `app/team_authority_schema.py`
- `app/teams.py`
- `app/trusted_verifier_dashboard.py`
- `docs/canonical-seasons.md`
- `docs/contest-finalization-job.md`
- `docs/contest-week-provisioning.md`
- `docs/persistent-team-cutover.md`
- `docs/persistent-team-validation.md`
- `docs/season-team-activation.md`
- `migrations/env.py`
- `migrations/versions/p20team001_persistent_team_authority.py`
- `static/js/app.js`
- `templates/home.html`
- `tests/test_contest_schema_compatibility.py`
- `tests/test_contest_week_provisioning.py`
- `tests/test_hall_public.py`
- `tests/test_persistent_team_authority.py`
- `tests/test_persistent_team_competition.py`
- `tests/test_persistent_team_cutover.py`
- `tests/test_persistent_team_cutover_clock.py`
- `tests/test_persistent_team_http.py`
- `tests/test_persistent_team_migration.py`
- `tests/test_persistent_team_private_http.py`
- `tests/test_season_team_activation.py`
- `tests/test_season_team_activation_postgres.py`
