# Pre-beta activity report

## Scope and sources

`GET /admin/analytics` uses the existing `require_site_admin` guard. Sign in at
`/admin/login` with the configured site-admin credential and follow **Pre-beta
activity** from Memberships. A contest-admin, student, parent or verifier session
alone does not grant access. The report performs SELECTs only and sends
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

The population is **all non-deleted student profiles**, including any staff or
test accounts. The repository has no pre-beta cohort field. This is a small-group
report, with a fixed 30-calendar-day window including today, not a cohort system.

| Question | Existing authoritative source |
| --- | --- |
| Account count and creation | `woodchuck_profiles.created_at`, excluding deleted profiles |
| Daily sign-in activity | `reward_grants` with `category_key=login-streak` and `reward_type=dandelion`; this already records daily login rewards, including visits with a persistent session |
| Practice saved and credited duration | `practice_charts.created_at`, source and existing `chart_seconds_sql()` calculation |
| Arcade started/completed and open plays | `arcade_play_sessions.started_at/completed_at`, grouped by game |
| Quest/trivia use | `quest_completions.completed_at`, `daily_trivia_attempts.created_at` |
| Board claims | `camp_point_awards.created_at`, limited to `hours`, `care`, `marching`; excludes automatic contest-placement awards |
| Store purchases | `owned_item_copies.acquired_at` where `acquisition_source=store`; excludes Mum gifts |
| Contest opt-in | Saved charts' existing individual/team opt-in flags; does not assert eligibility or placement |
| Reward/contest context | Existing reward grants and student contest medal rows created in the window; excluded as independent evidence of activity because they can be automatic |

High scores are existing best-score snapshots, not a complete play history. The
report uses the more useful start/completion records instead. Quest duration is
not added to practice duration. Billing/membership audits, account state blobs,
free-text practice notes, trivia answers, public sign-in IDs and names are not read
into the report. The report does not create login, practice, score, contest,
purchase or reward events.

## Two new observations

| Event | Exact hook | Meaning |
| --- | --- | --- |
| `arcade_entered` | Successful authenticated render of `/arcade` | First arcade entrance recorded for that student/Central date |
| `pristine_entered` | Successful authenticated render of `/practice/pristine` | First Pristine Practice entrance recorded for that student/Central date |

These are the only newly observed behaviors. No event means a game was played or
practice occurred. Both require the existing server-side `current_profile`
resolution in the page renderer. Names and identity cannot be supplied via query
parameters or JSON; there is no event-ingestion endpoint. A person can still
deliberately visit their own pages: these are observation clues, not fraud-proof
metrics or a reward input.

`analytics_events` contains only `id`, numeric `profile_id`, constrained
`event_type`, UTC `occurred_at`, and Central `activity_date`. There is **no metadata
field**. A unique constraint on `(profile_id, event_type, activity_date)` both
minimizes collection and handles concurrent reloads. Its profile-leading index
also supports later deletion by profile ID. The timestamp index supports the
report window. The profile foreign key cascades on physical deletion.

Migration: `u1q2r3s4t5u6_add_analytics_events.py`, revision `u1q2r3s4t5u6`, parent
`t0p1q2r3s4t5`. Upgrade creates only the table and its index/constraints. Downgrade
drops only this analytics table/index and loses its observations. No historical
data or existing schema is modified by this migration. No backfill is attempted.

## Definitions and limitations

- **Active:** at least one recorded activity in the source table above, or one
  of the two new observations. Account creation, grants other than the existing
  daily-login ledger, and contest results alone do not make a student active.
- **Returning:** active on at least two distinct Central dates within the
  displayed 30-day window. This is not lifetime retention or a cohort rate.
- **Today / last 7 days:** distinct active profile IDs across those Central
  calendar dates, including today up to the report time.
- **Practice:** chart submissions saved in the window, including backdated
  practice. Duration uses the existing authoritative seconds calculation;
  ordinary and Pristine charts remain additive. Counts are charts, not distinct
  real-world sessions or verified-practice claims.
- **Workflow clues:** student/date pairs with an entry but no corresponding
  game start or Pristine chart save that day. These are not conversion funnels
  or proof of abandonment. Today is incomplete; browsing, cross-midnight work,
  resuming older plays, missing best-effort observations and direct game
  navigation limit inference.
- **Game completion table:** games started inside the window, with the number
  of those completed by report time, and still-uncompleted starts at least one
  hour old. The feature table separately counts completions in the window,
  including those started earlier.
- **Last observed:** latest evidence within the window. Daily entry records
  retain their first timestamp, so this is not an exact last visit.
- **Recent activity:** latest 50 source observations, displaying only Central
  timestamp, internal numeric ID and fixed activity label.

UTC storage and America/Chicago presentation reuse `SEASON_TIMEZONE`. Naive
SQLite timestamps are interpreted as UTC, as elsewhere in the app. Calendar-day
boundaries account for daylight saving time. Before collection begins, durable
records provide partial historical activity; newly observed entries have no
historical coverage. The report scans only selected activity columns inside the
30-day window; the per-student table includes all non-deleted accounts. Revisit
query volume and pagination if the user population grows substantially.

## Failure isolation and operations

The two page renders finish their existing database sessions and build their
normal response before attaching a Starlette response background task. It runs
after the response body is sent, in the existing framework thread pool. It opens
its own short session, checks the profile is still active, and commits only an
analytics row. It never receives a primary application Session, modifies the
cookie, or participates in a practice/reward/game transaction. An existing
response background task is left alone and the observation is skipped.

Scheduling/write exceptions are caught only within analytics. Write failure
pauses attempts for five minutes per worker. A warning is emitted at most once
per five minutes per worker, with operation and exception class only; no SQL,
exception payload, profile ID or credentials. There are no retries of a failed
event, durable queues or delivery guarantees. A restart or failure can lose an
observation. A missing analytics table leaves both observed pages working; the
admin report still queries authoritative tables in a separate transaction and
clearly labels entry observations unavailable. Core database failures retain
the application's existing behavior.

Set `WOODSHED_ANALYTICS_ENABLED=0` in the server environment to disable new
recording. The dashboard remains read-only and retains historical observations.
This switch does not affect business logic. It is optional and defaults to on.

## Privacy and deletion

No IPs, user agents, fingerprinting, session tokens, PINs, payment fields, audio,
request bodies, browser payloads or free-form metadata are collected. Numeric
profile IDs are still linkable student data and the report is internal only.
Deleted accounts are excluded from every report query and from new observations.

Existing account deletion **anonymizes** the profile rather than physically
deleting it. This change intentionally does not alter that workflow: its dated
analytics rows remain stored until explicitly removed. A future authorized
deletion can directly delete `analytics_events` rows filtered by `profile_id`;
the current foreign key also supports physical deletion. There is no automatic
retention/purge subsystem in this change.

## Local/staging review for Jeff

1. Use a disposable local database or staging environment. Review the additive
   migration; apply it there only when ready. Production migration and deployment
   remain Jeff's separate decisions.
2. Sign in normally; open Arcade and Pristine Practice twice. Confirm the normal
   pages render, then inspect one row of each type for the internal student ID
   and Central date. Opening a game itself adds no new analytics event.
3. Complete a game and save Pristine Practice; verify scores, balances, exact
   saved duration and the Book behave normally. Return on a later Central day.
4. Sign in at `/admin/login`; open `/admin/analytics`. Verify activity, practice,
   game completion, dates, return counts and the limits explained on the page.
5. In signed-out, student-only and verifier-only browsers, confirm the report
   returns 403. Adding `profile_id`, `student_id` or `event_type` to observed page
   URLs must not change the recorded identity or event.
6. On a **disposable** database, run against the prior migration (or deny access
   only to the analytics table). Both observed pages and real game/practice/login
   actions must continue. The admin page should flag missing entry observations,
   and logs should contain at most one sanitized analytics warning per five
   minutes per worker. Restore the local migration afterward. Also check the
   recording-disable switch.

Focused automated checks:

```sh
.venv/bin/pytest -q tests/test_analytics.py tests/test_analytics_migration.py
```

## Implementation verification

Before edits, the relevant baseline passed **82 tests** across Pristine Practice,
arcade economy, site-admin CSRF, page rendering, login streaks, practice-time
precision and account deletion. After implementation, those same tests plus
**34 new analytics tests all passed (116 total)**. The new tests cover identity
spoofing, allowed types, daily/concurrent deduplication, administrator access,
read-only SQL, authoritative sources and duration, Central/DST/window boundaries,
recent-record limits, missing-table/commit/scheduling failures, response-before-
write ordering, disabled recording, and the additive migration round trip.
Synthetic desktop and narrow-screen reports were checked in local headless Chrome.
PostgreSQL migration SQL was compiled; the migration round trip used disposable
SQLite, not a production database.

The unchanged full-suite baseline was **1,394 passed, 152 skipped, 10 failed**.
The existing failures were recorded before modifying the repository:

| Test file | Existing failing test(s) |
| --- | --- |
| `test_board_standings.py` | `test_live_scoreboard_javascript_uses_actual_ranks_and_preserves_ties`; `test_live_leaderboard_rows_show_numeric_scores_without_repeated_units` |
| `test_contests.py` | `test_existing_finalized_student_scores_are_not_rewritten` |
| `test_phase5_persistence.py` | `test_contest_models_match_approved_foundation` |
| `test_phase6_mobile_assets.py` | `test_rendered_pages_use_one_current_stylesheet_version` |
| `test_phase6a.py` | `test_book_actions_precede_spiral_and_metallic_statistics_page` |
| `test_phase6c_audio.py` | `test_tone_is_exactly_pinned_local_licensed_and_loaded_in_order` |
| `test_production_hotfix.py` | `test_instrument_assets_are_cache_busted_and_failures_are_visible` |
| `test_team_continuity.py` | `test_join_code_collision_and_no_automatic_activation` |
| `test_trusted_verifier_dashboard.py` | `test_shared_metrics_completed_rating_and_private_note_isolation` |

These unrelated expectations/behaviors were not changed as part of analytics.

The final full-suite run (`.venv/bin/pytest -q`) was **1,428 passed, 152 skipped,
10 failed**. Its exact failing-test set matches the baseline above: no new
failures, with 34 additional passing tests. `git diff --check` also passed.

**No production migration performed. No production deployment performed.**

## Separate follow-ups (not implemented)

- **Product / Operations:** identify which internal IDs are actual testers;
  review workflow clues alongside direct observations; choose a review cadence.
- **Security:** review who holds site-admin credentials and whether existing
  operational access controls remain appropriate for student activity reports.
- **Legal / Compliance:** review notice/consent requirements for the tester
  group, retention duration and handling of analytics during account deletion.
- **Marketing:** no external sharing or marketing integration is included;
  consider only appropriately reviewed aggregate findings later.
- **Leadership:** decide whether the observed usage supports proceeding beyond
  pre-beta; approve any production migration/deployment separately.
