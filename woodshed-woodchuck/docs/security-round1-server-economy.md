# Woodshed Woodchuck — Cumulative Security Round 1 Report

Date: September 15, 2026. This cumulative report preserves the economy-only phase evidence below and appends the authorized continuation (authentication throttling, production sessions, authorization, and sensitive-data review). Continuation status supersedes earlier queued-phase statements.

## Outcome and limits

The original attack was reproduced locally on unchanged main: submitting
`progress.credits = 999999` through authenticated state synchronization returned
200, then an otherwise unaffordable UFO purchase returned 201 with balance 998999.

The proposed backend changes preserve the saved balance during signup/state
synchronization and serialize balance changes with revision checks. Practice and
Board earnings previously calculated in JavaScript now have backend award paths;
simply discarding their browser credits would have disabled legitimate earnings.

**Status: generic synchronization vulnerability fixed locally, subject to the
recorded regression evidence below. Overall economy assurance remains PARTIAL.**
This is not deployed protection. Existing game scores and reports of real-world
practice/activity remain client assertions. The server decides their allowed
amounts and limits, but cannot establish that the activity actually happened.
Historical balances are preserved, not certified or reconstructed. See the exact
remaining dependencies below. No beta clearance is asserted.

At the end of the economy-only phase, authentication throttling, production-session hardening, and the broad cross-user authorization matrix were queued. Their continuation results appear later in this same report.

## Repository and production safety

- Actual Git root: `/home/geph/Training_scripts`.
- Starting branch: `main`.
- Verified starting/main HEAD: `efee4d1f3a79ed57505c6b7db897968dd956e256`.
  It had not advanced from the supplied reference.
- Isolated worktree: `/home/geph/Training_scripts-security-round1-server-economy`.
- Application directory: `/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck`.
- Security branch: `security/round1-server-economy`; created from that local main.
- Initial isolated worktree: clean; no staged, unstaged, or untracked files.
- Original checkout had only these two untracked project files:
  `woodshed-woodchuck/docs/contest-precision-release-checklist.md` and
  `woodshed-woodchuck/woodshed.db.pre-analytics-20260915-130118.bak`.
  Neither was changed, staged, or copied. The database backup was not read.
- Existing branches, worktrees, and stashes were preserved. No stash, reset,
  cleanup, merge, commit, push, or deployment was performed.
- No production connection, production probe, real email/payment call, Render
  action, dependency change, or schema/migration edit was performed.

Tests use a cleared environment via `/tmp/ww-security-economy.rNz0uA/run-safe`:

```bash
#!/bin/bash
exec env -i PATH=/home/geph/Training_scripts/woodshed-woodchuck/.venv/bin:/usr/lib/postgresql/16/bin:/usr/bin:/bin LANG=C.UTF-8 TMPDIR=/tmp/ww-security-economy.rNz0uA PYTHONPYCACHEPREFIX=/tmp/ww-security-economy.rNz0uA/pycache DATABASE_URL=sqlite:////tmp/ww-security-economy.rNz0uA/default-test.db SESSION_SECRET=isolated-economy-tests SESSION_COOKIE_SECURE=false "$@"
```

The session secret above is a disposable test literal, not a production secret.
Only the original virtualenv executables are reused; the original database and
environment files are not loaded. SMTP configuration is absent; existing email
and billing tests use their test doubles. New route tests explicitly remove
`SMTP_HOST` and patch each participating module's database factory.

Disposable PostgreSQL 16 was initialized with `initdb` under
`/tmp/ww-security-economy.rNz0uA/pgdata`, started on `127.0.0.1:55439`, and assigned
database `ww_billing_a2_test`. `SHOW data_directory` verified the newly created
directory. The existing `disposable_url` test helper restricts host/database and
creates a unique schema per test. No pre-existing database is used. The opt-in URL
below contains no password and refers only to this disposable cluster:

```text
postgresql+psycopg://geph@127.0.0.1:55439/ww_billing_a2_test
```

## Balance and valuable-state trace

The authoritative balance remains `WoodchuckState.state_json.progress.credits`;
the proposal does not introduce a new balance table or migration.

| Path | Before this change | Server decision and proposed behavior |
| --- | --- | --- |
| Signup `POST /account/create` | Imported browser initial credits, then added login grant. | Starts balance at zero; existing first-day login award adds one. Canonical account identity is reconstructed. |
| Generic `PUT /account/state` | Replaced credits from browser JSON; revision check and write were not serialized with rewards/purchases. | Keeps stored credits even when submitted credits are absent, malformed, higher, or lower. Locks profile then state; refreshes cached state; checks revision under the lock. |
| BOOK `POST /practice-charts` | Browser calculated `floor(minutes/5) + detail count`, capped at 75/date. Server stored submitted `credits_awarded` without crediting balance. | Backend computes the same formula from validated minutes and normalized unique details; caps using persisted chart awards and any legacy quest BOOK entry for that date. The current Board Bonus Challenge remains separate from this cap. Atomically writes chart, RewardGrant, balance, revision. Submission-key retries do not award again. |
| Pristine practice | Explicit zero-dandelion policy. | Remains zero. Internal historical/service callers retain their existing non-awarding default; only authenticated BOOK creation enables the new award path. |
| Board hours/care/marching | Backend recorded dated, deduplicated CampPointAward; browser added one credit. | Existing accepted activity records cause one backend dandelion grant, atomically with balance/revision. No second award on replay. |
| Board trivia | Answer route validated today's first answer; browser added one credit. Generic camp-points route could claim trivia without the correct answer. | Correct saved answer awards one through the same backend helper. Generic trivia claim now requires a saved correct answer. Wrong-answer retry cannot change the first answer. |
| Bonus Challenge / legacy quest completion | Backend configured fixed five-credit reward, dated completion, grant, and activity records; client reports completion. | Existing reward rules preserved; locks before reading quest/state so sync or another earning action cannot race. Browser may select a configured quest via legacy daily state; it cannot select a payout. |
| Daily secret | Backend checked configured code and dated RewardGrant; awarded 20. | Same rules; locks before checking grant and changing balance to prevent duplicate/lost updates. |
| Login streak | Backend LoginStreak and dated RewardGrant decide amount (current streak days), once per Central day; weekly crown awards. | Existing rules retained; uses the common lock/refresh helper. |
| Arcade games | Backend charges one per authorized play, enforces ownership/token completion/idempotency, payout thresholds and ten rewarded completions/game/day. Scores still originate in browser. | Rules retained; balance reads/writes use common helper. History Mystery's once/Central-day rule remains. No game-authority redesign. |
| Weekly contest awards | Backend persisted results, RewardGrant deduplication, placement rewards (50/25/15), participation rewards (5), and legacy award paths. | Award amounts/results unchanged; `_add_dandelion` now serializes and refreshes balance, while preserving multiple pending rewards in one transaction. |
| Store purchases | Backend catalog determines price/availability; OwnedItemCopy proves ownership; balance deducted under state lock. | Profile-to-state lock order aligned with rewards/sync, response pairs authoritative balance with revision. Normal purchases and separate copies retained. |
| Other ownership grants | Mum free grant and permanent crowns use owned-item/crown tables, not browser credits. | Existing paths left intact and covered by existing tests. |

Readers inspected include store catalog/inventory/purchase services, arcade status,
login streak payloads, quest payloads, account loading and synchronization,
contest job reward summaries, and browser HUD/BOOK/BOARD/shop state. Remaining
browser balance assignments primarily consume backend payloads. The legacy
`wireStore` buy handler still contains client-only subtraction/ownership code,
but its required `store-credits-value`, `equipped-head-value`,
`equipped-body-value`, and `store-items` elements are absent from the current
templates; the rendered shop uses `/store/purchases`. No legacy handler was
removed or reactivated.

Immediately related fields:

- `account` is reconstructed from the authenticated profile, revision and sync
  timestamp. Submitted IDs, authenticated flags, or admin flags cannot select
  another account or become canonical account identity.
- Canonical profile name/instrument/level/goal/createdAt are rebuilt from the
  profile record. Existing profile-edit routes remain the means to change them.
- `progress.level`, `progress.streak`, and `progress.lastCompletedDate` retain
  stored values during generic sync; quest logic already owns streak/date writes.
- Legacy `inventory.ownedItems`, `inventory.crowns`, and `inventory.medals` display
  copies retain saved values. Actual owned copies, crown awards/progress, reward
  grants, memberships, Full Access, and XP continue using backend tables/rules.
- Preferences, equipment selections and other ordinary state still synchronize.
  This is a narrow preservation layer, not a general state-schema redesign.

## Implementation details

`app/economy.py` supplies a shared profile-to-state row lock. Locking the profile
also serializes first creation of a missing state row. The state query refreshes
SQLAlchemy's identity map after waiting for a concurrent writer. A pending change
already made by the same transaction is flushed before refresh, so repeated
contest awards in one transaction accumulate rather than overwrite one another.

Signup and generic sync preserve server-owned values. Sync keeps its existing
successful interface and 409 conflict payload; successful replies additionally
include the stored balance. Anonymous requests still receive 401 and account
selection still comes from the session.

The browser uses returned balance/revision pairs instead of adding practice or
Board credits locally. A small `WWState.applyEconomy` helper ignores delayed
older pairs. Store purchase responses also update the browser's revision so the
next ordinary sync does not attempt to restore the pre-purchase snapshot. Script
versions for the three changed assets are advanced together to avoid mixing a
cached state API with its new callers. No markup or styling redesign is included.

## Baseline and validation

Evidence directory: `/tmp/ww-security-economy.rNz0uA` (outside the repository).
`run-safe` in the following commands means the absolute runner path documented
above. Commands run from the isolated app directory unless specified otherwise.

Before editing code, the focused baseline passed **88 tests**:

```bash
/tmp/ww-security-economy.rNz0uA/run-safe env WW_BILLING_TEST_POSTGRES_URL=postgresql+psycopg://geph@127.0.0.1:55439/ww_billing_a2_test pytest -q -o cache_dir=/tmp/ww-security-economy.rNz0uA/pg-cache tests/test_team_contests.py tests/test_login_streaks.py tests/test_arcade_economy.py tests/test_account_state_sync.py tests/test_store_inventory.py tests/test_quest_completion_persistence.py
```

These existing files predominantly use SQLite even with the opt-in variable;
their count is not presented as PostgreSQL concurrency proof.
Baseline JavaScript: `run-safe node --test tests/*.js`, **34 passed, 0 failed**.
JavaScript syntax, Python compilation/import, and `git diff --check` also passed.

Two initial full baseline runs were interrupted because the filesystem was slow;
their partial results are not counted as completed baselines. A tracked-files-only
`git archive` of the same base was extracted under
`/tmp/ww-security-economy.rNz0uA/base-source`. This did not copy untracked files or
the database backup. A complete untouched baseline runs from its app directory:

```bash
/tmp/ww-security-economy.rNz0uA/run-safe pytest -q --basetemp=/dev/shm/ww-economy-baseline-snapshot -o cache_dir=/tmp/ww-security-economy.rNz0uA/baseline-cache
```

Final changed-tree command, after all implementation/test refinements and after
creating an empty disposable default schema for page tests that do not provide
their own database fixture:

```bash
/tmp/ww-security-economy.rNz0uA/run-safe env DATABASE_URL=sqlite:////dev/shm/ww-economy-final-default.db pytest -q --basetemp=/dev/shm/ww-economy-regression-complete -o cache_dir=/tmp/ww-security-economy.rNz0uA/final-complete-cache
```

Focused existing regression command (`focused-1.txt`):

```bash
/tmp/ww-security-economy.rNz0uA/run-safe pytest -q --basetemp=/dev/shm/ww-economy-focused -o cache_dir=/tmp/ww-security-economy.rNz0uA/focused-cache tests/test_account_state_sync.py tests/test_login_streaks.py tests/test_store_inventory.py tests/test_arcade_economy.py tests/test_quest_completion_persistence.py tests/test_practice_time_precision.py tests/test_phase5_persistence.py tests/test_contests.py
```

Result: **166 passed, 2 failed**. The two failures are the pre-existing contest
schema assertion and finalized historical week with unknown scoring mode; their
identities/causes are compared against untouched main below. No unrelated failing
expectation was relaxed to make the suite green.

New attack and concurrency command (`attacks-final.txt`):

```bash
/tmp/ww-security-economy.rNz0uA/run-safe env WW_BILLING_TEST_POSTGRES_URL=postgresql+psycopg://geph@127.0.0.1:55439/ww_billing_a2_test pytest -q --basetemp=/dev/shm/ww-economy-attacks-final -o cache_dir=/tmp/ww-security-economy.rNz0uA/attack-cache tests/test_server_economy.py
```

The test parametrization executes ordinary attacks on SQLite and disposable
PostgreSQL. Row-lock tests deliberately skip the SQLite variant and run on
PostgreSQL. Result: **50 passed, 6 skipped**. An earlier iteration had two failures caused by an incorrect test
catalog key; this was corrected to the real `ladybug` catalog item, without
changing application behavior or weakening the purchase assertion.

After reviewing the practice cap, additional tests distinguish current Board
Bonus Challenges (five credits separate from the 75-credit BOOK cap) from the
legacy quest route (its rewarded BOOK entry consumes five of that cap). The
backend identifies the legacy entry using its existing grant source key, not
browser log contents. An initial new test omitted the legacy request's required
`minutes` field and received 422; the test input was corrected. Post-refinement
command (`cap-confirmed.txt`):

```bash
/tmp/ww-security-economy.rNz0uA/run-safe env WW_BILLING_TEST_POSTGRES_URL=postgresql+psycopg://geph@127.0.0.1:55439/ww_billing_a2_test pytest -q --basetemp=/dev/shm/ww-economy-cap-confirmed -o cache_dir=/tmp/ww-security-economy.rNz0uA/cap-cache tests/test_server_economy.py -k practice
```

Result: **9 passed, 1 skipped, 50 deselected**. This overlaps the earlier attack
run and adds four new backend-parametrized compatibility cases; do not add the
two pass counts as if every case were distinct. The PostgreSQL cluster was
stopped after these checks completed.

### Actual baseline failures and environment checks

The complete untouched-main run produced **1415 passed, 23 failed, 152 skipped**.
The ten failures identified in `docs/prebeta-analytics.md` were confirmed by test
identity and cause, rather than assumed from a count:

| Existing failing test | Observed cause on untouched main |
| --- | --- |
| `test_board_standings.py::test_live_scoreboard_javascript_uses_actual_ranks_and_preserves_ties` | String assertion expects the former minute-only score formatting. |
| `test_board_standings.py::test_live_leaderboard_rows_show_numeric_scores_without_repeated_units` | Expects direct `String(row.total_minutes)` assignment predating precision formatting. |
| `test_contests.py::test_existing_finalized_student_scores_are_not_rewritten` | Historical finalized fixture has unknown scoring mode; existing repair code rejects it with 409. |
| `test_phase5_persistence.py::test_contest_models_match_approved_foundation` | Approved-column assertion omits already-present `practice_scoring_mode`. |
| `test_phase6_mobile_assets.py::test_rendered_pages_use_one_current_stylesheet_version` | Expects stylesheet v119; unchanged main has v120. |
| `test_phase6a.py::test_book_actions_precede_spiral_and_metallic_statistics_page` | Expected previous BOOK markup substring is absent. |
| `test_phase6c_audio.py::test_tone_is_exactly_pinned_local_licensed_and_loaded_in_order` | Expected prior asset substring is absent. |
| `test_production_hotfix.py::test_instrument_assets_are_cache_busted_and_failures_are_visible` | First assertion expects stylesheet v119 rather than v120. |
| `test_team_continuity.py::test_join_code_collision_and_no_automatic_activation` | Assertion forbids an existing `team_continuity_repair` import. |
| `test_trusted_verifier_dashboard.py::test_shared_metrics_completed_rating_and_private_note_isolation` | Expected old metrics dictionary lacks three already-present seconds fields. |

Additional baseline failures were investigated separately:

- Ten route/render tests queried the intentionally empty default SQLite database
  without constructing their own schema, producing `no such table: seasons`:
  six `test_board_standings.py` tests (live markup, requested order, authentication,
  past-winners states, conflict markers, camp-points winners),
  `test_contest_admin.py::test_admin_page_requires_valid_token_and_is_not_in_student_navigation`,
  `test_phase10_back_to_school.py::test_live_contests_are_collapsible_categories_with_future_divisions`,
  `test_phase8_pages.py::test_main_templates_still_render`, and
  `test_shed_audio_position.py::test_shared_sound_controls_render_on_main_pages_except_shop`.
- Two CLI tests require literal `.venv/bin/python` in the checkout:
  `test_team_continuity_inventory.py::test_help_import_does_not_load_app_models_or_offer_repair`
  and `test_team_continuity_repair.py::test_cli_defaults_driver_normalization_and_redaction`.
  The archive/worktree initially had no such directory. Temporary links to the
  existing interpreter were supplied for validation, without installing anything.
- `test_trusted_verifier_dashboard.py::test_rendered_dashboard_mobile_and_desktop[390]`
  exceeded its existing 20-second headless-Chrome deadline. Its changed-tree
  repeat passed; no timeout or assertion was weakened.

All 23 failing baseline identities plus the changed app-asset version assertion
were rerun on each tree with separate, fully initialized disposable page-test
databases. Initial corrected baseline: **13 passed, 11 failed** (the same ten plus
the Chrome timeout). Confirmed changed-tree repeat: **14 passed, 10 failed**,
exactly the ten known failures above. An earlier changed-tree environment repeat
started before schema initialization finished and had three SQLite lock errors;
that setup race was corrected and the repeat passed those tests.

Exact environment-rerun driver and expanded pytest argument lists are saved in
`/tmp/ww-security-economy.rNz0uA/rerun-environment.py`, `baseline-environment.txt`,
and `final-environment-confirmed.txt`. The driver takes the failed node IDs from
the untouched baseline log and appends only
`tests/test_phase8_stabilization_book.py::test_book_asset_versions_are_advanced`.
It does not modify, deselect, or weaken existing tests.

### Attack matrix

| Surface | Attack attempted | Expected | Actual/result |
| --- | --- | --- | --- |
| Signup | Import 999999 credits and owned UFO/admin flag | Only real login credit; no ownership/privilege | PASS: assertions confirmed |
| State sync | Huge/higher/lower/zero/negative/null/string/bool/missing credit; malformed progress | Saved funds retained; normal preference saved | PASS: assertions confirmed |
| Purchasing power | Forge funds/owned items/crowns then buy unaffordable UFO | 409; no owned copy/crown/reward minted | PASS: assertions confirmed |
| Identity | Submit Student B's ID in JSON/query from Student A; spoof profile and auth flags | A's canonical identity; B unchanged; anonymous 401 | PASS: assertions confirmed |
| Missing state | Remove isolated state row, submit initial forged balance | Zero, no imported money | PASS: assertions confirmed |
| Practice | Submit 75-credit claim on a 6-credit chart; retry key; exceed daily cap | Server computes 6, retry zero additional, total capped at 75; real purchase works | PASS: assertions confirmed |
| Board/trivia | Repeat awards; generic trivia claim; retry wrong answer as correct | One grant/activity; generic unearned trivia 400; wrong answer stays wrong | PASS: assertions confirmed |
| Stale revision | Sync snapshot from before secret/practice/Board/quest/purchase/arcade | 409; neither lost reward nor refunded purchase/entry cost | PASS: assertions confirmed |
| Fresh revision | Replace current balance with pre-action value | 200 preference sync; current funds unchanged | PASS: assertions confirmed |
| PostgreSQL lock wait | Hold reward/purchase transaction while stale sync waits | After commit, stale sync 409 and correct balance | PASS on disposable PostgreSQL |
| PostgreSQL cached row | Load old state, commit other reward, add server award | Refresh and accumulate both rewards | PASS on disposable PostgreSQL |
| PostgreSQL simultaneous sync | Two requests using same revision | Exactly one 200 and one 409 | PASS on disposable PostgreSQL |
| PostgreSQL practice cap | Two concurrent 50-credit submissions | Awards 50 and 25, total 75 | PASS on disposable PostgreSQL |
| PostgreSQL reward/purchase | Duplicate secret requests plus purchase concurrently | Exactly one reward and one correct deduction | PASS on disposable PostgreSQL |

Existing tests cover login-streak grants/idempotency/crowns, arcade costs/payouts
and replay/daily limits, quest completion/reward persistence, duplicate store
copies/ownership/placements, contest finalization/reward idempotency and multiple
rewards in one transaction. New Node tests cover balance/revision pairing and
rejection of older responses. Final JavaScript suite: **36 passed, 0 failed**.

Shop/BOOK follow-up (`shop-final.txt`):

```bash
/tmp/ww-security-economy.rNz0uA/run-safe env DATABASE_URL=sqlite:////tmp/ww-security-economy.rNz0uA/final-page-tests.db pytest -q --basetemp=/dev/shm/ww-economy-shop-final -o cache_dir=/tmp/ww-security-economy.rNz0uA/shop-cache tests/test_phase6b_shop.py tests/test_phase8_stabilization_book.py
```

Result: **24 passed**. The existing shop test's blanket `saveState` prohibition
legitimately changes because caching the backend purchase snapshot is now
required. Its replacement permits only the exact `sync: false` cache update,
requires `applyEconomy`, and continues to forbid other shop state saves. Existing
dialog, keyboard-focus, API-call and accessibility assertions remain intact.
The full run had already collected the prior test body before this final
expectation adjustment; the focused rerun uses the updated test.

The first changed-tree full attempt (`final-suite.txt`) had **1436 passed,
24 failed, 186 skipped**. It used the initially empty default database and had
already collected the old asset assertion. Additional failures were two
headless-browser timeouts and the now-corrected shop assertion above. This is
intermediate evidence, not the final full-suite result.

Both director browser sizes passed isolated repeats without changing timeouts or
application behavior: `tests/test_band_director_metrics.py::test_browser_table_layout_and_sorting[390]`
and `[1440]`, one pass each. Commands used the safe runner, the initialized
`final-page-tests.db`, and their individual node IDs; output is in
`director-repeat.txt` and `director-desktop-repeat.txt`. The main-only verifier
mobile repeat again hit its existing deadline (`baseline-browser.txt`); the
changed-tree environment repeat passed that same test. Browser timeouts are
reported as intermittent environment limitations, not silently counted as green.

No application logging was added. Review of the touched backend paths and entire
diff found no added credential/token logging, committed secrets, dependency
changes, or production-service calls. This is a focused change review, not a
claim that the separately queued security work is complete.

## Remaining dependencies and classification

1. **HIGH / BETA BLOCKER — deployed synchronization protection: OPEN.** These are
   uncommitted local changes. Product must review and release them separately;
   production protection was neither changed nor probed. No production action is
   authorized in this run.
2. **HIGH / BETA BLOCKER for complete server-authoritative earned game rewards —
   Arcade score authenticity: OPEN.** Arcade accepts client scores within its
   existing bounds. Server-owned amounts, ownership, caps and deduplication do
   not establish that a submitted score was earned. Authoritative game simulation
   or replay validation requires a separate game protocol/validation design; it
   is not implemented by this narrow balance patch. Product/Security must resolve
   this dependency before claiming fully tamper-resistant earned rewards.
   Separately, practice minutes/details/date and Board/quest completion are
   self-reported under existing product rules. Requiring verified practice or
   restricting historical dates needs an explicit Product decision. These
   **MEDIUM / SHOULD FIX** policy boundaries are documented, not changed. No
   earning pathway was disabled while awaiting that decision.
3. **MEDIUM / SHOULD FIX — historical balance provenance: OPEN.** Existing balance
   snapshots and historical chart credit values may already include client-forged
   amounts. Old browser earnings have no complete authoritative ledger. Repair
   requires a separately approved policy for legitimate historical credits and
   any supporting evidence; this run does not zero balances or run a migration.
4. **MEDIUM / SHOULD FIX — old unsynchronized clients/retries.** Existing dated
   activity rows and submission keys are not retroactively re-awarded: doing so
   could double-credit accounts that already synced old browser awards. A
   pre-release offline earning that was never persisted/synced cannot be safely
   accepted just because a browser later claims it. Any restoration needs a
   reviewed support/reconciliation policy. New accepted activities award
   atomically on the backend.
5. **LOW / BACKLOG — legacy browser state.** Legacy daily quest selection/status,
   equipment preferences and display-only histories remain syncable. Backend
   entitlements and reward records do not use them as ownership proof. A complete
   state cleanup is deliberately deferred.

No new infrastructure is needed for this local economy change. Render Key Value
configuration belongs to the separately queued throttling run, not this patch.

## Changed-file audit

All paths below are relative to `woodshed-woodchuck/`; every change supports this
economy finding or its evidence.

| File | Reason |
| --- | --- |
| `app/economy.py` | New narrow lock/refresh, protected-field preservation and balance payload helpers. |
| `app/account_routes.py` | Protect signup/sync balances and canonical identity; serialize daily-secret award. |
| `app/arcade_rewards.py` | Share consistent balance lock/refresh, retaining game rules. |
| `app/login_streaks.py` | Share consistent balance lock/refresh, retaining streak/crown rules. |
| `app/contests.py` | Serialize rewards/quests; replace Board browser earnings; require correct trivia attempt. |
| `app/practice_charts.py` | Compute and atomically persist legitimate BOOK awards/cap/dedup. |
| `app/practice_chart_routes.py` | Enable award for authenticated BOOK route and return authoritative snapshot. |
| `app/store_inventory.py` | Align purchase lock ordering and refresh. |
| `app/store_routes.py` | Pair purchase balance with transaction's revision. |
| `static/js/state.js` | Apply backend balance/revision together; reject older snapshots. |
| `static/js/account.js` | Consume authoritative sync balance and avoid revision rollback. |
| `static/js/app.js` | Use server practice/Board/purchase snapshots instead of browser credit arithmetic. |
| `templates/base.html` | Only bump three changed script cache versions; prevent mixed cached APIs. |
| `tests/test_server_economy.py` | New attacks, legitimate earning tests, PostgreSQL races. |
| `tests/test_server_economy.js` | New authoritative snapshot/older-response tests. |
| `tests/test_account_state_sync.py` | Two expectations intentionally change: initial browser 9 no longer becomes 10; browser 18 no longer replaces stored 17. |
| `tests/test_quest_completion_persistence.py` | Seed test starting funds through database fixture instead of the now-prohibited signup credit import; existing reward assertions unchanged. |
| `tests/test_phase8_stabilization_book.py` | Update expected app.js cache version for the changed asset. |
| `tests/test_production_hotfix.py` | Update expected account.js cache version; unrelated pre-existing failure remains. |
| `tests/test_phase6b_shop.py` | Replace the blanket ban on saving any local shop state with a narrow allowance for the backend balance/revision cache using `sync: false`; continue forbidding other shop state saves. |
| `docs/security-round1-server-economy.md` | Required trace, validation and review evidence. |

No unrelated files, CSS, dependency manifests, schema, or migrations changed.
No pre-existing work was altered. No legitimate earning or purchasing function
was intentionally removed; direct browser authority over protected fields was
intentionally removed. All changes remain unstaged and uncommitted.

## Economy phase final validation record

| Run | Passed | Failed | Skipped | Evidence |
| --- | ---: | ---: | ---: | --- |
| Focused untouched baseline | 88 | 0 | 0 | `baseline-focused.txt` |
| Full untouched baseline | 1415 | 23 | 152 | `baseline-complete.txt` |
| Baseline failures repeated with corrected local setup, plus asset check | 13 | 11 | 0 | `baseline-environment.txt`; includes persistent mobile-browser timeout |
| Same targeted checks on changed tree after setup completes | 14 | 10 | 0 | `final-environment-confirmed.txt`; exactly the ten known failures |
| Existing account/economy/reward/practice/contest regression | 166 | 2 | 0 | `focused-1.txt`; both known baseline failures |
| New attacks and PostgreSQL concurrency | 50 | 0 | 6 | `attacks-final.txt`; SQLite row-lock variants skipped |
| Final practice cap compatibility, SQLite/PostgreSQL | 9 | 0 | 1 | `cap-confirmed.txt`; 50 cases intentionally deselected by `-k practice` |
| Final full run | 1450 | 12 | 188 | `regression-complete.txt` |
| Final shop/BOOK follow-up | 24 | 0 | 0 | `shop-final.txt` |
| Director browser isolated repeats, mobile and desktop | 2 | 0 | 0 | `director-repeat.txt`, `director-desktop-repeat.txt` |
| Baseline JavaScript | 34 | 0 | 0 | `baseline-js.txt` |
| Final JavaScript | 36 | 0 | 0 | `final-js.txt` |

The final full run is **not all green**. Its 12 failures are the ten existing
failures listed above, a 20-second director mobile Chrome timeout (exit 2), and
the old shop assertion already collected before its documented update. The
mobile test and the updated shop test both pass in focused reruns. The Chrome
driver strips shared application scripts and executes the unchanged dashboard
script. The timeout's environmental root cause is not conclusively established;
the repeat result does not erase the failed full-run result. No unexplained
economy/authorization failure remains in this evidence.

The full run leaves PostgreSQL opt-in tests skipped because the environment does
not specify a PostgreSQL URL. The affected PostgreSQL economy and concurrency
tests ran separately against the verified disposable cluster. No production or
unverified PostgreSQL target was substituted for a skipped test. New tests add
60 collected backend-parametrized cases; in the SQLite-only full run their
PostgreSQL variants and six SQLite row-lock variants account for 36 extra skips.

Final syntax/import command (after the final application change):

```bash
/tmp/ww-security-economy.rNz0uA/run-safe python - <<'PY'
from pathlib import Path
import subprocess
paths = list(Path('app').rglob('*.py'))
for path in paths:
    compile(path.read_bytes(), str(path), 'exec')
import app.main
scripts = list(Path('static/js').glob('*.js'))
for path in scripts:
    subprocess.run(['node', '--check', str(path)], check=True)
print(len(paths), len(scripts))
PY
```

Result: **60 Python files compiled, `app.main` imported, 32 JavaScript syntax
checks passed**. Final JavaScript command is
`/tmp/ww-security-economy.rNz0uA/run-safe node --test tests/*.js`.
`git diff --check` passed after the final edits. No tests were disabled, skipped
to hide failures, or removed; the new SQLite-only concurrency skips explicitly
require the separately executed PostgreSQL variants.

Manually reviewed all changed files and new files: no unrelated formatting,
markup/CSS changes, renamed routes, debug logging, credentials, dependency
updates, migrations, schema edits, or changes elsewhere in Training_scripts.
The temporary worktree `.venv` link was removed after validation; only the intended
source, tests and report remain. All staged-file listings are empty.

Recovery copies (outside Git, including new files):

- `/tmp/ww-security-economy.rNz0uA/gate-a-recovery.patch` after the focused attack,
  regression and cap gates.
- `/tmp/ww-security-economy.rNz0uA/final-recovery.patch` with the final reviewed
  diff, including this report.

Final branch is `security/round1-server-economy`, still at starting HEAD
`efee4d1f3a79ed57505c6b7db897968dd956e256`. Final `git status --short`, from the
isolated application directory:

```text
 M app/account_routes.py
 M app/arcade_rewards.py
 M app/contests.py
 M app/login_streaks.py
 M app/practice_chart_routes.py
 M app/practice_charts.py
 M app/store_inventory.py
 M app/store_routes.py
 M static/js/account.js
 M static/js/app.js
 M static/js/state.js
 M templates/base.html
 M tests/test_account_state_sync.py
 M tests/test_phase6b_shop.py
 M tests/test_phase8_stabilization_book.py
 M tests/test_production_hotfix.py
 M tests/test_quest_completion_persistence.py
?? app/economy.py
?? docs/security-round1-server-economy.md
?? tests/test_server_economy.js
?? tests/test_server_economy.py
```

The original checkout was rechecked: branch `main`, same HEAD, and only its same
two original untracked files. Neither was read for test data or modified. No
commit, push, deployment, production data change, or infrastructure change was
made. Review/release and the unresolved decisions above remain human work.

---

# Continuation — remaining Security Round 1 phases

## Continuation scope and safety

Product/Operations authorized continuing in the existing application worktree
`/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck`,
branch `security/round1-server-economy`. The branch and HEAD were verified before
editing: `efee4d1f3a79ed57505c6b7db897968dd956e256`. All prior economy changes were
present and unstaged. They were preserved. The original checkout and its two
untracked files were not changed or used as test data.

This continuation implements authentication throttling and production session
validation, audits cross-user authorization, reviews Arcade forgery consequences,
and makes one narrow sensitive-log correction. PIN recovery and authoritative
game/replay protocols remain outside this implementation. No migrations, commits,
pushes, deployments, Render modifications, real email, or payment calls occurred.

**Cumulative status: PARTIAL; no unrestricted-public-beta clearance.** Locally
implemented controls have passing focused tests. Production enforcement has not
been configured or verified, and score authenticity remains OPEN.

## Finding 2 — authentication throttling

### Implementation and usability

`app/login_limits.py` provides a small internal abstraction with an atomic Redis
backend and a locked, deterministic in-memory backend for local development/tests.
Production never falls back to process-local counters. The only new application
dependency is `redis>=6.4,<7` (redis-py, tested with 6.4.0): the mature Redis wire
client, not a rate-limiting framework. No existing dependency was upgraded.

Limits use expiring 15-minute windows:

| Authentication path | Normalized identity limit | Source-IP limit |
| --- | ---: | ---: |
| Student login | 10 attempts per Woodchuck ID (`strip().upper()`) | 100 attempts |
| Verifier login | 10 attempts per email (`strip().lower()`) | 100 attempts |
| Verifier invitation acceptance | Shares verifier email counter | Shares verifier IP counter |
| Site-admin login | No token in key | 5 attempts |

Every attempt reserves quota **before** credential verification, including
successful attempts. This avoids a check-then-increment race and limits concurrent
credential guesses. A successful login does not reset the IP spray counter.
Ordinary mistakes remain recoverable; no permanent account lockout or student-table
counter is introduced. Excess attempts return generic HTTP 429 with `Retry-After`.
Counters expire without requests; blocked retries do not extend the window.
Counters use Redis time-to-live, not application-process clocks.

One Lua operation increments both relevant counters and sets/reads expiration
atomically. This prevents separate application processes from overshooting the
shared quota or creating counters with no expiration. Keys contain HMACs of
normalized identifiers/IPs with domain separation; the stable session signing key
is reused as the HMAC key. PINs, admin tokens, invitation tokens, raw emails and
raw IP addresses are not limiter keys. Redis exceptions/URLs are not logged or
returned to the browser. Standard login failure and throttled responses do not
identify whether the account exists.

**Additional bypass found and closed:** invitation acceptance verifies an existing
verifier's PIN and signs that verifier in. Limiting only the ordinary login route
would leave that alternate credential-checking path unrestricted. Acceptance now
uses the invitation's database email and the same verifier/IP limits; it never
keys on the invitation token. A negative test consumes quota across both routes,
checks a different IP still encounters the account limit, then proves legitimate
acceptance works after expiration. Existing invitation-specific validation/error
messages are otherwise retained, including the existing-PIN guidance for a valid
invitation; this is not a claim that the whole invitation flow is enumeration-free.

### Configuration, failure behavior, and status

| Setting | Behavior |
| --- | --- |
| `LOGIN_RATE_LIMIT_MODE=off` | Default. No limiter network connection; merging the code alone does not require Key Value or disable existing logins. Protection is **disabled**. |
| `LOGIN_RATE_LIMIT_MODE=memory` | Explicit local/test use only. Rejected for production. Never an outage fallback. |
| `LOGIN_RATE_LIMIT_MODE=redis` | Enables shared backend enforcement. Requires `LOGIN_RATE_LIMIT_REDIS_URL`. Failures close sign-in with 503 even if `REQUIRED` is false. |
| `LOGIN_RATE_LIMIT_REQUIRED=true` | Requires Redis mode. Missing backend, off/memory mode, malformed configuration or backend outage cause login 503. Existing authenticated, unrelated routes remain available. |
| `LOGIN_RATE_LIMIT_REQUIRED=false` | Default rollout setting; permits explicitly disabled protection. Invalid boolean spellings fail closed rather than silently disabling the requirement. |
| `LOGIN_RATE_LIMIT_REDIS_URL` | Secret Redis/Valkey connection URL, supplied outside Git. Both `redis://` and `rediss://` supported. No value printed in this report. |
| `FORWARDED_ALLOW_IPS` | Must be explicitly empty when production limiting is enabled; see raw-peer requirement below. |
| `LOGIN_TRUSTED_PROXY_CIDRS` | Optional comma-separated, verified proxy networks. Empty means use the socket peer. All-address `/0` trust is rejected. |

Redis connect/read timeouts are one second each, with retry disabled. Backend
failure does not silently allow a credential guess and does not switch to memory.
The rejection is a sanitized HTTP 503, without exception details or secret values.
Only sign-in enforcement is affected; this does not make unrelated pages depend
on Redis. Administrative outage diagnosis may require an already signed-in admin,
since new administrator logins are also protected.

`GET /admin/security/rate-limit` uses the existing site-admin authorization
boundary and `Cache-Control: no-store`. Unauthorized roles cannot reach its
backend probe. It reports separately:

- `disabled`: mode off, not required.
- `configured`: configuration accepted, backend not yet checked (startup report).
- `backend_operational`: the actual Redis atomic-script/ACL path succeeded using
  an isolated diagnostic key that expires in two seconds.
- `local_only`: the explicit development backend.
- `unavailable`: required configuration or backend check failed.

The response includes `configured`, `required`, and `production_verified: false`.
A successful backend probe is **not** evidence that Render's proxy/IP behavior or
all application processes have been verified. Production startup logs the
configuration state without printing secrets. This patch does not set a
production-clearance flag or claim currently deployed rate limiting.

### Proxy/source-IP review and remaining deployment gate

The repository README documents Uvicorn, but there is no checked-in Render
service configuration proving the actual launch command or ingress chain. No
Render configuration connector was available; the live service was not probed.

Render's official documentation says `RENDER=true` is supplied automatically and
its Python runtime defaults `FORWARDED_ALLOW_IPS` to `*`. Uvicorn can consume that
setting and rewrite ASGI `scope['client']` from forwarding headers before application
code runs. Therefore, blindly using `request.client.host` under those defaults is
not sufficient proof of a trustworthy source address. Sources:
[Render default environment variables](https://render.com/docs/environment-variables),
[Uvicorn proxy settings](https://www.uvicorn.org/settings/).

The production limiter requires an unmodified peer address: explicitly empty
`FORWARDED_ALLOW_IPS`, with a reviewed Uvicorn launch command using
`--no-proxy-headers` and no overriding wildcard/CLI trust setting. The application
then ignores forwarding headers from untrusted socket peers. For an explicitly
trusted peer, it walks `X-Forwarded-For` from right to left, skipping only configured
trusted proxy hops. Spoofed prefixes cannot replace the first untrusted address
encountered from the server side. Malformed/overlong/all-trusted chains use the
socket peer as one conservative bucket. IPv4-mapped IPv6 addresses normalize to
the same identity. Other forwarding headers are ignored.

**This assumes the trusted ingress appends or sanitizes a trustworthy chain. That
contract has not been verified for this deployment.** Do not guess a proxy CIDR
or assume a header position based on a general description of Render. Without a
verified chain, leave the proxy allowlist empty; that aggregates users behind the
proxy and is not satisfactory evidence of per-user source-IP protection/usability.
An explicit CLI override can also defeat an environment setting; the application
cannot introspect the external server's launch flags. These remain release gates.

Disabling Uvicorn proxy-header rewriting also stops its forwarded-scheme handling.
Preserve a correct HTTPS `PUBLIC_BASE_URL`/`RENDER_EXTERNAL_URL`, and verify HTTPS
links, admin CSRF/origin checks, and secure-cookie sign-in in staging before any
production rollout. The code does not change those Render settings itself.

Redis scripting reference: [atomic counter and expiration pattern](https://redis.io/docs/latest/commands/incr/).
Client reference: [redis-py 6.4.0](https://pypi.org/project/redis/6.4.0/).

### Evidence and status

Tests prove student/verifier valid login after ordinary mistakes, shared normalized
identity limits across IPs, IP spraying across identifiers, stricter admin limits,
expiration without permanent lockout, correct invitation acceptance after expiry,
no PIN/token/secret logging or keys, and 503 behavior on required misconfiguration
and simulated backend outage. Concurrency testing includes 60 concurrent local
reservations (exactly 10 admitted) and 40 operations from four spawned OS processes
against a real disposable Redis server (exactly 10 admitted). Redis expiration and
both counter dimensions are exercised on the actual server. A Uvicorn middleware
integration test proves the prescribed empty-trust setting retains the raw peer
when a client supplies forged forwarding headers.

Status: **FIXED locally for covered credential paths; PARTIAL/OPEN operationally**
until Key Value and the actual proxy chain are configured and verified. No
production-throttling claim is made.

## Finding 3 — production session hardening

`app/session_config.py` centralizes the small existing session-key/cookie decision,
without introducing a general configuration framework. Production is detected by
Render's marker or `APP_ENV=production`. A local environment override cannot negate
Render's marker. Production rejects missing/blank/short keys and the known
development key, including surrounding whitespace. The minimum length is 32
characters; this checks configuration shape, not entropy. Jeff must retain a
strong, private production key.

Production secure cookies default on. Explicit false/invalid cookie settings fail
startup. Local/test environments retain the development fallback and non-secure
HTTP cookies. `SameSite=lax`, session routes and kid-friendly PINs are unchanged.
A valid configured signing key is used exactly as supplied, preserving session and
HMAC semantics.

All session-secret usages were traced: SessionMiddleware, site-admin fingerprint,
contest-admin fingerprint, and retired student-ID HMAC reservations. They now use
the same validated secret accessor. Existing configured secrets were not read,
printed, replaced or rotated. Rotating that secret would invalidate sessions and
change the existing retired-ID HMAC derivation; no such rotation was performed.

Tests cover real application import/startup, missing/default/short keys, Render
marker precedence, production cookie rejection/defaults, local defaults, both
admin fingerprints and retired-ID hashing, and fingerprint invalidation on key
rotation. Existing admin/CSRF/membership/deletion tests remain passing.

Status: **FIXED locally**. Deployed settings were not inspected or changed; existing
production configuration must be checked against these requirements before release.

## Finding 4 — cross-user authorization matrix

Tests use disposable Student A/B and Verifier A/B identities, isolated relationships,
resource IDs and memberships. Existing adequate proof tests were retained and
reused. No ownership subsystem was rewritten. The following are observed results,
not claims that every possible application path has been exhaustively proven.

| Surface | Attack attempted | Expected | Actual | Result / evidence |
| --- | --- | --- | --- | --- |
| Account state/profile | A submits B's ID in JSON/query, admin/auth flags, forged profile fields | Session-derived A; B unchanged; anonymous 401 | Preserved | PASS; retained `test_server_economy.py::test_identity_and_other_account_stay_server_owned` and state-sync tests |
| Practice charts | A supplies B's profile/student IDs in query | Only A's records | 200 with A's empty chart list; B's private note absent | PASS; new `test_query_selectors_cannot_change_private_student_scope` |
| Practice insights / Full Access | Anonymous/free/other identity requests protected insights | Existing 401/403 and entitlement scope | Preserved | PASS; `test_practice_insights.py` denied/privacy/access-loss tests |
| Store inventory | A supplies B's profile ID | Only A's inventory | 200 scoped to A | PASS; new query-selector test |
| Placements and sizing | A uses B's owned-copy or permanent-reward IDs | 404; no ownership/placement change | Preserved | PASS; `test_store_placement.py`, `test_reward_inventory_bridge.py` cross-profile tests |
| Rewards/crowns | Client invents reward ownership; places another account's crown/snack | No table-backed grant; 404 on foreign IDs | Preserved | PASS; retained economy tests and reward-inventory tests |
| Arcade scores/rewards | B submits A's play token; wrong game/replay | 404/409; no other-account award | Preserved | PASS; `test_arcade_economy.py` replay/other-profile tests and new forgery characterization |
| Arcade score authenticity | A submits maximum accepted score without playing | Authentic score would be required for full integrity | Accepted; net four credits and shared score updated | **OPEN**, described below; not an ownership failure |
| Contests/results | Unauthorized/private-field requests; attempts to affect frozen results | Existing access controls; public rankings remain public | Preserved | PASS; contest endpoint/privacy/immutable-results tests |
| Memberships | Non-owner manipulates actor/membership IDs | No access to another owner's resources | Existing 403/404 preserved | PASS; `test_memberships.py::test_owner_routes_privacy_csrf_and_actor_selection` and owner-isolation tests |
| Seats/invitations | Owner A targets B's seats/invitations | No mutation; private payer data not disclosed | Preserved | PASS; membership cross-owner/seat tests |
| Verifier invitations | Student A cancels, reissues or resends B's invitation | 404 before email/token work | 404; invitation remains pending | PASS; new parametrized invitation tests |
| Verifier relationships | A disconnects B's relationship | 404; B remains connected | 404; accepted relationship retained | PASS; new relationship test |
| Verifier dashboards/reviews | Verifier A selects B's connection/student/review | No foreign private chart/note; 404 for foreign review | Preserved | PASS; `test_verifier_relationships.py`, `test_band_director_roster.py` isolation/revocation tests |
| Band-director data | Unrelated director selects roster/student; ordinary verifier requests director-only view | Established redirect/404; accepted roster only | Preserved | PASS; roster and director-contest authorization tests |
| Teams/rosters | Unrelated director manages a private team; join code used as management authority | Reject mutation; approval required | Preserved | PASS; `test_director_teams.py` and moderation tests |
| Site-admin operations | Student/verifier/contest-admin tries membership administration | Site-admin boundary and CSRF enforced | Preserved | PASS; existing memberships/admin/CSRF tests |
| `/admin/analytics` | Anonymous, A/B verifier, student, forged session flag, query/header admin hint | 403 before report query | 403; no protected query | PASS; new tests plus existing analytics token-rotation/read-only tests |
| Analytics underlying routes | Search for alternate report/data endpoint | Every exposed report route protected | Only `/admin/analytics` exists; `build_report` is internal, called after authorization | Audited; route-inventory assertion and existing 405 POST test |
| `/admin/security/rate-limit` | Non-admin requests backend diagnostic | 403 before backend probe | 403; no probe | PASS; new tests; real site admin gets non-cached status |
| Public/shared leaderboards | Attempt to hide intentional public names/scores while testing privacy | Preserve intended rankings; omit private IDs/PINs/notes | Preserved | PASS; existing public-payload tests and new Arcade assertion |

No new cross-user ownership defect requiring an authorization rewrite was found
in these tested surfaces. The original 403/404 distinctions remain unchanged.
Membership/billing architecture and deliberately shared leaderboard information
were not changed. This does not certify minor-user privacy policy or COPPA status.

## Arcade forgery consequences — OPEN, separate proposal required

The backend bounds scores but does not prove gameplay. The isolated attack starts
Blue with 20 credits, pays the real one-credit entry charge, submits the maximum
accepted integer without playing, and receives the five-credit payout. Balance
becomes **24**, replay does not add another payout, and another account cannot use
the token. The forged maximum appears in the shared scoreboard.

Under the current constants, eight repeatable game types each allow ten rewarded
completions per Central day with maximum payout five and entry cost one; History
Mystery allows one such play/day. **Inference from these rules:** repeated forged
winning submissions could earn up to 324 net dandelions/account/day from those
paths (320 plus four), assuming initial entry funds and successful allowed plays.
This is a derived bound, not a 324-credit attack run. Those credits can purchase
legitimate catalog items. The new generic-sync protections do not prevent this
separate earning-source forgery.

Personal/shared Arcade high scores are affected. Plunge also has a separate
`POST /xp/plunge-best` score path without a paid-play token and
`POST /xp/plunge-points` that validates event type/point value/dedup key, but not
actual gameplay. The latter's XP contribution is capped at ten per Central day.
These are additional integrity boundaries in the same required game proposal.
The reviewed contest scoring code uses practice/camp-point records, not Arcade
high-score rows; no direct forged-score-to-weekly-contest-medal path was found.

Classification: **HIGH / BETA BLOCKER for fully server-authoritative earned-game
rewards; OPEN**. A separate proposal must define trusted score/event evidence,
replay or server simulation, compatibility with existing clients, and treatment of
historic scores/awards. No game was disabled, payout weakened, protocol redesigned,
or migration created here. This open finding did not block independent phases.

## Focused sensitive-data/log findings

- No intentional new PIN, session key, admin token, database password, Stripe
  secret, webhook signature or cookie logging was added. Limiter failures discard
  backend exception details; tests inject synthetic secrets and verify absence.
- **Actual narrow defect fixed:** `arcade_routes._unexpected_arcade_error` used
  `logger.exception`, which can print SQLAlchemy bound parameters containing play
  tokens. It now records only the fixed operation and allowlisted game key. An
  arbitrary request header can no longer become the logged game label. A simulated
  database exception with sensitive markers is absent from both logs and response.
- Invitation/practice email warnings log record ID and a safe result code.
  Analytics warnings log operation and exception class, not exception text.
  Billing recovery/reconciliation already avoid persisting adapter exception text.
- A targeted tracked-file scan found no live Stripe key or private-key block.
  Embedded database-password patterns occurred only in existing synthetic
  redaction-test fixtures. This is not proof that all historical commits or external
  configuration are secret-free; neither was searched.
- **OPEN — MEDIUM / LAUNCH BLOCKER pending logging review:** invitation and Arcade
  token values occur in URL paths. Default Uvicorn/reverse-proxy access logging may
  record paths, independent of application logs. Actual Render/proxy log collection
  and retention were not inspected. Jeff must disable/redact sensitive paths at
  every logging layer before asserting tokens are absent from operational logs.
  No route/protocol redesign or infrastructure change was made to hide this risk.

## Continuation test isolation and exact commands

Lengthy logs and disposable dependencies/services live outside Git at
`/tmp/ww-security-round1-continuation.z1hkSz`.
The runner is `/tmp/ww-security-round1-continuation.z1hkSz/run-safe`:

```bash
#!/bin/bash
exec env -i PATH=/home/geph/Training_scripts/woodshed-woodchuck/.venv/bin:/usr/bin:/bin LANG=C.UTF-8 TMPDIR=/tmp/ww-security-round1-continuation.z1hkSz PYTHONPYCACHEPREFIX=/tmp/ww-security-round1-continuation.z1hkSz/pycache PYTHONPATH=/tmp/ww-security-round1-continuation.z1hkSz/deps DATABASE_URL=sqlite:////dev/shm/ww-security-continuation-default.db SESSION_SECRET=isolated-security-tests SESSION_COOKIE_SECURE=false "$@"
```

The literal secret above is fake test configuration. The database is a new empty
SQLite schema created with `Base.metadata.create_all` only on that disposable
engine. Individual fixtures create additional isolated databases. Prior economy
PostgreSQL proof is reused; no production PostgreSQL or Key Value was contacted.

Redis 7.0.15 was extracted from Ubuntu packages under the temporary directory,
with its required libraries, without installing a system service. It listens only
on `127.0.0.1:56389`, with persistence disabled. Tests use database 15, UUID keys,
and require `WW_TEST_REDIS_URL=redis://127.0.0.1:56389/15` plus
`WW_TEST_REDIS_DIR=/tmp/ww-security-round1-continuation.z1hkSz`. The fixture verifies
both the server's configured directory and its process ID against the disposable
pidfile before using it. No arbitrary caller-supplied Redis target is accepted.
redis-py was installed only under the temporary `deps` directory, leaving the
original virtualenv unchanged.

Below, `RUN` denotes the absolute runner path above. Commands ran from the existing
security application worktree. Each also used its own `--basetemp=/dev/shm/ww-security-...`
and `-o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/cache`; exact command
variants and log names are shown here for reproduction:

```bash
# Baseline before continuation edits: 102 passed (baseline.txt)
RUN pytest -q --basetemp=/dev/shm/ww-security-continuation-baseline -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/cache tests/test_account_state_sync.py tests/test_verifier_relationships.py tests/test_site_admin_csrf.py tests/test_memberships.py tests/test_analytics.py

# Session phase: 76 passed (sessions.txt)
RUN pytest -q --basetemp=/dev/shm/ww-security-session -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/cache tests/test_session_hardening.py tests/test_site_admin_csrf.py tests/test_memberships.py tests/test_account_deletion.py tests/test_contest_admin.py

# Initial limiter phase: 85 passed (limits.txt)
RUN env WW_TEST_REDIS_URL=redis://127.0.0.1:56389/15 WW_TEST_REDIS_DIR=/tmp/ww-security-round1-continuation.z1hkSz pytest -q --basetemp=/dev/shm/ww-security-limits -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/cache tests/test_login_limits.py tests/test_site_admin_csrf.py tests/test_account_state_sync.py tests/test_verifier_relationships.py tests/test_analytics.py

# Invitation bypass and affected regressions: 85 passed, 2 skipped (limits-final.txt)
RUN env WW_TEST_REDIS_URL=redis://127.0.0.1:56389/15 WW_TEST_REDIS_DIR=/tmp/ww-security-round1-continuation.z1hkSz pytest -q --basetemp=/dev/shm/ww-security-limits-final -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/cache tests/test_login_limits.py tests/test_email_delivery.py tests/test_verifier_relationships.py tests/test_memberships.py

# Authorization: 231 passed (authorization.txt)
RUN pytest -q --basetemp=/dev/shm/ww-security-authorization -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/cache tests/test_security_authorization.py tests/test_store_placement.py tests/test_reward_inventory_bridge.py tests/test_memberships.py tests/test_verifier_relationships.py tests/test_band_director_roster.py tests/test_practice_insights.py tests/test_director_teams.py tests/test_analytics.py tests/test_arcade_economy.py

# Additional public/admin/contest boundaries: 55 passed (boundaries.txt)
RUN pytest -q --basetemp=/dev/shm/ww-security-public-boundaries -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/cache tests/test_team_moderation.py tests/test_teams.py tests/test_contests.py::test_endpoint_requires_authentication_and_exposes_no_private_data tests/test_contests.py::test_results_are_immutable_private_and_preserve_historical_data tests/test_contests.py::test_crown_progress_endpoint_authentication_and_privacy tests/test_director_dashboard.py::test_contest_authorization_and_hall_are_private_roster_safe tests/test_security_authorization.py tests/test_session_hardening.py

# Final combined new tests after review: 59 passed (final-gates.txt)
RUN env WW_TEST_REDIS_URL=redis://127.0.0.1:56389/15 WW_TEST_REDIS_DIR=/tmp/ww-security-round1-continuation.z1hkSz pytest -q --basetemp=/dev/shm/ww-security-final-gates -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/cache tests/test_login_limits.py tests/test_session_hardening.py tests/test_security_authorization.py

# One final full regression run after application/test edits (full-regression.txt)
RUN env WW_TEST_REDIS_URL=redis://127.0.0.1:56389/15 WW_TEST_REDIS_DIR=/tmp/ww-security-round1-continuation.z1hkSz pytest -q --basetemp=/dev/shm/ww-security-cumulative-full -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/full-cache

# JavaScript: 36 passed (javascript.txt)
RUN node --test tests/*.js
```

These are overlapping focused runs, not additive counts of unique tests. Existing
assertions and tests were not weakened or removed in the continuation. The new
Arcade characterization deliberately documents an OPEN successful attack, not a
negative-test claim that score forgery has been fixed.

## Additional changed-file audit

The economy-phase file audit remains applicable. Continuation changes are:

| File | Necessary reason |
| --- | --- |
| `app/session_config.py` (new) | Shared production/local session validation for finding 3. |
| `app/main.py` | Apply validated signing/cookie settings; log non-secret limiter configuration state. |
| `app/accounts.py` | Use validated key for existing retired-ID HMAC; remove its now-unused `os` import. |
| `app/site_admin.py` | Use validated key for site-admin fingerprint. |
| `app/contest_admin.py` | Use validated key for separate contest-admin fingerprint. |
| `app/login_limits.py` (new) | Atomic shared counters, local test backend, proxy trust, failure/status behavior for finding 2. |
| `app/account_routes.py` | Add student login limit; retain all earlier economy changes. |
| `app/verifier_routes.py` | Protect verifier login and existing-PIN invitation acceptance with shared limits. |
| `app/membership_routes.py` | Limit site-admin login outside event loop; add protected non-cached diagnostic. |
| `app/arcade_routes.py` | Narrow sensitive exception/header logging correction; no game protocol/rule changes. |
| `requirements.txt` | Add redis-py only; no existing dependency upgrade. |
| `tests/test_session_hardening.py` (new) | Startup, local/production, cookie and all HMAC-use regressions. |
| `tests/test_login_limits.py` (new) | Authentication, normalization, outage, proxy, concurrency/process and expiry attacks. |
| `tests/test_security_authorization.py` (new) | Additional cross-user/admin tests, Arcade OPEN-finding characterization, sensitive-log regression. |
| `docs/security-round1-server-economy.md` | Same cumulative report, with preserved economy evidence. |

No additional UI/template/CSS/JavaScript changes were made in this continuation.
No migrations/schema, billing architecture or student PIN design changed. New
files were inspected directly, not staged. All changes remain unstaged.

## Infrastructure and release actions for Jeff — outside this repository

1. Review the cumulative diff and unresolved game integrity decision before any
   unrestricted-public-beta clearance. No commit/release has been performed.
2. Verify that the existing production session secret is strong, private, at least
   32 characters, and not the development key; retain the existing value if valid.
   Retain `SESSION_COOKIE_SECURE=true`. On non-Render production, set
   `APP_ENV=production`. Do not paste secret values into reports or chat.
3. Provision/configure a Redis-compatible Render Key Value service for shared
   counters, reachable privately by every web process. Use a common stable
   session key, backend and namespace across processes. Ensure appropriate TLS
   where needed, ACL permission for the Lua counter commands, and a non-evicting
   policy/capacity: eviction/restarts/flushes can reset rate-limit windows.
4. Supply `LOGIN_RATE_LIMIT_REDIS_URL` securely. Configure mode `redis`; set
   `LOGIN_RATE_LIMIT_REQUIRED=true` for the beta gate. Default mode off is a safe
   merge rollout, not security clearance. Install the added dependency through
   the normal reviewed requirements/release process.
5. Verify the real ingress/header-sanitization contract and raw peer chain.
   Explicitly override Render's wildcard `FORWARDED_ALLOW_IPS` with an empty value;
   review the actual launch command for `--no-proxy-headers` and no CLI override.
   Configure only verified `LOGIN_TRUSTED_PROXY_CIDRS`. Confirm distinct real
   source IPs and spoofed-header resistance in an isolated staging deployment,
   across multiple workers, before asserting per-IP production enforcement.
6. Check the protected diagnostic, real valid-login recovery, 429/expiration and
   a controlled test-backend outage returning 503. A configured URL or successful
   Redis probe alone is insufficient proof of deployed protection. Verify HTTPS
   links, origin checks and secure cookies after proxy configuration changes.
7. Review/disable/redact sensitive URL paths in application-server and proxy
   access logs and confirm retention/access controls. No logging infrastructure
   was inspected or changed here.
8. Keep PIN recovery and the Arcade trusted-evidence proposal as separate work.
   Do not imply a verified reset procedure or fully trustworthy earned scores.

## Continuation final regression and export record

### Full regression outcome

One cumulative full suite was run after application edits: **1510 passed, 11 failed,
188 skipped in 484.03 seconds** (1709 collected). Exact command is in the continuation
validation section; full output: `/tmp/ww-security-round1-continuation.z1hkSz/full-regression.txt`.
The ten previously documented failing identities all recur with the same causes:

| Test identity (under `tests/`) | Confirmed existing cause |
| --- | --- |
| `test_board_standings.py::test_live_scoreboard_javascript_uses_actual_ranks_and_preserves_ties` | Obsolete minute-only JavaScript string assertion. |
| `test_board_standings.py::test_live_leaderboard_rows_show_numeric_scores_without_repeated_units` | Obsolete `total_minutes` assignment assertion. |
| `test_contests.py::test_existing_finalized_student_scores_are_not_rewritten` | Historical fixture has unknown scoring mode; existing 409. |
| `test_phase5_persistence.py::test_contest_models_match_approved_foundation` | Expected columns omit existing practice scoring mode. |
| `test_phase6_mobile_assets.py::test_rendered_pages_use_one_current_stylesheet_version` | Old CSS version 119 versus existing 120. |
| `test_phase6a.py::test_book_actions_precede_spiral_and_metallic_statistics_page` | Obsolete markup assertion. |
| `test_phase6c_audio.py::test_tone_is_exactly_pinned_local_licensed_and_loaded_in_order` | Obsolete asset substring assertion. |
| `test_production_hotfix.py::test_instrument_assets_are_cache_busted_and_failures_are_visible` | Old CSS version 119 assertion fails first. |
| `test_team_continuity.py::test_join_code_collision_and_no_automatic_activation` | Existing maintenance import violates older source-text assertion. |
| `test_trusted_verifier_dashboard.py::test_shared_metrics_completed_rating_and_private_note_isolation` | Existing seconds fields absent from expected dictionary. |

The eleventh failure was the new
`test_security_authorization.py::test_database_failures_do_not_log_sql_tokens_or_arbitrary_headers`:
its safe-log assertion captured no output after an earlier migration test.
`migrations/env.py` calls `logging.config.fileConfig` with its default disabling of
existing loggers. This was reproduced independently with the two analytics migration
tests immediately preceding the new log test (**2 passed, 1 failed**, `log-order-before.txt`).
The new test now uses `monkeypatch` to enable only the tested logger and sets its
capture level; both are restored afterward. All original redaction, response and
safe-attribution assertions remain. No application logging configuration, migration,
or existing test was changed to resolve this order dependency.

The following targeted migration-first repeat then passed **61 tests**, including
all new limiter, session and authorization tests and real Redis multiprocess checks:

```bash
/tmp/ww-security-round1-continuation.z1hkSz/run-safe pytest -q tests/test_analytics_migration.py tests/test_security_authorization.py::test_database_failures_do_not_log_sql_tokens_or_arbitrary_headers --basetemp=/dev/shm/ww-security-log-order -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/log-order-cache
/tmp/ww-security-round1-continuation.z1hkSz/run-safe env WW_TEST_REDIS_URL=redis://127.0.0.1:56389/15 WW_TEST_REDIS_DIR=/tmp/ww-security-round1-continuation.z1hkSz pytest -q tests/test_analytics_migration.py tests/test_login_limits.py tests/test_session_hardening.py tests/test_security_authorization.py --basetemp=/dev/shm/ww-security-log-order-fixed -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/log-order-fixed-cache
```

Logs: `log-order-before.txt` and `log-order-fixed.txt` in the evidence directory.
The full suite was not repeated after this test-only isolation correction; its
recorded totals are not rewritten as an all-green run. No remaining new application
regression was observed. The 188 skipped cases include opt-in PostgreSQL variants;
retained disposable PostgreSQL economy/concurrency evidence above remains the
relevant proof, not the SQLite skips. JavaScript: **36 passed**; Python compilation:
**62 files**, `app.main` import successful; JavaScript syntax: **32 files**.

### Final scope and working-tree audit

Starting and final HEAD: `efee4d1f3a79ed57505c6b7db897968dd956e256`.
Branch: `security/round1-server-economy`. App worktree:
`/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck`.
Original branch remains `main` at the same HEAD. Its only reported changes remain
its two original untracked files. The database backup was never read or copied;
neither original file was modified or staged. Existing economy work was preserved
and continued only within authorized scope.

The entire tracked diff and new files were reviewed directly. The per-file tables
above cover all 25 modified tracked files and nine new files. No unrelated files,
CSS changes, file moves, broad formatting, debug code, actual secrets or schema/
migration changes were introduced. No legitimate feature was intentionally removed;
authoritative balance enforcement and throttling intentionally reject abusive input.
The sole template change remains the economy JavaScript cache versions. No existing
tests were disabled or deleted. Changes remain **unstaged and uncommitted**.
`git diff --check` passed. Temporary worktree `.venv` symlink was removed and only
the owned disposable Redis process was stopped. No production resources were used.

Final `git status --short` (Git-root-relative):

```text
 M woodshed-woodchuck/app/account_routes.py
 M woodshed-woodchuck/app/accounts.py
 M woodshed-woodchuck/app/arcade_rewards.py
 M woodshed-woodchuck/app/arcade_routes.py
 M woodshed-woodchuck/app/contest_admin.py
 M woodshed-woodchuck/app/contests.py
 M woodshed-woodchuck/app/login_streaks.py
 M woodshed-woodchuck/app/main.py
 M woodshed-woodchuck/app/membership_routes.py
 M woodshed-woodchuck/app/practice_chart_routes.py
 M woodshed-woodchuck/app/practice_charts.py
 M woodshed-woodchuck/app/site_admin.py
 M woodshed-woodchuck/app/store_inventory.py
 M woodshed-woodchuck/app/store_routes.py
 M woodshed-woodchuck/app/verifier_routes.py
 M woodshed-woodchuck/requirements.txt
 M woodshed-woodchuck/static/js/account.js
 M woodshed-woodchuck/static/js/app.js
 M woodshed-woodchuck/static/js/state.js
 M woodshed-woodchuck/templates/base.html
 M woodshed-woodchuck/tests/test_account_state_sync.py
 M woodshed-woodchuck/tests/test_phase6b_shop.py
 M woodshed-woodchuck/tests/test_phase8_stabilization_book.py
 M woodshed-woodchuck/tests/test_production_hotfix.py
 M woodshed-woodchuck/tests/test_quest_completion_persistence.py
?? woodshed-woodchuck/app/economy.py
?? woodshed-woodchuck/app/login_limits.py
?? woodshed-woodchuck/app/session_config.py
?? woodshed-woodchuck/docs/security-round1-server-economy.md
?? woodshed-woodchuck/tests/test_login_limits.py
?? woodshed-woodchuck/tests/test_security_authorization.py
?? woodshed-woodchuck/tests/test_server_economy.js
?? woodshed-woodchuck/tests/test_server_economy.py
?? woodshed-woodchuck/tests/test_session_hardening.py
```

### Deliverables and remaining gates

Cumulative recovery patch, including new files (without staging):
`/tmp/ww-security-round1-continuation.z1hkSz/cumulative-security-round1.patch`.
This snapshot follows the successful focused phase gates and final review; it does
not imply separate historical snapshots were taken after every continuation phase.
All lengthy test logs remain outside Git.

Report export: `/home/geph/Downloads/Woodshed_Security_Round1_Report.md`.

**Overall PARTIAL / no public-beta security clearance.** Generic forged-balance
attacks are blocked locally with preserved earning, purchase and conflict behavior.
Local throttling, session hardening, and covered authorization/logging proofs are
complete. Deployment is unchanged. Required remaining work includes verified
production Redis/proxy configuration, an Arcade trusted-score proposal, historical
balance provenance decisions, and sensitive-path access-log review. PIN recovery
remains separate. No commit, push, merge, deployment, infrastructure modification,
production migration, real email or real payment action was performed.

## Review follow-up — IP quota poisoning, delayed responses, contest-admin credentials

This section supersedes the earlier continuation's completeness claims for the
three reviewed gaps. Earlier economy, PostgreSQL, baseline and regression evidence
is retained as historical evidence. Work continued on the same branch and HEAD,
with all cumulative changes preserved; no original-checkout changes, staging,
commits, deployment, production access or infrastructure actions occurred.

### 1. Blocked-IP account-quota poisoning — FIXED locally

**Reproduction:** exhaust 100 student/verifier attempts from one IP using distinct
unknown identities; send ten rejected attempts targeting a fresh real test account;
submit that account's correct PIN from a second IP. Before correction, the victim
received 429. This reproduced in all four combinations (student/verifier × memory/
disposable Redis): **4 failed, 20 deselected**, with the failure at the expected
successful victim login (`review-poison-before.txt`). No real account was used.

Both implementations now reserve **IP first**, atomically under the local lock or
within the Redis Lua script. If the IP reservation exceeds its limit, the operation
returns immediately without creating/incrementing the account key. An admitted IP
still consumes its attempt when an account is blocked; this retains spray protection.
Counts retain their original fixed expiry, rejected attempts do not extend it, and
account limits, normalized identities, strict admin limits, secret-free keys,
proxy validation and fail-closed outage behavior are unchanged. Redis returns a
positive rejection even if PTTL reaches zero milliseconds just before expiration.

Proof now includes:

- Correct victim login succeeds from a second IP after all ten poisoning requests
  receive 429; victim account counter is exactly one. Both backends and both
  credential types pass.
- At IP count 99, 40 concurrent requests target distinct accounts: exactly one is
  admitted and the sum of all account counters is one. Local threads and four
  independent Redis client processes pass. Rejected requests create no victim keys.
- Existing account/IP limits, multiple-process atomicity, automatic expiration,
  recoverable mistakes, spoofed-forwarding resistance, outages and secret redaction
  tests remain passing.

These local tests do not establish operational production throttling.

### 2. Delayed browser economy responses — FIXED locally for covered flows

The review was correct: comparing a response with a pre-await state copy did not
protect the state subsequently stored by BOOK and Board. Those handlers could
replace a newer balance/revision and unrelated changes with that captured copy.

`state.js` now supplies a small request-account snapshot and `stateForResponse`.
A handler captures account identity/authentication and the in-page account generation
before awaiting. After the response, it obtains the current stored state; a changed
identity/authentication/generation causes it to ignore the response. The generation
also detects an in-page logout followed by signing into the same account. Handlers
modify only their current fields on this fresh state, and `applyEconomy` compares
against the latest saved revision. Board date preparation is reapplied to the fresh
state. BOOK also avoids replacing a newer streak with an older response.

Covered writers: BOOK submission; Board hours, care, trivia and marching; Bonus
Challenge completion; daily secret; login-streak balance; SHOP purchase; account
sync; and shared Arcade status/start/completion balance storage. The directly related
Bonus Challenge loader is guarded against account switching too. SHOP/Arcade balance
labels use the accepted stored balance rather than an older payload balance.

Sync responses for 200, 401 and 409 are checked against the initiating account;
conflict-recovery GET additionally checks returned account identity and revision.
The existing server-conflict backup/reload policy remains. This is a browser
consistency guard, not a replacement for server identity/ownership or economy checks.
No score protocol, reward formula, request endpoint or database schema changed.

**Behavioral proof uses real state.js localStorage saves and the actual application
handler source, with only DOM/network edges mocked:**

- Board care and marching are both pending. Newer marching response saves revision
  12/balance 22 first; a user changes their goal; care revision 11/balance 21 arrives
  last. Saved balance/revision and goal remain current, and both completion flags survive.
- BOOK waits while a newer Board response saves. BOOK's older result cannot replace
  funds, revision, newer streak, goal or the Board completion flag.
- Pending BOOK and Board responses are ignored after logout, account switch, and
  in-page logout/re-login to the same identity.
- Actual account-sync success cannot replace newer economy/user edits. Responses
  with 200/401/409 cannot affect a switched account. Conflict recovery cannot restore
  an account after logout.
- Actual shared Arcade status requests delivered out of order preserve the newer
  balance/revision; a response for the previous account cannot change the next account.

The browser behavioral suite contains **16 passing cases** within the complete
JavaScript suite of **50 passed, zero failed**. An existing login-streak source-text
assertion expected a direct `payload.dandelion_balance` assignment. It now checks
that the handler uses the account/revision helper, has no direct credit assignment,
and the helper reads that server field. This expectation change follows the security
correction; its other UI and server-derived-streak assertions remain intact.

### 3. Separate contest-admin credential checks — FIXED locally

Source trace found two credential-checking implementations:

1. `contest_admin.require_contest_admin`: nine routes under `/contests/admin`.
   An existing valid contest-admin session fingerprint continues to authorize normal
   navigation without spending guessing quota. Otherwise, the shared limiter runs
   before comparing `X-Contest-Admin-Token` with `CONTEST_ADMIN_TOKEN`.
2. `contests.finalize_week_route`: `POST /contests/weeks/{week_start}/finalize`.
   It remains header-only: even an authenticated contest-admin session cannot replace
   its required header. The same limiter runs before its header comparison.

All ten routes share a **contest_admin IP bucket, five attempts per 900 seconds**.
The bucket is separate from site-admin login, whose credential is `SITE_ADMIN_TOKEN`
and whose fingerprint/session boundary remains distinct. No credential enters a
limiter key. The existing unavailable-token 503, wrong-token 403, valid session
behavior and route authorization semantics are retained; excessive guesses yield 429.
When protection is required but cannot initialize, credential checks return 503.
Existing authenticated contest-admin sessions are not fresh credential guesses and
remain usable; the header-only finalization endpoint still needs the limiter.

Entry points tested: GET `/contests/admin`; POST `band-directors`,
`band-directors/{id}/revoke`, `teams/{id}/moderation`, `team-reports/{id}/resolve`,
`finalize-current`, `finalize-due`, `readiness`, `rollover`; and the separate
`/contests/weeks/{week_start}/finalize` route. Alternating guesses across the two
implementations cannot evade the common bucket. Tests prove recovery after expiry,
independent IPs, valid header authentication, reusable contest session, header-only
finalization, rejected site token on contest routes, rejected contest token on site
login, and no site-only session access to contest administration. Existing real
contest administration and finalization authorization tests also pass.

### Focused validation and exact commands

Environment: the previously documented `run-safe` clears inherited service settings,
uses isolated SQLite fixtures/default database, fake email/payment configuration and
external bytecode/cache paths. Disposable Redis is the same explicitly checked local
server on 127.0.0.1:56389, logical database 15, with persistence disabled. Tests verify
its directory and owned PID, use isolated keys, and never contact Render Key Value.
The owned process is stopped after validation. PostgreSQL economy lock code did not
change; prior disposable PostgreSQL proof is reused. The 36 opt-in PostgreSQL/SQLite
row-lock skips are recorded, not represented as newly executed concurrency tests.
No full suite was repeated: the existing baseline/full-suite comparison above remains
applicable, and this review uses focused checks for the concrete changes.

All commands below run from the isolated app directory. Logs named below reside in
`/tmp/ww-security-round1-continuation.z1hkSz/`, outside Git.

```bash
# Before fix: 4 failed, 20 deselected — expected attack reproduction.
/tmp/ww-security-round1-continuation.z1hkSz/run-safe env WW_TEST_REDIS_URL=redis://127.0.0.1:56389/15 WW_TEST_REDIS_DIR=/tmp/ww-security-round1-continuation.z1hkSz pytest -q tests/test_login_limits.py -k poison --basetemp=/dev/shm/ww-security-review-before -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/review-cache

# Final auth + contest/site-admin regressions: 58 passed, zero failed.
/tmp/ww-security-round1-continuation.z1hkSz/run-safe env WW_TEST_REDIS_URL=redis://127.0.0.1:56389/15 WW_TEST_REDIS_DIR=/tmp/ww-security-round1-continuation.z1hkSz pytest -q tests/test_login_limits.py tests/test_contest_admin.py tests/test_site_admin_csrf.py --basetemp=/dev/shm/ww-security-review-auth-confirmed -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/review-cache

# Affected workflows: 161 passed, 36 skipped, zero failed.
/tmp/ww-security-round1-continuation.z1hkSz/run-safe pytest -q tests/test_account_state_sync.py tests/test_server_economy.py tests/test_arcade_economy.py tests/test_arcade.py tests/test_login_streaks.py tests/test_quest_completion_persistence.py tests/test_phase6b_shop.py tests/test_phase8_stabilization_book.py tests/test_phase6_regressions.py --basetemp=/dev/shm/ww-security-review-workflows -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/review-cache

# Focused UI/manual-finalization compatibility: 20 passed, zero failed.
/tmp/ww-security-round1-continuation.z1hkSz/run-safe pytest -q tests/test_login_streak_ui.py tests/test_book_board_polish.py tests/test_phase8_i_played_it.py tests/test_phase6_control_refinements.py tests/test_contests.py::test_manual_route_missing_invalid_token_and_results_authentication --basetemp=/dev/shm/ww-security-review-ui-confirmed -o cache_dir=/tmp/ww-security-round1-continuation.z1hkSz/review-cache

# All JavaScript tests, including actual asynchronous state-save flows: 50 passed.
/tmp/ww-security-round1-continuation.z1hkSz/run-safe node --test tests/*.js

git diff --check
```

Final logs: `review-auth-confirmed.txt`, `review-workflows.txt`,
`review-ui-confirmed.txt`, `review-js-confirmed.txt`. Earlier focused auth pass:
56 tests before adding the two concurrent-boundary cases; final 58 includes them.
A standalone limiter run passed 39 cases. Intermediate UI run had 19 passed/one failed
because of the direct-assignment assertion described above; the final corrected
20-test run is the relevant result. JavaScript harness scaffolding errors during
construction were corrected without weakening application assertions.

Python compilation/import and JavaScript syntax validation also passed:

```bash
/tmp/ww-security-round1-continuation.z1hkSz/run-safe python - <<'PY_CHECK'
from pathlib import Path
import py_compile, subprocess
paths = list(Path('app').glob('*.py'))
for path in paths:
    py_compile.compile(str(path), doraise=True)
import app.main
javascript = list(Path('static/js').glob('*.js'))
for path in javascript:
    subprocess.run(['node', '--check', str(path)], check=True)
print(len(paths), len(javascript))
PY_CHECK
```

Result: **62 Python files compiled, app.main imported, 32 JavaScript files parsed**.
This review adds no dependency, migrations or test schema changes. The application
code received no changes after final checks except explanatory comments. The last
existing test expectation adjustment was independently rerun as documented.

### Review changed-file audit

Earlier cumulative file audits still apply. This follow-up changes:

| File | Necessary reason |
| --- | --- |
| `app/login_limits.py` | Atomic IP-first reservation; independent strict contest-admin bucket. |
| `app/contest_admin.py` | Throttle fresh header credentials after existing-session check. |
| `app/contests.py` | Same contest bucket for the separate header-only finalization route. |
| `static/js/state.js` | Request-account snapshot/generation and fresh response-state helper. |
| `static/js/app.js` | Fresh BOOK/Board and related economy response saves; identity guards; current balance labels. |
| `static/js/account.js` | Guard sync success/unauthorized/conflict responses and recovery against account changes. |
| `static/js/arcade-economy.js` | Revision/account-safe shared balance storage; no score/reward protocol change. |
| `templates/arcade.html` | Arcade economy cache version 3 to 4 only. |
| `templates/arcade_game.html` | Same cache-version update only. |
| `templates/dressed_to_the_nines.html` | Same cache-version update only. |
| `templates/history_mystery.html` | Same cache-version update only. |
| `templates/interval_basic_training.html` | Same cache-version update only. |
| `templates/plunge_burrow.html` | Same cache-version update only. |
| `templates/scale_keyboard.html` | Same cache-version update only. |
| `templates/thirds.html` | Same cache-version update only. |
| `templates/wheel_of_woodchuck.html` | Same cache-version update only. |
| `tests/test_login_limits.py` | Poisoning, concurrent IP boundary, all contest entry points and separate authorization proofs. |
| `tests/test_server_economy.js` | Actual asynchronous handler/save-flow regression tests. |
| `tests/test_arcade.py` | Existing two asset-version assertions now expect the necessary version 4. |
| `tests/test_login_streak_ui.py` | Expect guarded server-balance application instead of direct assignment. |
| `docs/security-round1-server-economy.md` | This appended review evidence; prior content preserved. |

No unrelated cleanup or formatting changes, files outside Woodshed, new secrets,
removed features, commits, staging or infrastructure modifications. Original checkout
still reports only its two pre-existing untracked files. The database backup was not
read or copied. Final cumulative tree has 37 modified tracked files and nine untracked
files; nothing staged. These counts include all preserved earlier phases.

### Remaining deployment gates and export

All three review corrections are **local**, not deployed protection. Production
rate limiting remains uncleared until shared Render Key Value, strict required-mode
configuration, verified proxy trust/raw-peer handling, spoof-resistance and outage
behavior are configured and verified through the approved release process. Include
contest-admin routes in that operational verification. Existing Arcade score
forgery, historical balance provenance, sensitive-path access-log review and separate
PIN recovery findings remain as previously classified. No production environment,
service configuration or database was changed.

Updated timestamped report: `/home/geph/Downloads/Woodshed_Security_Round1_Report_20260916T025355605870Z.md`.

Updated cumulative patch (tracked and new files): `/home/geph/Downloads/Woodshed_Security_Round1_Cumulative_20260916T025355605870Z.patch`.
Previous Downloads exports are preserved.

Final tracked/new-file whitespace checks passed. The cumulative patch was checked
against a temporary copy of the changed files at the recorded HEAD, using
`git apply --check`; original and security working trees were not used as apply targets.

## Release preparation update — Jeff-confirmed Key Value provisioning

The reviewed implementation is preserved in local commit
`a4db762da0092bf585493ad4cdacf61c5e872334` on
`security/round1-server-economy`. This update is documentation only. Previous
statements about work being uncommitted or Key Value not yet provisioned describe
those earlier checkpoints; the current facts below supersede that status only.
The application implementation and existing test evidence are unchanged.

### User-confirmed facts and verification boundary

Jeff reports the following; these are user-confirmed facts, not observations from
assistant access to Render:

- Render Key Value `woodshed-login-limits` is **Available**.
- It is on the **Free** plan, in the same region as the Woodshed web service.
- Its maxmemory policy is **`noeviction`**.
- Its Internal URL was saved on the web service as
  **`LOGIN_RATE_LIMIT_REDIS_URL`**, using **Save only**.
- No deployment or limiter activation has occurred.

No connection URL or secret value is reproduced here. Render states that Save only
saves a setting without applying it to the running service until the next deploy.
The saved URL therefore does not establish active runtime configuration, backend
connectivity, or enforcement. Production limiting remains **not activated and not
verified**. [Render environment-variable save behavior](https://render.com/docs/configure-environment-variables)

### Required paid-plan release gate

- [ ] **Before production limiter activation, upgrade `woodshed-login-limits` to
  the $10/month paid Key Value plan, then verify connectivity and enforcement.**
  **Jeff approves the expense; Product/Operations owns verification and records
  the evidence.** This item remains unchecked; no approval or upgrade is inferred
  from this report. Confirm the displayed charge when approving the purchase.

Render currently lists the 256 MB Key Value tier at $10/month. Its Free guidance
excludes production use and says Free Key Value loses data on restart and during
upgrade to a paid plan. Thus the upgrade must precede reliance on limiter counters.
[Render pricing](https://render.com/pricing),
[Free Key Value limitations](https://render.com/docs/free)

Product/Operations should confirm the paid instance's persistence mode and retain
`noeviction`. That policy returns write errors at memory exhaustion; the committed
limiter converts backend failures into generic 503 sign-in responses, with no memory
fallback. Capacity and persistence need operational verification: paid storage does
not by itself guarantee uninterrupted counters. Internal connectivity also requires
the same workspace as well as the reported same region. Do not enable external
access simply to test the Internal URL from a laptop.
[Render Key Value networking, persistence and eviction](https://render.com/docs/key-value)

### Next configuration decision from the committed implementation

**First obtain the actual web-service Start Command.** README.md shows only a typical
Uvicorn command; it does not prove what Render currently launches. The launch command
may invoke Uvicorn directly, Gunicorn workers, a wrapper or a container entrypoint,
each of which can change how forwarded headers reach `request.client.host`.

Render's documented Python default is `FORWARDED_ALLOW_IPS=*`. In contrast,
`app/login_limits.py::_settings` requires an **explicitly empty** environment value
when production limiting is enabled. A missing variable or literal quote characters
are not equivalent to an empty string. The application must receive the actual socket
peer; the environment check cannot detect a command-line or wrapper override that
rewrites it first. [Render default environment variables](https://render.com/docs/environment-variables)

For a confirmed direct Uvicorn launch, the planned raw-peer setting is
`--no-proxy-headers`, together with explicit empty `FORWARDED_ALLOW_IPS`. Uvicorn's
proxy-header handling affects both client address and scheme. Do not paste this flag
into an unverified Gunicorn/wrapper command; inspect the effective worker and options
first. This is preparation for a separately approved release, not an instruction to
save or deploy changes now. [Uvicorn settings](https://www.uvicorn.org/settings/)

After the proxy and HTTPS gates below are satisfied, the prepared activation settings
are `LOGIN_RATE_LIMIT_MODE=redis` and `LOGIN_RATE_LIMIT_REQUIRED=true`, using the
already saved Internal URL. Defaults remain off/false before activation. Enabled
Redis failures fail closed; required mode rejects off/memory configuration. The URL
alone neither enables the limiter nor establishes operational protection.

### Evidence required before choosing trusted proxy CIDRs

The committed `source_ip` implementation trusts X-Forwarded-For only when the raw
socket peer is in `LOGIN_TRUSTED_PROXY_CIDRS`. It then scans the chain from right to
left, skips verified proxy addresses and selects the first untrusted address.
An empty allowlist uses the socket peer: safe against arbitrary client headers, but
behind shared ingress it aggregates users into a proxy bucket and cannot establish
accurate per-client enforcement.

Render documents traffic through Cloudflare and Render load balancers, so the raw
peer normally represents a proxy. Its PocketBase guidance also warns that a client
can supply the leftmost X-Forwarded-For value. Those facts do not establish all trusted
hop ranges or header handling for this service.
[Render ingress/client-IP guidance](https://render.com/articles/how-render-handles-ddos-attacks),
[Render warning about forged leftmost X-Forwarded-For](https://render.com/articles/host-pocketbase-on-render)

Product/Operations needs the following evidence before approving an allowlist:

1. The actual launch command, worker/proxy middleware and effective forwarding
   settings, showing that `request.client.host` reaches the limiter unmodified.
2. A Render-supported explanation of the ingress path for this service: immediate
   peer ranges, intermediate X-Forwarded-For proxy ranges, whether clients can reach
   the app through another path, and which hop appends, replaces or sanitizes supplied
   headers. If current public documentation cannot establish stable trusted ranges,
   obtain confirmation from Render support; no support message was sent in this task.
3. In an approved isolated staging exercise with disposable identities, record the
   raw peer, complete forwarded chain and selected limiter IP for two known source
   networks. Correlate each selected IP with its known client. Repeat with forged
   X-Forwarded-For prefixes, extra headers and alternate supported hostnames, across
   application processes. Evidence must show both correct attribution and resistance
   to spoofing; a plausible-looking IP from one request is insufficient.

Never substitute guessed private ranges, `0.0.0.0/0`, `::/0`, Render outbound ranges,
or a Cloudflare list alone for evidence of the entire chain. Render's outbound IP
ranges describe egress to external services, not a verified inbound peer allowlist.
[Render outbound address scope](https://render.com/docs/outbound-ip-addresses)

**No proxy CIDRs are proposed or approved here.** If Render cannot establish a stable
trust contract compatible with this implementation, leave activation blocked and
return the incompatibility to Product/Security for a separate change proposal.
The committed code does not read CF-Connecting-IP or dynamically discover proxies;
configuration cannot silently substitute a different IP algorithm.

### HTTPS, sessions and CSRF compatibility gate

Render terminates public HTTPS at its load balancer and forwards HTTP to the app.
Disabling forwarded-scheme handling therefore affects the app's view of the URL even
while the browser continues to use HTTPS.
[Render web-service TLS termination](https://render.com/docs/web-services)

Code-specific consequences to verify in the approved staging/release process:

- Preserve the existing strong `SESSION_SECRET` and `SESSION_COOKIE_SECURE=true`.
  `session_config.py` enforces production secret/cookie requirements;
  `main.py` sets secure cookies with SameSite=Lax. Do not weaken cookies or rotate
  the secret to work around proxy configuration. Verify session continuity and
  ordinary student/verifier/admin sign-in and sign-out over HTTPS.
- Confirm `PUBLIC_BASE_URL` is the actual canonical HTTPS user-facing origin.
  `site_admin.check_csrf` prefers it, then `RENDER_EXTERNAL_URL`, for expected
  host/port. A custom domain can differ from Render's onrender.com fallback. Normal
  Origin checks compare normalized host/port; without configured origin, an internal
  HTTP scheme can imply port 80 and reject an HTTPS origin on port 443.
- The special `Origin: null` branch also compares the request-derived host/port,
  even with a configured public origin; explicit PUBLIC_BASE_URL alone does not
  prove that path works. Preserve established rejection semantics and validate
  legitimate admin/membership forms, CSRF rotation and foreign-origin rejection.
- Check redirects and generated invitation/verification links stay HTTPS on the
  correct host. `email_service.public_link` prefers PUBLIC_BASE_URL; its fallback
  can use a caller-supplied local base. A configured public base does not globally
  repair every request-derived URL. Use fake email/payment delivery and test
  identities for staging verification.

A scheme or origin regression is an unresolved release dependency. Do not restore
wildcard proxy trust or disable CSRF to bypass it.

### Operational acceptance and retained test evidence

After the paid upgrade and approved configuration/release, Product/Operations must
verify private connectivity and actual Lua counter/expiration operations, shared
counts across workers, accurate client-IP selection, 429 limits and recovery for
students, verifiers, site admins and both contest-admin credential paths. Repeat
blocked-IP poisoning and spoofed-header checks using designated test identities.
Controlled backend-unavailable/fail-closed exercises belong in isolated staging,
not destructive requests or forced outages against beta users.

A Redis connection or PING is only connectivity evidence. Even a successful protected
`/admin/security/rate-limit` diagnostic means **backend_operational**, not production
verification; its `production_verified` field intentionally remains false. The
required expense, proxy trust, HTTPS/CSRF and enforcement evidence must all be recorded
before Security clears activation/public beta.

Existing reviewed evidence is reused: 58 authentication/admin tests, 161 affected
workflow tests (36 expected skips), 20 UI/manual-finalization checks and 50 JavaScript
tests; earlier disposable PostgreSQL proofs and baseline failure identities remain
above. No tests were rerun for this documentation change. Prior application security
findings, including Arcade score authenticity and the separate PIN-recovery work,
retain their recorded status.

**Single next manual check for Jeff:** open the Woodshed web service in Render,
choose **Settings**, and copy the current **Start Command** exactly for Product/
Operations to review (redact any embedded secret). Do not edit or save it. That one
check identifies the server/worker path before selecting any proxy-setting change.

Updated documentation export: `/home/geph/Downloads/Woodshed_Security_Round1_Report_20260916T041959825221Z.md`. Previous exports are preserved.

## Runtime confirmation and focused client-IP validation procedure

### Observed runtime facts — supplied by Jeff

Jeff confirmed the following current Render runtime observations:

```text
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
FORWARDED_ALLOW_IPS='*'
LOGIN_TRUSTED_PROXY_CIDRS=None
```

These are recorded as Jeff-observed runtime facts, not results of assistant production
access. The earlier request to obtain the Start Command is complete. The previously
reported Free Key Value instance, `noeviction`, saved Internal URL, no deployment and
no limiter activation remain the current reported state. Commit `a4db762` is still
local reviewed implementation; these runtime observations do not establish its
presence in production.

Interpretation detail: `None` is retained exactly as reported. It must not be copied
as a literal configuration value. If it means the variable is absent, the committed
code treats it as an empty allowlist. A literal string `None` fails CIDR parsing once
limiting is enabled. Product/Operations must distinguish absent, empty, and literal
text when preparing the approved configuration, without exposing secret values.

### Decision: obtain the provider trust contract before choosing CIDRs

The smallest next step is a **provider-confirmation request**, using the draft below.
It requires no deployment, limiter activation or Key Value upgrade. Jeff or Product/
Operations sends it through the existing Render support channel; no message has been
sent by this assistant.

The direct Uvicorn command has no explicit proxy-header override, while the reported
forwarding trust is wildcard. An address observed through `request.client.host` in
that configuration may already have been rewritten from forwarded headers; it cannot
establish the original socket peer or a safe trusted subnet.

The committed production guard rejects wildcard forwarding trust when limiting is
enabled: simply setting mode to redis under the current configuration would produce
503 sign-in responses. Conversely, disabling Uvicorn rewriting without verified
proxy ranges makes `source_ip` use the socket peer, potentially sharing one bucket
among unrelated users. Neither state meets accurate client-IP enforcement.

The existing research remains applicable: Render documents its Cloudflare/load-
balancer ingress, and warns about trusting caller-controlled leftmost X-Forwarded-For
entries. These sources do not supply a service-specific complete trust contract.
[Render client-IP guidance](https://render.com/articles/how-render-handles-ddos-attacks),
[Render forwarded-header warning](https://render.com/articles/host-pocketbase-on-render),
[Render Python defaults](https://render.com/docs/environment-variables),
[Uvicorn settings](https://www.uvicorn.org/settings/)

**Reviewable provider request — copy for Jeff/Product to send:**

> We are preparing IP-based login throttling for a Render Python web service running
> `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Current forwarding trust is `*`.
> Our reviewed implementation requires Uvicorn to preserve the socket peer and then
> trusts X-Forwarded-For only from explicit proxy CIDRs, walking right-to-left past
> trusted proxy hops. Before configuring that allowlist, please confirm:
>
> 1. What stable, supported ranges identify the immediate ingress peers reaching
>    this service and all additional trusted proxy hops in X-Forwarded-For? How are
>    changes to those ranges communicated? Please distinguish ingress from outbound
>    service IPs and include IPv6 behavior where applicable.
> 2. In what order do Cloudflare and Render hops append, replace or sanitize
>    X-Forwarded-For? How are caller-supplied values and duplicate header fields
>    handled? Can untrusted requests supply an address to the right of the real client?
> 3. Do custom domains, the onrender.com hostname, optional external proxies and
>    private-network access follow different paths? Can another service or client
>    reach the app directly while appearing inside a proposed trusted proxy range?
> 4. Does Render support disabling Uvicorn proxy-header rewriting while preserving
>    the peer and complete chain needed by this algorithm? What scheme/host headers
>    does the ingress set, and can clients override them?
> 5. If stable trusted ranges cannot be guaranteed, what supported, authenticated or
>    sanitized client-IP mechanism should an application use instead, and what access
>    restrictions are needed to trust it?
>
> We need the supported trust guarantees and limitations for this service, not a
> subnet inferred from a sample address. We are not requesting a production change.

Attach the service identifier and region through Render's authenticated support UI
if needed, without sharing database URLs, the Key Value URL, cookies or secrets.
Product/Operations records the response and its applicability to the deployed path.
A single observed address, private-address classification, or a successful forwarded
header test cannot establish ownership of a CIDR or rule out alternate ingress.
Outbound Render ranges and Cloudflare ranges alone are not a complete allowlist.

If the answer cannot support this exact algorithm, keep client-IP assurance OPEN and
propose a narrow separate implementation change for review. The current code only
interprets X-Forwarded-For; switching to another provider header is not a configuration
option. No alternate-header implementation or guessed CIDR is introduced here.

### Future isolated validation — procedure prepared, not executed

**Owner:** Product/Operations. **Prerequisite:** provider reply reviewed, plus approval
for a staging deployment/configuration exercise. No production probing is part of
this procedure. Reuse an existing isolated Render staging service only after its
availability and isolation are confirmed; none is assumed to exist. If one does not
exist, creating it and any resulting expense require a separate proposal and Jeff's
approval. No new infrastructure is provisioned by this documentation task.

**Capture method:** the committed `/admin/security/rate-limit` route reports backend
state only. It does not expose the raw peer, header chain, selected client IP or scheme.
Product must first select existing private tracing that can capture those exact
fields, or obtain review for temporary staging-only instrumentation. It must invoke
the committed `source_ip` implementation, not a substitute parser. Such instrumentation
requires a separately approved edit/deployment; none is written here. Avoid a public
IP/header echo endpoint. Record only synthetic request IDs, raw peer, raw forwarded
IP fields (including duplicate fields), selected IP, scheme/host and worker identifier;
omit credentials, cookies, tokens and token-bearing URLs.

1. Prepare an isolated copy of commit `a4db762` with disposable accounts and data,
   fake email/payments and separate test limiter storage. Do not point staging at
   production PostgreSQL or `woodshed-login-limits`. Preserve the same relevant
   Render runtime/ingress settings and record any differences from production.
2. For this confirmed direct Uvicorn launch, review the candidate staging command:

   ```text
   uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-proxy-headers
   ```

   Set the staging `FORWARDED_ALLOW_IPS` value to an actual empty string, not quote
   characters and not an absent inherited wildcard. Use only provider-confirmed
   `LOGIN_TRUSTED_PROXY_CIDRS`. Record the effective command/settings, with secrets
   omitted. Keep limiting off for the initial attribution checks; no Redis connection
   is needed merely to evaluate `source_ip` through the approved capture method.
3. Use client A and client B on independently routed networks (for example Wi-Fi and
   cellular), with known different public egress IPs and no common VPN/proxy. Two
   browsers on the same router are not distinct source IPs. Record expected A/B
   addresses through an approved reference and correlate request IDs. Cover IPv4
   and IPv6 if supported by the deployment.
4. Execute the matrix below against the isolated staging service. For each row,
   record expected IP, peer/chain, selected IP, worker, observed result and pass/fail.
   Repeat normal and forged-header cases on both supported hostnames and across
   workers. Do not convert an observed peer into a trusted range after a failure.

| Staging case | Request variation | Acceptance criterion |
| --- | --- | --- |
| Baseline A and B | No supplied forwarding headers | Selected IP matches each known egress IP; A and B are distinct. |
| Forged singleton | A sends `X-Forwarded-For: 192.0.2.123` | Selected IP remains A, never the injected address. |
| Forged chain | A sends `X-Forwarded-For: 192.0.2.123, 198.51.100.77` | Selected IP remains A; arbitrary leftmost/rightmost caller values do not win. |
| Duplicate fields | A sends two separate X-Forwarded-For fields; repeat with reversed order | Provider normalization/parser interaction cannot select a caller-controlled address; ambiguous behavior blocks clearance. |
| Alternate headers | Forged `Forwarded`, `X-Real-IP`, `CF-Connecting-IP`, alone and combined | No bypass of the committed X-Forwarded-For/peer trust rule. |
| Bad or excessive chain | Malformed IPs and more than 20 supplied entries | No 500 or attacker-selected identity. If ingress retains a malformed chain, the code's conservative peer fallback is recorded; shared-bucket denial-of-service/usability implications must be reviewed rather than called accurate attribution. |
| Alternate entry paths | onrender.com, configured custom host and any provider-confirmed private/direct route | Every supported route has a documented trust boundary; callers cannot impersonate trusted proxies. |
| Worker consistency | Repeat A/B and forged-header cases on multiple workers | Same client resolves consistently; different clients are not collapsed into a proxy bucket. |

The example injected IPs above are synthetic test input, **not trusted CIDRs**.
The matrix is a validation specification, not evidence of a completed test.

### HTTPS, session and CSRF matrix for the same staging change

Render terminates HTTPS and forwards HTTP internally. With Uvicorn rewriting disabled,
the observed ASGI scheme may therefore be HTTP even though the browser connection is
HTTPS. `PUBLIC_BASE_URL` does not globally rewrite the ASGI scheme.
[Render TLS termination](https://render.com/docs/web-services)

Use a strong test-only SESSION_SECRET and secure cookies in staging; retain the
production signing key/settings unchanged. Set staging PUBLIC_BASE_URL to its actual
canonical HTTPS origin, not the production host. Check these behaviors using browser
requests and controlled negative requests with valid test CSRF tokens where necessary:

| Check | Expected / decision |
| --- | --- |
| External transport and redirects | HTTP reaches HTTPS; no redirect loop or generated link downgraded to HTTP. Record both browser scheme and internal ASGI scheme. |
| Cookie/session round trip | Secure, HttpOnly and SameSite=Lax session attributes; valid student, verifier and admin login/session/logout behavior over HTTPS. Do not capture raw cookie values in evidence. |
| Legitimate forms | Site-admin/membership forms pass with their current session CSRF token and the canonical browser Origin; test the ordinary browser's actual headers. |
| Invalid CSRF token | Missing/wrong token remains 403, independent of forwarding headers. |
| Foreign Origin | A valid test token with an unrelated Origin remains 403; a forged forwarded host/scheme cannot authorize it. |
| Custom versus onrender.com host | Record which origin is canonical and which form flows are supported. A configured public base must not silently break the intended host. |
| `Origin: null` | Exercise separately with and without `Sec-Fetch-Site: same-origin`; observe the established policy. The null-origin branch also checks request-derived host/port, so an internal HTTP/80 versus configured HTTPS/443 mismatch can still yield 403. If a legitimate flow depends on this case, stop and report it; do not weaken CSRF. |
| Links and notifications | Invitation/verification links use the intended HTTPS host, with fake delivery only. Do not send test emails or payments to real recipients. |

The CSRF check normalizes origin host/port and still validates the session token.
Testing an Origin header alone is insufficient. Merely setting PUBLIC_BASE_URL is
not proof that null-origin, redirect or every generated-URL path remains correct.
A valid session-cookie test cannot substitute for accurate client-IP validation.

### Enforcement follow-through and release gate

Only after attribution and HTTPS checks pass, use isolated shared Redis in staging
with `LOGIN_RATE_LIMIT_MODE=redis` and `LOGIN_RATE_LIMIT_REQUIRED=true`. Reuse the
existing local proofs rather than rerunning the full audit; deployment-specific
checks must demonstrate that spoofing cannot change A's IP bucket, IP-blocked A
cannot poison B's account quota, B's valid login remains usable, account limits hold
across IPs, and workers share expiry/counters. Include both admin credential kinds.
Use separate test identities to distinguish account-based blocking from IP blocking.
Verify 429/Retry-After recovery and a controlled **staging-only** unavailable-backend
case returning 503 without fallback.

Passing attribution means the right IP is selected. Passing a connection probe means
the datastore is reachable. Operational clearance additionally needs actual limiter
behavior, HTTPS/CSRF compatibility and the supported provider trust contract. The
existing diagnostic's `production_verified` field stays false; a successful PING or
`backend_operational` result is not production verification.

- [ ] **Jeff approves the expense; upgrade production Key Value to the $10/month paid
  plan before production limiter activation, then Product/Operations verifies
  connectivity and enforcement under the approved release process.** This existing
  release requirement remains unchecked. The provider inquiry itself requires no
  deployment or upgrade. A staging deployment/capture method is a later separately
  approved step; any additional service cost requires Jeff's approval.

No production settings changed, deployment, new infrastructure, application edit,
staging, commit, push or test rerun occurred in this update. Existing test evidence
is retained. The report remains an unstaged documentation change on commit `a4db762`.
Previous report/patch exports and the original checkout are preserved.

Updated report export: `/home/geph/Downloads/Woodshed_Security_Round1_Report_20260916T043739504253Z.md`.

## September 16 continuation — sensitive logging, Arcade integrity, release assessment

This section supersedes earlier descriptions of logging and History Mystery score
validation. Earlier full-suite results above are historical evidence, **not runs of
this final code**. Overall Security Round 1 and unrestricted public beta remain
**NOT CLEARED**. No production access, probes, configuration changes or deployment
were performed in this continuation.

### Starting state and preserved work

- Worktree Git root: `/home/geph/Training_scripts-security-round1-server-economy`.
- Application directory: `woodshed-woodchuck`; branch: `security/round1-server-economy`.
- Starting and retained HEAD: `a4db762da0092bf585493ad4cdacf61c5e872334`.
- Starting `git status --short`: only ` M docs/security-round1-server-economy.md`.
  Its 391 intentional post-commit added lines were preserved. No files were staged.
- No applicable AGENTS.md was found in the worktree or its ancestor directories.
- The original checkout, its protected checklist/database backup, other branches,
  worktrees, stashes and historical data were not changed. The database backup was
  neither opened nor copied. Current worktree sources were used throughout.
- All new databases, logs and runner files are under
  `/tmp/ww-security-logs-games.dvxn86q2`; the existing virtual environment was used
  read-only. The runner clears inherited environment variables and sets an explicit
  disposable SQLite DATABASE_URL and synthetic session secret. No SMTP/payment
  configuration is inherited; no real email, payment or provider API was invoked.

**User-confirmed infrastructure facts, not independently observed here:** Key Value
`woodshed-login-limits` is Available, Free, `noeviction`, and in the web service's
region; its Internal URL was privately saved as LOGIN_RATE_LIMIT_REDIS_URL with
“Save only.” The command remains
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`; FORWARDED_ALLOW_IPS is `*`.
The latest instruction explicitly states LOGIN_TRUSTED_PROXY_CIDRS is **unset**;
this supersedes any ambiguity around the earlier reported `None`. Provider proxy
trust guarantees are unresolved. The previously prepared provider questions,
spoofing/distinct-client/HTTPS/session/CSRF matrices and paid-tier requirement
remain in force. They were not reinvestigated or marked verified.

### Application-controlled logging — fixed locally, provider coverage OPEN

| Input / flow | Existing exposure or deliberate use | Local protection / remaining boundary |
| --- | --- | --- |
| Trusted-verifier invitation | Capability appears in `/trusted-verifiers/accept/{token}` and `/trusted-verifiers/invitations/{token}/accept`; intentional invitation response/email links remain. | Uvicorn records the route template, never token values or query strings. Invalid and unmatched URLs receive the same sanitization. |
| Membership invitation | Capability in `/membership/invitations/{token}` GET/POST; email and intended recipient-facing link still contain it. | Same sanitized access logging; capability transport and provider ingress logs are unchanged. |
| Student/verifier authentication | IDs/email and PINs are form inputs; site-admin credential is a form token; contest-admin uses its separate credential header/session boundary. | No body, header, cookie or credential values enter new diagnostics. Actual student/verifier success/rejection and admin CSRF rejection logs are tested. Existing authorization/rate-limit boundaries are retained. |
| Arcade completion/answer | Play token in URL; alternative completion carries it in JSON. Browser failure diagnostics previously included the literal completion URL. | All dynamic route components and query strings are omitted from server access logs. Browser warnings contain operation, attempt, status and response-presence only; attached error endpoints use placeholders. |
| Request query strings / unknown paths | Default Uvicorn prints the complete requested path/query, even for rejected routes. | Route templates only; unmatched routes become `<unmatched>`. No raw-path fallback. Source-IP text is omitted from these application access records. |
| Exceptions / SQL failures | Unhandled exception text and chained SQLAlchemy errors can include bound tokens or credentials. | Uvicorn exception records retain exception type and filename/function/line locations, but omit exception messages, chain contents, source lines, locals and SQL parameters. The request error record retains sanitized route, method, status, elapsed time and a generated correlation ID. Existing handled Arcade SQL errors retain safe operation/game attribution. |
| Email/analytics diagnostics | Existing verifier/chart delivery warnings contain record ID and fixed delivery code; analytics warning contains operation/type. SMTP exceptions map to fixed result codes. | Existing bounded messages retained; no message bodies or invitation URLs added. Response payloads/emails are not logs and still carry intended recipient information. |
| Uvicorn TRACE / WebSocket diagnostics | Optional ASGI TRACE can expose scope path/query; WebSocket handshake messages can include a URL. | TRACE payload records are suppressed to a fixed message; WebSocket URL messages are sanitized. There is no application WebSocket game protocol. Current HTTP logging was exercised at INFO and TRACE. |

Implementation is `app/safe_logging.py`, installed early during `app.main` import,
plus ASGI request context middleware. Normal access records retain HTTP method,
sanitized route, status, protocol version, milliseconds to response headers and a
server-generated request ID. Caller-provided request IDs are not trusted. On an
unhandled exception, the contextual error record has the route/timing/ID; an outer
Uvicorn fallback 500 access record can be `<unmatched>`/UNKNOWN after context has
unwound. Exception locations still identify the failing code without its values.

**Deployment wiring:** the observed direct Uvicorn command loads these filters after
Uvicorn creates its loggers; no new launch flag or external logging dependency is
needed. The Arcade script cache version is incremented on every consuming page.
A later replacement of logging configuration/handlers, different server/wrapper,
third-party tracing or new explicit application logging needs its own verification.
These changes do not rewrite browser history, developer-tools network records,
email links, Render/Cloudflare/load-balancer logs, log drains, existing stored logs,
or provider error reporting. They do not establish provider log retention/deletion
or restricted access. **Provider-controlled exposure remains OPEN**; Product must
obtain the relevant logging/redaction/access/retention guarantees separately.

Tests launch a real local Uvicorn process against a synthetic SQLite database and
capture stdout/stderr. They send synthetic secrets in capability paths, queries,
form bodies, headers and cookies; exercise success, 401/403/404/422 and an unhandled
500; and assert both absence of secret values and presence of useful diagnostics.
The existing handled-SQL failure test also verifies emitted application logs.
Node tests capture actual Arcade console output on network, rejection and JSON
parse failures, including error objects, instead of asserting that a filter ran.

### Arcade exploit reproduction and local correction

Before changing scoring, an isolated reproduction started each game with 20 credits
and submitted an invented score without playing: 999999 for each of the eight
non-History games, and 5 for History Mystery. **All nine returned 200, paid 5,
left balance 24 and stored the invented best score.** The unpaid
`POST /xp/plunge-best` accepted 999999; `/xp/plunge-points` accepted an invented
`band_complete` event worth 20 raw points. Evidence: `scores-before.txt` and the
exact disposable driver `reproduce_scores.py` in the evidence directory.

#### History Mystery: server-calculated score and ordered answers

- Existing server question bank/date selection supplies five ordered questions.
  Start/resume returns only the current question's prompt/choices/category/ID;
  page HTML no longer embeds the answer key. Correct answer/fact is returned only
  after that question's first accepted choice is stored.
- New authenticated `POST /arcade/plays/{play_token}/answer` accepts only a bounded
  question index and choice. It checks active identity, token ownership, game,
  Central-day validity, current sequence and allowed choices. An identical retry
  is idempotent; changing a prior choice or jumping ahead is rejected.
- The backend calculates the score from committed answers. The fifth answer,
  final score, best-score update and payout commit together. Both existing score
  completion endpoints enforce this same boundary; a score alone cannot earn a
  History reward or create its record. Completed identical acknowledgments remain
  idempotent, including historical completed rows, without re-awarding anything.
- Existing WoodchuckState JSON holds only the latest History attempt (five question
  IDs, committed choices, bank version, play ID and server signature). No schema or
  migration is introduced. Generic sync preserves it under the same profile/state
  locks as the balance; stale sync receives the existing conflict response.
- The signature binds this server-written data to the profile using a separate
  HMAC domain with the validated session key. This prevents old arbitrary browser
  JSON from being promoted into trusted quiz answers on deployment. It is **not**
  a client gameplay proof: the server independently selects questions, validates
  every choice/transition, and derives score. Client-supplied scores, transcripts,
  answer keys and replacements of signed state are not trusted. Malformed legacy
  signatures fail safely, including non-ASCII strings.
- An unfinished current-day attempt resumes without another charge, including a
  lost start response and an existing pre-release unfinished play. Unsigned legacy
  quiz progress starts with zero accepted answers on resume; balance is not reset.
  Old cached clients must reload to submit choices instead of a bare score.
- Unfinished attempts expire at the Central-day boundary. The UI explicitly asks
  for a reload to start today's quiz after expiry. Completed identical completion
  retries remain safe. This makes the daily validity boundary explicit; Product
  should review the midnight/old-client handoff before release. No refund or new
  charge is invented for an expired old attempt; the next day's normal entry is 1.
- Prices remain 1; payouts for scores 0–5 remain **0, 0, 0, 1, 2, 5**; once per Central
  day, existing global reward caps, high-score ordering and tie rules remain.
  No historical score/balance is deleted, lowered or certified. Shared persistence,
  purchase locking and legitimate earning mechanisms remain in place.
- Browser answer/score responses use the current account generation. Logout,
  switching users, or logout followed by the same user's login cannot apply a
  pending old response to the new account. On repeated network loss, manual retry
  resends the original choice. Mobile answer targets and existing feedback remain.

**Status:** the bare-score/changed-answer/quiz-state-forgery attacks are fixed locally
for History Mystery, subject to the evidence below. This establishes answer
correctness under quiz rules, **not human effort**. The finite static question bank,
shared daily questions, prior knowledge, answer sharing, lookup and automation can
still produce five correct submissions. No bot resistance or all-games assurance
is claimed. Session-key rotation invalidates unfinished quiz signatures as well as
sessions; reopening an unfinished quiz initializes its answers again without a
second charge. Keep the production key unchanged for this proposed release.

#### Per-game score-integrity status and exact residual dependencies

All paid games share `POST /arcade/plays` and
`POST /arcade/plays/{play_token}/complete`; `POST /arcade/scores/{game_key}` is an
alternate completion route into the same service. Ownership, entry charge,
idempotence and reward-count caps are backend-enforced; those checks alone never
prove a submitted score was earned.

| Game / surface | Valuable outcomes and final local status | Remaining implementation / product decision |
| --- | --- | --- |
| History Mystery | Server validates ordered choices and derives 0–5, payout, ArcadeHighScore/personal best; common scores API can return its leaderboard though cabinet shows personal best. **Bare-score forgery FIXED locally; automation limitation remains.** | Product reviews daily expiry, mixed-client rollout and the explicitly retained static-bank/answer-sharing limits. |
| Blue | Browser platform physics, movement, collectibles and stage/timer transitions feed a bare score. Invented 999999 still earns maximum payout and leaderboard record. **HIGH / BETA BLOCKER, OPEN.** | Versioned authoritative movement/collision/collection/stage simulation with validated input timing and initial state. A ceiling from collectibles alone does not validate a played route. |
| Radio Tuner | Browser needle motion/random initial direction, tap position and timing bonuses/penalties feed a bare score. Browser's 50000 ceiling is bypassable. Currency/record still forgeable. **HIGH / BETA BLOCKER, OPEN.** | Server-controlled initial direction/timing and a validated tap/motion timeline, with latency/background-tab policy. A timer or numeric ceiling alone is insufficient. |
| Wheel of Woodchuck | Browser-selected terms, wheel outcomes/multipliers, guesses, misses and 45-second run produce score. Currency/record still forgeable. **HIGH / BETA BLOCKER, OPEN.** | Server-controlled term/spin sequence, validated guess/miss/solve transitions and run timing. Spin randomness and replay/refresh semantics require a separate protocol proposal. |
| Scale Keyboard | Browser-selected scales/notes and 30-second timer award 100/note, 500/completed scale, -50/wrong note. Currency/record still forgeable. **HIGH / BETA BLOCKER, OPEN.** | Server-controlled scale sequence and validated note transitions; timed-input and offline/latency behavior must be designed without changing existing rates. |
| Thirds | Random browser chord sequence; +1/correct in 30 seconds. Currency/record still forgeable. **HIGH / BETA BLOCKER, OPEN.** | Timed server-owned question sequence/answer protocol or validated deterministic replay, preserving rapid keyboard/touch interaction. It cannot be secured by copying the slower daily quiz protocol without considering latency. |
| Dressed to the Nines | Random browser tonality/answer sequence; +1/correct in 30 seconds. Currency/record still forgeable. **HIGH / BETA BLOCKER, OPEN.** | Same timed-answer/sequence requirements, using its own rules and transitions. |
| Interval Basic Training | Browser-selected audio intervals; 30-second/two-mistake termination and answers are client-controlled. Currency/record still forgeable. **HIGH / BETA BLOCKER, OPEN.** | Server-owned interval sequence, validated answers/mistakes and timing; actual listening cannot be proven by a signed token or replay. Audio/latency handling needs review. |
| Plunge Burrow paid completion | Browser grid movement, growth, collision/hearts/portals/enemies, random item placement and band collections produce score. Paid route changes currency and profile.plunge_best_score/leaderboard. **HIGH / BETA BLOCKER, OPEN.** | A single authoritative simulation/replay boundary with server initial seed/rules and validated movement/tick transitions must derive both score and reward events. Merely reporting collected objects is not proof. |
| Plunge `POST /xp/plunge-best` | Separate authenticated **unpaid** route accepts an arbitrary bounded score and updates the same profile record/leaderboard. **HIGH / BETA BLOCKER, OPEN.** | Route must eventually acknowledge the same verified run result, not accept an independent number. Adding a paid token alone would leave forged paid scores and change compatibility without solving authenticity. Current browser paid completion already records best; no endpoint was silently removed. |
| Plunge `POST /xp/plunge-points` | Accepts invented event keys of the allowed types/values: 1 dandelion, 3 carrot, 5 instrument, 20 band completion. Dedup applies only to a chosen key. XP sum remains capped at 10/day, but events/XP-derived level can be fabricated; raw event rows continue to accumulate. **HIGH / BETA BLOCKER for earned-state integrity, OPEN.** | Derive event counts from the same validated Plunge run, bind event IDs to server transitions and preserve the current daily XP cap. A separate client transcript or token would not establish collection. |

The unchanged non-History paid pathways can yield net 4 credits per maximum-reward
completion, ten rewarded completions per game/day: eight games permit up to 320
net credits/day through these invented results (given an initial entry credit).
The account economy therefore remains **PARTIAL**, despite generic-sync protection.
High scores can be fabricated up to the existing backend integer bound; leaderboards
and rank/ties are affected. The reviewed score/XP writers do not directly write
practice-contest standings, contest rewards or crowns; XP affects its displayed
level, not a new contest award introduced here. No product economics, public
leaderboard fields or existing tie rules were changed in this continuation.

**Recommended follow-up:** Product/Security should approve one coordinated game
validation proposal: shared versioned attempt/retry/expiry ownership, per-game
server rules and initial state, bounded validated input transitions, and a single
Plunge source for paid payout, best score and XP. Timed/physics games need explicit
latency, pause/background-tab, offline, input-volume and deterministic simulation
choices. Any schema change needs its own strict-guard/migration/disposable-DB and
release-order validation. A signed play token, plausible ceiling, elapsed time, or
unverified client proof is not a substitute. Alternatively, a Product decision
about what unverified play may reward would be an explicit economics/competition
change requiring approval; none was made here. No games or reward paths were disabled.

### Independent release assessment — proposal only

**A code-only controlled pre-beta release can be considered independently of
production IP throttling. It cannot clear unrestricted public beta.** Logging,
generic-state economy authority, browser response ordering, quiz validation,
existing authorization boundaries and session hardening do not require a live
limiter datastore or trusted proxy allowlist when limiter mode is explicitly off.
The saved Redis URL alone neither activates protection nor needs a paid upgrade for
this code-only step. These dependency statements follow `login_limits._settings`,
`protection_status`, session configuration and the directly exercised application
paths; they are not production verification.

| Proposed step | Prerequisites / exact action | Evidence and rollback implications |
| --- | --- | --- |
| 1. Review candidate, no release yet | Review cumulative patch against its stated base and this report; verify the actual production commit/schema against the candidate. Confirm existing required schema guards/migrations are already satisfied. This continuation adds no migrations, columns or dependency upgrades. Confirm the earlier redis-py requirement is installed by the normal build. | Do not assume the production base from local HEAD. Known baseline failures and eight-game/Plunge debt remain explicitly tracked. Approve the History daily-expiry/old-client behavior and a controlled observation scope; preserve historical balances. |
| 2. Validate code-only configuration in an existing approved isolated environment | In isolated staging, use production-mode validation with a **test-only** strong SESSION_SECRET (at least 32 characters) and SESSION_COOKIE_SECURE=true. Before any production release, Product privately verifies that the existing production key/cookie configuration meets those requirements; do not copy the production key into staging or reports. Explicit proposed limiter values: `LOGIN_RATE_LIMIT_MODE=off`, `LOGIN_RATE_LIMIT_REQUIRED=false`. Retain the saved Redis URL privately; it is unused in off mode. Retain the current Uvicorn command, FORWARDED_ALLOW_IPS='*' and unset LOGIN_TRUSTED_PROXY_CIDRS **for this off-mode step only**. Preserve the canonical HTTPS/public URL and current CSRF policy. | No expense, infrastructure creation or proxy change is intrinsically required for these code protections. If no isolated staging facility exists, propose it separately; this run creates none. Production fallback-secret or insecure-cookie configuration still fails safely; required/Redis outage safeguards are not weakened. |
| 3. Isolated smoke checks | Ordinary student/verifier/site-admin/contest-admin login/logout; generic-sync forged balance and stale-state rejection; legitimate reward/purchase; private admin analytics; History low/high scores, retries, refresh, day boundary, wrong user/game, both completion endpoints; old open page reload; actual sanitized application logs on success/rejection/error; HTTPS Secure/HttpOnly/SameSite cookies and legitimate/foreign-Origin/missing-token CSRF cases. | Use synthetic accounts, fake email/payments and separate storage. Confirm the configured limiter diagnostic says **disabled**, not operational/verified. Test private routes and normal responses, with no real-user/prod attacks. Existing provider-controlled logs remain unverified. |
| 4. Separately authorized code deployment | After review/commit/release approval, deploy backend/templates/scripts together. All workers must run the reviewed version before claiming these local protections are live. Fresh pages must load arcade-economy v5 and History v2. Keep limiter off and avoid proxy rewiring in this release. | Until older workers are drained, they may still accept old score/state writes. Old quiz tabs need a reload/resume; do not add a compatibility bypass accepting their score. Product performs ordinary approved test-identity smoke checks and verifies build identity plus sanitized logs. No such deployment or production smoke ran here. |
| 5. Rollback decision | No schema downgrade is needed because this batch uses existing rows/JSON. Preserve balances, play records and choices; do not restore a historical database or issue automatic compensating credits. | Rolling back to a4db762 reopens raw access logging and History bare-score acceptance; older code can also overwrite the new JSON field. Pause any claim of quiz protection. A forward re-release after an old-writer interval needs an explicit plan to invalidate/reinitialize unfinished quiz state: a previously signed old snapshot could otherwise have been replayed while the old writer was active. Do not silently certify it or rotate the production session key as a convenience. A reviewed forward fix is preferable where feasible. |
| 6. Separate production limiter activation | Preserve the earlier provider-response, supported trust-boundary, distinct-client/spoofed-header and HTTPS/session/CSRF gates. Only provider-supported CIDRs may be used. The previously proposed direct-Uvicorn raw-peer command adds `--no-proxy-headers`; FORWARDED_ALLOW_IPS must then be explicitly empty, with reviewed LOGIN_TRUSTED_PROXY_CIDRS. This must first pass the prepared isolated staging matrix. | Do not make this command/configuration change as part of step 4. Disabling proxy scheme rewriting can affect HTTPS URL/CSRF behavior; PUBLIC_BASE_URL alone is not proof. No CIDRs or alternative provider trust mechanism are guessed. |
| 7. Paid tier and enforcement clearance | **Jeff approves the expense; upgrade woodshed-login-limits to the $10/month paid plan before activation.** Product/Operations verifies connectivity, shared counters across processes, account/IP limits (including both admin credential kinds), blocked-IP non-poisoning, expiry/recovery, spoof resistance and required-backend outage behavior. Then separately authorize `LOGIN_RATE_LIMIT_MODE=redis`, `LOGIN_RATE_LIMIT_REQUIRED=true`. | Connectivity/PING/diagnostic `backend_operational` alone is not enforcement verification. Required mode and enabled-Redis outages retain 503 fail-closed behavior; no per-process fallback. Once required protection is activated, an outage response is repair/rollback under an explicit release decision, not silently setting off/false. Protection is not production-operational or cleared by this local work. |

- [ ] Paid Key Value upgrade before limiter activation; Jeff approves expense,
  Product/Operations owns connectivity **and enforcement** verification.
- [ ] Provider-supported proxy and logging guarantees, isolated spoofing/distinct-client
  validation and HTTPS/session/CSRF checks. A single observed address is not a CIDR contract.
- [ ] Review/deploy/verify the cumulative code; no local result is deployed protection.
- [ ] Resolve the eight remaining paid-game score paths and both separate Plunge paths
  before claiming authoritative earned Arcade rewards or public-beta security clearance.
- [ ] Historical balance/score provenance and practice/activity self-reporting remain
  separate policy/data questions. No history was repaired, erased or re-valued.
- [ ] PIN recovery remains separate and unresolved; unrelated product work stays out of scope.

### Validation evidence for this continuation

Evidence directory: `/tmp/ww-security-logs-games.dvxn86q2`.
`run-safe` is an executable wrapper around `env -i` with the existing virtualenv
PATH, isolated PYTHONPYCACHEPREFIX, temporary SQLite DATABASE_URL, synthetic session
configuration and the previously isolated Redis-client import directory. It never
loads project/production environment files. Tests run from this worktree application
directory. Test logs remain outside Git and are not included in exports.

Disposable PostgreSQL 16 was initialized in `pgdata`, bound only to loopback port
56491 and a socket inside this evidence directory. The only test URL is
`postgresql+psycopg://ww_test@127.0.0.1:56491/ww_billing_a2_test`. `pg-isolation.txt`
records database/data directory/address/port; tests create independent schemas with
lock/statement timeouts. No existing PostgreSQL service/database was used. No Redis
backend code changed in this run, so prior disposable Redis/multiprocess evidence
is reused; opt-in Redis tests skipped without an explicit new test service must not
be called production or Redis verification.

| Check / evidence log | Exact result | Interpretation |
| --- | --- | --- |
| Untouched focused baseline, `baseline.txt` | 62 passed; 0 failed/skipped | Existing History, Arcade economy and security-authorization tests. Earlier full-suite baseline retained above; no broad baseline rerun. |
| Emitted logging, `logging-confirm.txt` | 3 passed; 0 failed/skipped | Real Uvicorn INFO/TRACE plus existing handled SQL-error logging; final integrated run also includes the expanded credential cases. |
| History/Arcade focused, `history.txt` | 54 passed; 3 PostgreSQL-only skips; 0 failed | Initial quiz correction; PostgreSQL proofs subsequently ran against the new cluster. |
| PostgreSQL races, `history-pg-final.txt` | 3 passed; 10 deselected; 0 failed/skipped | Concurrent start, conflicting answers, last-answer/completion payout. Final integrated run repeats these with the final signature checks. |
| Real Chromium, `browser.txt` | 2 passed; 0 failed/skipped | Widths 390 and 1440, legitimate score 0/5, cookie login, lost committed response, mid-quiz refresh, no second charge, button height. Repeated within final integrated run. |
| Final integrated Python, `final-integrated.txt` | **437 passed, 3 failed, 11 skipped** | Final application code; all three failures identified and resolved by the focused recheck below. This is not a full project suite. |
| Final failing-surface recheck, `final-recheck.txt` | **35 passed; 0 failed/skipped** | Entire Arcade and contest-admin test files, with the cache assertions updated and disposable default schema initialized. No application changes were needed after the integrated run. |
| Final all Node tests, `final-js.txt` | **59 passed; 0 failed/skipped** | Includes 6 real-handler account-switch/retry/expiry cases and 3 captured-console cases added here; prior browser-state tests retained. |
| Final syntax/import, `final-syntax.txt` | **64 Python files compiled, app.main imported; 32 JavaScript files syntax-checked; all passed** | No repository bytecode/cache files produced. |

The integrated selection covered 451 cases. After the focused corrections, 440
unique selected cases have passing evidence and 11 intentional skips remain; this
is **not** a claim that a single clean full-suite run produced that aggregate.
Earlier 1510-pass full-suite evidence in this report predates this implementation.

**Exact integrated failures and resolution:**

1. `tests/test_arcade.py::test_arcade_room_renders_nine_touch_friendly_cabinets`
   expected arcade-economy v4. Updated to v5 because the security logging/client
   protocol change requires fresh assets. Existing touch/layout assertions remain.
2. `tests/test_arcade.py::test_arcade_landing_renders_personal_bests_from_existing_score_payload`
   searched for v4 when checking load order. It now checks v5 still loads before
   arcade.js; no behavior assertion was removed. An immediate standalone run of
   this file passed 25 tests (`arcade-cache-recheck.txt`).
3. `tests/test_contest_admin.py::test_admin_page_requires_valid_token_and_is_not_in_student_navigation`
   reached the unchanged `/quest` lookup through main's default SessionLocal;
   the new temporary default SQLite database had no `seasons` table. Its fixture
   patches contest_admin's factory, not main's. The earlier report documents this
   default-page-schema harness requirement. This is not classified as one of the
   ten known baseline failures or as a security-code regression. The guarded setup
   checked the exact temporary database path, then ran Base.metadata.create_all
   there only (`default-schema.txt`); no production database or migration was used.
   Re-running both affected files passed all 35 tests with no application fix.

The 11 final skips are exactly: six SQLite instances of PostgreSQL-only row-lock
race tests (`test_server_economy.py:225`), whose PostgreSQL instances did run; and
five explicit Redis cases in `test_login_limits.py` (multiprocess/expiry, dimension
expiry, student/verifier poisoning and concurrent-IP boundary). Prior real Redis
proofs remain the evidence for those unchanged paths. No skip was added to suppress
a failing application test.

Initial test-harness corrections are retained transparently in outside-Git logs:
`logging.txt` had 2 new subprocess-readiness failures and 1 pass; cold imports were
outlasting the initial 7.5-second readiness poll, so the test now waits up to 50
seconds and guarantees subprocess cleanup. PostgreSQL's first initialization used
SQL_ASCII (`history-pg.txt`: 3 setup errors); the disposable test database was
recreated as UTF8. The next run exposed an overlength synthetic profile ID
(`history-pg-confirm.txt`: 3 setup errors); only the new PostgreSQL fixture's suffix
was shortened to comply with the existing varchar(16) constraint. The subsequent
race run passed all 3. An early integrated launch (`integrated.txt`) was explicitly
interrupted during final review to include malformed-signature/expiry refinements;
pytest reported KeyboardInterrupt and a teardown StashKey error, without a completed
suite summary. It is not counted as completed evidence or as an application failure.

Existing History tests now submit actual choices before expecting a reward. Tests
that previously required a second unfinished daily start to fail now require it to
resume the same play without charging, while a second completed daily start still
fails. Personal-best/lower-score tests use the atomically completed final answer.
These are the necessary behavior changes; tests were not deleted, disabled or
weakened. The original pure browser game-rule tests remain, while live gameplay now
uses server responses for score. No unrelated test expectation changed.

Exact commands (RUN below means `/tmp/ww-security-logs-games.dvxn86q2/run-safe`):

```sh
# Focused baseline, baseline.txt
RUN pytest -q tests/test_history_mystery.py tests/test_arcade_economy.py tests/test_security_authorization.py --basetemp=/tmp/ww-security-logs-games.dvxn86q2/baseline-tmp -o cache_dir=/tmp/ww-security-logs-games.dvxn86q2/cache

# Real emitted logs, logging-confirm.txt
RUN pytest -q tests/test_sensitive_logging.py tests/test_security_authorization.py::test_database_failures_do_not_log_sql_tokens_or_arbitrary_headers --basetemp=/tmp/ww-security-logs-games.dvxn86q2/logging-confirm-tmp -o cache_dir=/tmp/ww-security-logs-games.dvxn86q2/cache

# Focused quiz/Arcade, history.txt
RUN pytest -q tests/test_history_mystery.py tests/test_history_score_integrity.py tests/test_arcade_economy.py --basetemp=/tmp/ww-security-logs-games.dvxn86q2/history-tmp -o cache_dir=/tmp/ww-security-logs-games.dvxn86q2/cache

# PostgreSQL races, history-pg-final.txt
RUN env WW_BILLING_TEST_POSTGRES_URL=postgresql+psycopg://ww_test@127.0.0.1:56491/ww_billing_a2_test pytest -q tests/test_history_score_integrity.py -k postgres --basetemp=/tmp/ww-security-logs-games.dvxn86q2/history-pg-final-tmp -o cache_dir=/tmp/ww-security-logs-games.dvxn86q2/cache

# Real mobile/desktop Chromium, browser.txt
RUN pytest -q tests/test_history_browser.py --basetemp=/tmp/ww-security-logs-games.dvxn86q2/browser-tmp -o cache_dir=/tmp/ww-security-logs-games.dvxn86q2/cache

# Final integrated application code, final-integrated.txt
RUN env WW_BILLING_TEST_POSTGRES_URL=postgresql+psycopg://ww_test@127.0.0.1:56491/ww_billing_a2_test pytest -q -ra \
 tests/test_sensitive_logging.py tests/test_history_mystery.py tests/test_history_score_integrity.py tests/test_history_browser.py \
 tests/test_arcade_economy.py tests/test_arcade.py tests/test_arcade_soundtrack.py \
 tests/test_account_state_sync.py tests/test_server_economy.py tests/test_security_authorization.py \
 tests/test_login_limits.py tests/test_session_hardening.py tests/test_site_admin_csrf.py tests/test_contest_admin.py \
 tests/test_login_streaks.py tests/test_xp.py tests/test_plunge_best.py tests/test_plunge_burrow.py tests/test_plunge_burrow_second_pass.py \
 tests/test_thirds.py tests/test_dressed_to_the_nines.py tests/test_interval_basic_training.py tests/test_scale_keyboard.py tests/test_wheel_of_woodchuck.py \
 tests/test_verifier_relationships.py tests/test_analytics.py \
 --basetemp=/tmp/ww-security-logs-games.dvxn86q2/final-integrated-tmp -o cache_dir=/tmp/ww-security-logs-games.dvxn86q2/cache

# Final focused correction verification, final-recheck.txt
RUN pytest -q tests/test_arcade.py tests/test_contest_admin.py --basetemp=/tmp/ww-security-logs-games.dvxn86q2/final-recheck-tmp -o cache_dir=/tmp/ww-security-logs-games.dvxn86q2/cache

# Final Node suite, final-js.txt
RUN node --test tests/*.js
```

The final compile/import command runs `py_compile.compile(..., doraise=True)` on
all `app/**/*.py`, then `import app.main`; the same isolated Python driver runs
`node --check` for every `static/js/*.js`. The complete final integrated command is
also saved as executable `final-integrated.sh`. Post-fix reproduction uses:

```sh
RUN env PYTHONPATH=/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck:/tmp/ww-security-round1-continuation.z1hkSz/deps python /tmp/ww-security-logs-games.dvxn86q2/reproduce_scores.py
RUN env PYTHONPATH=/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck:/tmp/ww-security-round1-continuation.z1hkSz/deps python /tmp/ww-security-logs-games.dvxn86q2/reproduce_alternate_scores.py
```

`scores-after.txt` and `scores-alternate-after.txt` show History returning **409**
through both completion paths, while the eight other paid games still return 200
with the invented record/payout and the two Plunge endpoints still accept their
invented inputs. These successful residual attacks are reported OPEN, not counted
as successful defenses. Non-History unfinished plays still have no completion
expiry; History's day check does not secure their tokens/results.

### Changed-file audit for this continuation

| File(s), relative to application directory | Necessary change |
| --- | --- |
| `app/safe_logging.py` (new) | Sanitized Uvicorn access/error/trace records and request correlation/timing. |
| `app/main.py` | Install logging before application dependency imports; add request context; stop embedding History answer keys. |
| `app/history_attempts.py` (new) | Server-selected ordered quiz progress, bounded state storage, server signature, validated choices, score derivation and daily validity. |
| `app/economy.py` | Preserve server-owned quiz state during generic synchronization. |
| `app/arcade_rewards.py` | Resume current-day unfinished quiz without charge; derive/validate quiz completion; atomically persist final answer/reward using existing locks. |
| `app/arcade_routes.py` | Bounded authenticated answer endpoint and public question/progress response; preserve HTTP conflict/ownership semantics on both completion routes. |
| `static/js/arcade-economy.js` | Remove capability URLs from console/error metadata; shared quiz-answer request/retry/current-account state handling. |
| `static/js/history-mystery.js` | Consume server progress/feedback; resume, retry same answer, ignore responses after account changes, explain expiry. |
| `templates/history_mystery.html` | Remove answer-key JSON and update the two necessary script cache versions. |
| `templates/arcade.html`, `templates/arcade_game.html`, `templates/dressed_to_the_nines.html`, `templates/interval_basic_training.html`, `templates/plunge_burrow.html`, `templates/scale_keyboard.html`, `templates/thirds.html`, `templates/wheel_of_woodchuck.html` | Only arcade-economy cache version v4→v5 so existing pages receive the logging fix. No layout/CSS/game-rule edits. |
| `tests/test_sensitive_logging.py`, `tests/test_sensitive_logging.js` (new) | Actual emitted server/browser logs, synthetic credentials and useful-diagnostic assertions. |
| `tests/test_history_score_integrity.py` (new) | HTTP forgery, state injection/replay, legitimate tiers, ownership/game binding, retry/order/expiry and real PostgreSQL concurrency proofs. |
| `tests/test_history_browser.py` (new) | Actual local Uvicorn/SQLite and Chromium gameplay at mobile/desktop sizes, network loss and refresh. |
| `tests/test_history_browser_state.js` (new) | Real state/client handlers across account switches, same-account re-login, manual retries and expiry. |
| `tests/test_history_mystery.py` | Preserve existing tests with legitimate server answer submissions and the explicit no-extra-charge resume semantics. |
| `tests/test_arcade.py` | Update only the two assertions for the required shared-script cache version; retain script-order/layout checks. |
| `docs/security-round1-server-economy.md` | Preserve all prior report updates and append this evidence, residual game status and release proposal. |

New files were inspected directly without staging. Existing diffs were inspected
for interface deletion, schema changes, broad formatting and unrelated edits.
There are no new dependencies, migrations/schema changes, CSS changes, historical
balance edits, unrelated file changes or removed games/rewards in this continuation.
The previous redis-py dependency remains part of a4db762 and the cumulative patch.
All code changes remain unstaged and uncommitted. The branch and a4db762 are retained.
The known pre-existing report work is preserved. The original checkout was not altered.

### Final preservation and exports

Final `git diff --check` passed. No staged files; HEAD remains a4db762da0092bf585493ad4cdacf61c5e872334
on `security/round1-server-economy`. The disposable PostgreSQL server was
cleanly stopped (`pg-stop.txt`). No committed secret was discovered in the
reviewed changes; literal live-secret marker scan found none. Only synthetic
credentials were used in tests. Previous exports are preserved.

Final `git status --short` from the application directory:

```text
 M app/arcade_rewards.py
 M app/arcade_routes.py
 M app/economy.py
 M app/main.py
 M docs/security-round1-server-economy.md
 M static/js/arcade-economy.js
 M static/js/history-mystery.js
 M templates/arcade.html
 M templates/arcade_game.html
 M templates/dressed_to_the_nines.html
 M templates/history_mystery.html
 M templates/interval_basic_training.html
 M templates/plunge_burrow.html
 M templates/scale_keyboard.html
 M templates/thirds.html
 M templates/wheel_of_woodchuck.html
 M tests/test_arcade.py
 M tests/test_history_mystery.py
?? app/history_attempts.py
?? app/safe_logging.py
?? tests/test_history_browser.py
?? tests/test_history_browser_state.js
?? tests/test_history_score_integrity.py
?? tests/test_sensitive_logging.js
?? tests/test_sensitive_logging.py
```

- Current report: `/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck/docs/security-round1-server-economy.md`.
- Timestamped report: `/home/geph/Downloads/Woodshed_Security_Round1_Report_20260916T054225188019Z.md`.
- Cumulative recovery patch: `/home/geph/Downloads/Woodshed_Security_Round1_Cumulative_20260916T054225188019Z.patch`.
- Patch base: **efee4d1f3a79ed57505c6b7db897968dd956e256**, the original main base of this security branch.
  It includes committed a4db762 security work, all preserved report additions,
  this continuation and all seven new source/test files. It is not a patch to
  apply on top of a4db762. No secrets, databases, test logs or temporary files
  are included.
- Verification: `git apply --check --whitespace=error` and actual application
  to an outside-repository snapshot of affected files at the stated base passed;
  every resulting file matched the current worktree bytes. No worktree, index,
  branch or stash was created/modified for this check. Hashes/file counts and
  the exact verification driver are saved in `patch-verification.json` and
  `export.py` in the evidence directory.

**Single next action for Jeff:** review this cumulative diff and the proposed
code-only controlled pre-beta release sequence with Product/Operations, keeping
limiter activation pending its separate gates. This review requires no deployment
or spending. No commit, push, merge, deployment, production/infrastructure change
or support message was performed in this continuation.

## September 16 bounded closeout and department handoff

This section is the current stopping point. Earlier sections retain their historical
status/results; this section and `docs/security-round1-handoff.md` summarize the
review candidate. **Ready for a bounded code-only release review, not release
approval, deployed protection, overall security clearance or unrestricted beta.**

### Starting state and preservation

Verified branch `security/round1-server-economy`, HEAD
`a4db762da0092bf585493ad4cdacf61c5e872334`; nothing staged. Starting status had
18 tracked modifications and seven intended new files, exactly the previous
continuation's final status above. The existing report updates, logging and History
implementation were expected. All 55 cumulative files matched the prior verified
patch snapshot byte-for-byte. No unexplained overlapping changes were found.

Current Git root is `/home/geph/Training_scripts-security-round1-server-economy`;
commands ran in its `woodshed-woodchuck` subdirectory. Applicable ancestor/repository
AGENTS.md checks found no instruction files. The original checkout, its checklist
and database backup, historical data, other branches/worktrees and stashes were not
touched. No backup was read/copied. Prior report text is preserved verbatim and this
closeout is appended; `/tmp/ww-security-closeout.07wtilsw/starting-report.md` and
`starting-status.txt` record the starting state outside Git.

### Concrete review corrections

1. **Delayed account-switch UI effects:** shared Arcade start/completion already
   refused an old account's balance update, but still wrote its reward message;
   start also retained the obsolete token in the client map. Both now check the
   captured account generation before those effects. History's delayed start,
   completion and initial-status errors/final rendering also respect that boundary.
   Cross-account and logout/re-login cases use the actual WWState and Arcade client
   flow; balance, score and current-account messages remain untouched. No reward,
   request interface, timing or server authorization rule changed.
2. **Malformed legacy quiz signature:** an unpaired Unicode surrogate in an old
   JSON signature raised an encoding exception. The completion route translated
   that ValueError subclass to 404 rather than the intended untrusted-progress 409;
   resume could not safely validate it. Signatures must now be ASCII before
   constant-time comparison. The HTTP regression proves rejection followed by
   zero-answer resume with the existing balance (19), without a second charge.
   This is reproduced against disposable SQLite legacy JSON, not a demonstrated
   production PostgreSQL insertion path. Normal server-generated signatures are
   unchanged. No lock, award transaction, schema or migration changed.
3. **Explicit code-only configuration proof:** a subprocess imports the app in
   production mode using a synthetic strong session key, Secure cookies,
   `LOGIN_RATE_LIMIT_MODE=off`, `LOGIN_RATE_LIMIT_REQUIRED=false`, wildcard
   FORWARDED_ALLOW_IPS and unset LOGIN_TRUSTED_PROXY_CIDRS. An inert test-only Redis
   URL is present and a backend-construction trap forbids any connection. The test
   verifies disabled/not-production-verified diagnostics, HTTPS student login,
   Secure/HttpOnly/SameSite=Lax cookie flags, state access, logout and subsequent 401.
   Existing production missing/unsafe-key and insecure-cookie rejection tests pass.
   This verifies local application configuration behavior, not Render ingress/TLS.

Review retained the existing backend/template/JavaScript protocol, no-answer-key
HTML, server-owned date-selected questions, answer order/ownership/game checks,
atomic final-answer payout, completed idempotent acknowledgments, same-choice retry,
refresh resume and Central-day expiry. Corrected cache assertions still require
shared Arcade v5 before arcade.js; History loads v2. These versions belong to the
same unreleased candidate, so closeout did not invent another asset rollout.

The actual emitted INFO/TRACE Uvicorn logging tests and handled SQL-error case pass
again: synthetic capabilities/queries/PINs/headers/cookies/exception payloads are
absent, while method, route template, status, timing, correlation and safe exception
locations remain. No further logging-code correction was necessary. Application
logging installation still follows the documented direct-Uvicorn import wiring;
provider/proxy/log-drain logging, past retained logs and future replacement logger
configuration remain outside this local evidence.

### Evidence verification and reproducibility

The previous logs are present, not reconstructed: `final-integrated.txt` records
**437 passed, 3 failed, 11 skipped**; `final-recheck.txt` records **35 passed**;
`final-js.txt` records **59 passed** in `/tmp/ww-security-logs-games.dvxn86q2`.
Their exact three failure identities/causes remain in the preceding section. The
35-pass recheck is separate evidence, not a replacement history of the integrated
run. That integrated run predates this closeout's small corrections. The earlier
1510-pass full-suite run also predates this candidate and is not a final-code test.

The same evidence directory's PostgreSQL race log (**3 passed, 10 deselected**),
real Chromium log (**2 passed**, mobile/desktop), emitted-log log (**3 passed**),
post-fix residual-score reproductions, default-schema and syntax logs also remain
available. Presence and tails are recorded in the new `prior-evidence.json`.
No cited closeout/prior-game log was missing. This does not attest that every older
historical `/tmp` artifact remains available indefinitely. No old result is presented
as a new execution. Concurrency locks and normal browser gameplay did not change;
those proofs were reused rather than restarting PostgreSQL/Redis/Chromium or a full
suite. The deferred eight-game/Plunge exploit inventory was not reopened.

New evidence directory: **`/tmp/ww-security-closeout.07wtilsw`**. Logs/temporary
schemas/bytecode stay outside Git. `run-safe` clears inherited environment values,
sets a known disposable SQLite default and synthetic session values, and loads no
project environment/secret files:

```bash
#!/bin/bash
exec env -i PATH=/home/geph/Training_scripts/woodshed-woodchuck/.venv/bin:/usr/bin:/bin LANG=C.UTF-8 TMPDIR=/tmp/ww-security-closeout.07wtilsw PYTHONPYCACHEPREFIX=/tmp/ww-security-closeout.07wtilsw/pycache PYTHONPATH=/tmp/ww-security-round1-continuation.z1hkSz/deps DATABASE_URL=sqlite:////tmp/ww-security-closeout.07wtilsw/default.db SESSION_SECRET=isolated-closeout-session-key-at-least-32-characters SESSION_COOKIE_SECURE=false LOGIN_RATE_LIMIT_MODE=off LOGIN_RATE_LIMIT_REQUIRED=false "$@"
```

Default-page database setup was reproduced explicitly before tests:
`initialize-default.py` asserts SQLite and the exact path
`/tmp/ww-security-closeout.07wtilsw/default.db`, imports models via app.main, then
runs Base.metadata.create_all there only. This handles the contest-admin test's
unchanged `/quest` lookup through main.SessionLocal. It is disposable test setup,
not a migration or repair of the application/production database.

Exact commands, from this application directory (`RUN` means the wrapper above):

```sh
# Reproduce legacy malformed signature, signature-before.txt
RUN pytest -q tests/test_history_score_integrity.py -k pre_release_browser --basetemp=/tmp/ww-security-closeout.07wtilsw/signature-before-tmp -o cache_dir=/tmp/ww-security-closeout.07wtilsw/cache
# Actual client-handler checks, browser-before.txt, browser-after.txt, browser-final.txt
RUN node --test tests/test_history_browser_state.js
# Guarded schema setup, default-schema.txt
RUN env PYTHONPATH=/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck:/tmp/ww-security-round1-continuation.z1hkSz/deps python /tmp/ww-security-closeout.07wtilsw/initialize-default.py
# Integrated focused closeout, focused.txt (also saved in focused.sh)
RUN pytest -q -ra tests/test_sensitive_logging.py tests/test_history_mystery.py tests/test_history_score_integrity.py tests/test_arcade_economy.py tests/test_arcade.py tests/test_contest_admin.py tests/test_session_hardening.py tests/test_site_admin_csrf.py tests/test_security_authorization.py tests/test_account_state_sync.py --basetemp=/tmp/ww-security-closeout.07wtilsw/focused-tmp -o cache_dir=/tmp/ww-security-closeout.07wtilsw/cache
# All lightweight Node tests, node-final.txt
RUN node --test tests/*.js
# Application compile/import and JavaScript syntax, syntax.txt
RUN env PYTHONPATH=/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck:/tmp/ww-security-round1-continuation.z1hkSz/deps python /tmp/ww-security-closeout.07wtilsw/syntax.py
# Repository whitespace, with new files also checked through cumulative patch application
 git diff --check
```

| Current closeout check | Exact result | Limits / explanation |
| --- | --- | --- |
| Malformed legacy signature before correction | 2 passed, 1 failed, 14 deselected | New surrogate case expected 409 and got 404; existing two cases passed. Same case passes in final focused selection. |
| Initial browser reproduction | 6 passed, 7 failed | Four genuine stale reward-message assertions plus one stale status-error assertion failed. Two start/finish rejection tests initially tried to mutate the frozen API; those failures were harness errors, not valid application evidence. |
| First browser fix check | 11 passed, 2 failed | Remaining two were the same frozen-API harness errors. Tests now replace the wrapper object as the existing retry tests do, preserving the actual handlers. |
| Final actual browser-handler checks | **13 passed, 0 failed/skipped** | Includes all seven added reward/error cases and the six prior switch/retry/expiry cases. |
| Integrated focused Python, final closeout code | **152 passed, 0 failed, 3 skipped** in 79.52 seconds | 155 cases covering logging, History rules/attacks, Arcade economy/assets, contest admin, sessions/CSRF, private authorization and account sync. Skips are exactly the three opt-in History PostgreSQL races; prior real PostgreSQL passing evidence reused. |
| All Node tests, final closeout code | **66 passed, 0 failed/skipped** | Shared Arcade change warrants retaining existing delayed-response/state, economy and console regression coverage; no broad Python suite rerun. |
| Compile/import/syntax | **66 Python files compiled; app.main imported; 33 JavaScript files checked; all passed** | 64 application Python files plus two touched Python test files; 32 application JS files plus the changed browser test. |

The 152-pass selection also revalidates both corrected Arcade cache assertions and
the contest-admin default-schema case in a fresh disposable location. There are no
new unresolved failures from this closeout. Ten unrelated historical baseline test
failures remain documented debt; their absence from this focused selection is not
proof of resolution. No tests were skipped/disabled to suppress a failure, no
existing assertion was weakened, and no full-suite result was manufactured.

### Closeout changed-file audit and release assessment

Relative to the preserved start of this closeout, only these files changed:

| File | Reason |
| --- | --- |
| `static/js/arcade-economy.js` | Guard stale-account token/message side effects before shared start/completion processing. |
| `static/js/history-mystery.js` | Guard delayed errors/final rendering against account-generation changes. |
| `app/history_attempts.py` | Treat malformed non-ASCII legacy signatures as untrusted before comparison. |
| `tests/test_history_browser_state.js` | Seven actual-handler reward/error account-switch regressions; shared deferred-response harness. |
| `tests/test_history_score_integrity.py` | Add unpaired-surrogate legacy JSON regression to existing HTTP test. |
| `tests/test_session_hardening.py` | Production-mode explicit-off HTTPS login/session/no-backend regression. |
| `docs/security-round1-server-economy.md` | Preserve history; append closeout, exact evidence and export record. |
| `docs/security-round1-handoff.md` (new) | Concise department release-review handoff and all remaining owners/actions/evidence gates. |

The previous continuation's full changed-file audit remains applicable to preserved
work. No unrelated implementation, formatting/CSS, dependency, schema/migration,
route removal, economics or historical-data change was added in closeout. Browser
and backend diffs and new files were inspected directly, without staging.

**Independent code-only release:** the local proof supports reviewing the candidate
with limiter explicitly off/required false, a strong existing production secret and
Secure cookies. Existing login/auth/economy/quiz flows and local HTTPS/CSRF checks
pass. Actual production base/schema/build, isolated staging matrix, deployment
approval, mixed-client/worker behavior and post-release smoke verification are
still pending. No local test proves deployed protection or Render header handling.
All fail-closed required-limiter and production-session safeguards are retained.

The handoff carries forward all unresolved findings: eight forged paid-game score
paths and non-expiring unfinished tokens; separate Plunge best/XP forgery; provider
proxy and sensitive-log guarantees; paid Key Value upgrade and enforcement gates;
historical balance/score and offline-earning reconciliation; self-reported practice/
activity policy; legacy client state; quiz lookup/automation limitations; PIN
recovery; baseline test debt; release/session/mixed-version/rollback verification.
The detailed per-game proposals and earlier provider verification procedures remain
in this report. None was silently implemented, cleared or dropped during closeout.

Known Render facts remain user-confirmed only: Available **Free** same-region
`woodshed-login-limits`, noeviction, private URL saved with “Save only,” unchanged
Uvicorn command, wildcard FORWARDED_ALLOW_IPS and unset LOGIN_TRUSTED_PROXY_CIDRS.
Provider guarantees remain unresolved. **Unchecked release requirement:** Jeff
approves the $10/month upgrade before limiter activation; Product/Operations owns
connectivity **and enforcement** verification. No deployment or spending is needed
merely to review this candidate. No provider contact or infrastructure change ran.

Marketing/artwork preparation, Legal/Finance setup, Support documentation and
existing tester observation can continue within established scope. Broader beta
clearance and verified-security claims remain gated. Proposed owners and exact
closure evidence are in the handoff; it has not been sent to any department.

### Closeout final Git state and verified exports

Branch `security/round1-server-economy`; HEAD `a4db762da0092bf585493ad4cdacf61c5e872334` retained.
Final `git diff --check` passed. There are 19 tracked modifications and eight
intended untracked files, including the new handoff. Nothing staged; no commits,
pushes, merges, deployments, production access or infrastructure changes in closeout.

Final `git status --short` from the application directory:

```text
 M app/arcade_rewards.py
 M app/arcade_routes.py
 M app/economy.py
 M app/main.py
 M docs/security-round1-server-economy.md
 M static/js/arcade-economy.js
 M static/js/history-mystery.js
 M templates/arcade.html
 M templates/arcade_game.html
 M templates/dressed_to_the_nines.html
 M templates/history_mystery.html
 M templates/interval_basic_training.html
 M templates/plunge_burrow.html
 M templates/scale_keyboard.html
 M templates/thirds.html
 M templates/wheel_of_woodchuck.html
 M tests/test_arcade.py
 M tests/test_history_mystery.py
 M tests/test_session_hardening.py
?? app/history_attempts.py
?? app/safe_logging.py
?? docs/security-round1-handoff.md
?? tests/test_history_browser.py
?? tests/test_history_browser_state.js
?? tests/test_history_score_integrity.py
?? tests/test_sensitive_logging.js
?? tests/test_sensitive_logging.py
```

- Current report: `/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck/docs/security-round1-server-economy.md`.
- Current handoff: `/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck/docs/security-round1-handoff.md`.
- Report export: `/home/geph/Downloads/Woodshed_Security_Round1_Report_20260916T062401283120Z.md`.
- Handoff export: `/home/geph/Downloads/Woodshed_Security_Round1_Handoff_20260916T062401283120Z.md`.
- Cumulative recovery patch: `/home/geph/Downloads/Woodshed_Security_Round1_Cumulative_20260916T062401283120Z.patch`.
- Recorded pre-security base: **efee4d1f3a79ed57505c6b7db897968dd956e256**. Apply to this base, not on top of a4db762.
- Includes the committed Round 1 change, all current tracked changes and all eight
  intended new files. No database, backup, secret configuration, temporary log or unrelated
  file is included. Prior exports remain untouched.
- Verification: `git apply --check --whitespace=error`, actual patch application and
  byte-for-byte comparison of every intended file passed in a disposable snapshot
  of affected files at the base. No working index, branch or registered worktree was
  used/changed. The exact driver, hashes, file list and final status are recorded in
  `export.py`, `patch-verification.json` and `final-status.txt` under the closeout
  evidence directory. The original report prefix remains byte-for-byte unchanged.

**Single next action:** Jeff/Product Operations review the handoff and cumulative
candidate, accept/assign remaining owners and decide whether to advance the bounded
limiter-off candidate to a separate release-review step. This is a review decision,
not deployment or expense approval. This security closeout stops here.
