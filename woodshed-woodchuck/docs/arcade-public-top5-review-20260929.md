# Woodshed-wide public Arcade Top 5 repair

## Review candidate

- Status: READY FOR PUBLIC TOP 5 REVIEW, subject to the baseline test failures listed below.
- Branch: `fix/arcade-public-top5-20260929`.
- Worktree: `/tmp/ww-arcade-public-top5-20260929`.
- Base: `f7f194ee1d1de93a247df650497df20ea8b6fa65` (approved logo overlay).
- Production baseline beneath the overlay: `58bced5a26ce94b1a7908765dc67adf7e0fedc4a`.
- SEC-003 remains enforced. There is no promotion of arbitrary browser totals.
- No merge, deployment, production data access, or edits to the original dirty worktree.
- `ui/welcome-logo-overlay-20260929` remains at the base SHA above. Its files were not edited.

The candidate commit contains this report; its exact SHA is supplied in the review handoff.

## Authority and publication

| Game | Server authority | Shared Top 5 |
| --- | --- | --- |
| Thirds | Persists the current chord, accepts an ordered A–G answer, and awards one point for correctness. Chooses each next chord. | Restored |
| Dressed to the Nines | Persists the tonality/start note and validates the selected ninth against the server answer. One point per correct answer. | Restored |
| Interval Basic Training | Issues the MIDI interval, validates the selected label, counts mistakes, and stops on the second mistake. One point per correct answer. | Restored |
| Scale Keyboard | Issues the scale and tracks its next note. Correct notes earn 100; wrong notes deduct 50, floored at zero; completing all eight notes earns 500. | Restored |
| History Mystery | Existing signed, ordered answer evidence and scoring remain in use. New completions also record the authoritative score. | Retained |
| Blue, Radio Tuner, Wheel of Woodchuck, Plunge Burrow | Client claims remain self-reported; no new public score or prize is awarded. | Not restored |

The four repaired games share `arcade_challenges.py`, one action route, one browser adapter, and the existing play lifecycle. Challenge state belongs to the play database row, outside browser-synchronized account state. Start request IDs, ownership, attempt packs, and completion idempotency remain enforced. Profile/play locks serialize actions against duplicate submissions and completion. Repeating the last identical action returns its result; changed duplicates and out-of-order actions are rejected.

A completion must match the score accumulated from accepted server-validated actions. Merely owning a play token confers no scoring authority. The final-score endpoints reject injected totals, including `2147483647`. No payouts were enabled for the four repaired games.

The run lasts 30 seconds. The server allows one second of early completion tolerance for countdown/network skew, and accepts actions only through 32 seconds from `started_at`. Each action also has a conservative minimum spacing: 100 ms for Thirds/Nines, 650 ms for Intervals, and 50 ms for Scale Keyboard. The action timestamp is read after obtaining locks. Late completion can only seal points already earned inside that window. Expired runs cannot accumulate more points. A new start closes an expired verified run before consuming a new attempt; resuming an active run preserves its question, score, and remaining time. An unfinished legacy run without evidence closes without a public score.

These controls verify the game actions and score; they do not establish whether the answers were supplied by a human.

### Public projection

`publishable_attempt_bests()` selects completed attempts with an authoritative score, grouped by profile. Both start and completion must satisfy `public_from`; current `can_publish()` consent/privacy checks still apply, and deleted/inactive profiles are excluded. No age, consent, or privacy rules were loosened.

All viewers receive the same eligible public standings, with descending scores, Olympic ranks (1, 1, 3), deterministic tie ordering, and at most five rows. A viewer's historical private best is no longer inserted into the shared board. `best_score` remains a separate personal value; it can exceed the player's publishable best, including when it predates this repair.

History Mystery has one compatibility check in the projection: a legacy NULL score can qualify only when its surviving signed answer evidence is independently verified and matches the completed result. Missing or altered evidence does not qualify. The migration does not backfill scores. Older History attempts whose evidence no longer survives remain private; the submitted total alone is insufficient proof. History gameplay and reward rules are unchanged.

### Migration

`f19arcade001` follows production `e18tester001`. It adds nullable `authoritative_score` and `challenge_state` columns to `arcade_play_sessions`. NULL is not treated as a trusted saved score. No old browser results are converted, and no existing score data is rewritten.

Upgrade, downgrade to `e18tester001`, and re-upgrade passed on disposable PostgreSQL and SQLite, including schema comparison and preservation of a legacy forged total as untrusted data. Downgrading removes the new evidence columns; re-upgrading leaves those old rows untrusted. Apply the additive migration before running the new application code.

The current Top 5 markup and game layouts are retained. Shared script and game script cache versions were bumped, and completion messages now distinguish verified competition from self-reported practice.

## Validation

Only synthetic accounts and disposable databases were used. Test commands cleared inherited environment variables, set `DATABASE_URL=sqlite://`, and used a synthetic session secret. PostgreSQL ran in a separately initialized local cluster on port 56529, database `ww_billing_a2_test`, with isolated schemas. Its durability settings were disabled for disposable test speed; these were transaction/locking tests, not crash-recovery tests.

The new security suite covers all four games: final-score injection through both completion aliases, immediate completion, owned-token bypass attempts, other-account token use, replay conflicts, idempotent duplicate actions, invalid/out-of-order actions, impossible action rates, expiry, correct scoring, another account's leaderboard, private/under-13/deleted profiles, withdrawn consent, historical publication boundaries, Olympic ties, five-row truncation, request retry/resume, legacy unfinished plays, and PostgreSQL concurrency. Blue still cannot publish a forged score. History signed-evidence compatibility and tamper rejection are also covered.

Each real Chromium test uses the existing DOM controls and real 30-second timing. Account A answers a server challenge; a deliberately lost successful action response is retried without double scoring. The result is persisted as authoritative. Account B signs in, opens the same game, and sees A's name and score. Forged completion and replay totals return 409 and do not alter the standings.

### Results

| Final validation | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Combined Arcade, SEC-003, four game units, privacy/age, Release 3, History integrity, and new authority suite | 354 | 20 baseline | 53 |
| New migration plus Release 3 migrations, SQLite and PostgreSQL | 6 | 10 baseline | 0 |
| Real Chromium multi-account flows, one per repaired game | 4 | 0 | 0 |
| Release 3 and History browser-state Node tests | 20 | 0 | 0 |
| **Total distinct cases** | **384** | **30 baseline** | **53** |

The new authority suite contributes **52 passed, 8 skipped** within the combined row. The eight skips are SQLite versions of concurrency cases that passed on PostgreSQL. Remaining skips are pre-existing focused-suite skips. Baseline focused run: 254 passed, 20 failed, 93 skipped (PostgreSQL was not enabled in that initial run). Enabling PostgreSQL exercised 48 additional existing cases successfully. Baseline migration run with PostgreSQL enabled: 4 passed, 10 failed. Failure-ID comparisons are exact: **zero new failures** in either comparison.

All five modified JavaScript files passed `node --check`. `git diff --check` passed.

Reproduction uses the project Python environment and a freshly initialized local PostgreSQL test database:

```sh
env -i PATH=/home/geph/Training_scripts/woodshed-woodchuck/.venv/bin:/usr/bin:/bin \
  PYTHONDONTWRITEBYTECODE=1 DATABASE_URL=sqlite:// \
  SESSION_SECRET=top5-local-test-only-secret LOGIN_RATE_LIMIT_MODE=off \
  WW_BILLING_TEST_POSTGRES_URL=postgresql+psycopg://ww_test@127.0.0.1:56529/ww_billing_a2_test \
  pytest -q -p no:cacheprovider --basetemp=/dev/shm/ww-top5-integrated \
  tests/test_arcade_public_authority.py tests/test_arcade.py tests/test_arcade_economy.py \
  tests/test_sec003_authority.py tests/test_thirds.py tests/test_dressed_to_the_nines.py \
  tests/test_interval_basic_training.py tests/test_scale_keyboard.py \
  tests/test_age_screening.py tests/test_instrument_medal_privacy.py \
  tests/test_release3_arcade.py tests/test_history_score_integrity.py
```

With the same isolated environment, run `tests/test_arcade_authority_migration.py tests/test_release3_arcade_migration.py` for migrations and `tests/test_arcade_authority_browser.py` for Chromium. Node command: `node --test tests/test_release3_arcade.js tests/test_history_browser_state.js`.

The 20 existing focused failures concern old page/artwork/audio expectations and legacy game HTTP fixtures/contracts (including missing middleware database patching, old start payloads, and old reward/entry-cost assumptions). They also fail on the exact approved base. They were not made into passing tests by weakening production behavior. New HTTP and browser tests exercise the repaired behavior with complete fixtures.

The ten Release 3 migration failures concern an older downgrade path from the current head past `e18tester001` to `b14tester001`: later migrations mutate schema before the old R3 refusal check. All ten also reproduce on the approved base. The new migration's own upgrade/downgrade/re-upgrade tests pass.

Initial repair-test failures were resolved: scale comparison across JSON serialization, a PostgreSQL fixture identifier exceeding its column width, browser page readiness/user-gesture setup, and the completion timing edge. The final comparison contains no repair-only failure IDs.

Logs for this review are under `/tmp/ww-top5-validation/`. The disk-backed exploratory migration run was superseded by the completed tmpfs run. Browser evidence is the three passing final batch cases plus the passing Thirds rerun after the timing fix; the earlier Thirds process had loaded the pre-fix server.

## Smallest proper future mechanisms

| Game | Recommended next authority model |
| --- | --- |
| Blue | Move the fixed map, collectible state, movement, collision, checkpoints, stage transitions, and stage bonus into a deterministic server simulation. Accept ordered direction/jump inputs with server-bounded simulation ticks. Recompute pickups and score; do not accept claimed coordinates or pickup events as proof. |
| Radio Tuner | Replace the browser timer's evolving needle with a server-defined time/phase function. Score each ordered tap from bounded server time and authoritative needle position, with an explicit latency policy and tap cadence. A client-supplied tap timestamp or needle position alone is insufficient. |
| Wheel of Woodchuck | Extend the challenge mechanism with server-owned puzzles, spin outcomes/values, guessed letters, misses, spin/guess state, solve validation, bonuses, and the 45-second deadline. Reveal only the prompt/mask; reject repeated guesses and invalid spin transitions. This is the smallest next extension, but includes more state than the four repaired games. |
| Plunge Burrow | Persist a server-generated board/seed and simulate ordered directional inputs at the authoritative movement cadence. Derive trail growth, collisions, hearts, portals, pickups, instrument sets, and bonuses. Validate termination and bound run resources. Existing claimed pickup/score/XP events remain insufficient. |

## Changed files

- `woodshed-woodchuck/app/arcade_challenges.py`
- `woodshed-woodchuck/app/arcade_rewards.py`
- `woodshed-woodchuck/app/arcade_routes.py`
- `woodshed-woodchuck/app/arcade_scores.py`
- `woodshed-woodchuck/app/models.py`
- `woodshed-woodchuck/docs/arcade-public-top5-review-20260929.md`
- `woodshed-woodchuck/migrations/versions/f19arcade001_authoritative_scores.py`
- `woodshed-woodchuck/static/js/arcade-economy.js`
- `woodshed-woodchuck/static/js/dressed-to-the-nines.js`
- `woodshed-woodchuck/static/js/interval-basic-training.js`
- `woodshed-woodchuck/static/js/scale-keyboard.js`
- `woodshed-woodchuck/static/js/thirds.js`
- `woodshed-woodchuck/templates/arcade.html`
- `woodshed-woodchuck/templates/arcade_game.html`
- `woodshed-woodchuck/templates/dressed_to_the_nines.html`
- `woodshed-woodchuck/templates/history_mystery.html`
- `woodshed-woodchuck/templates/interval_basic_training.html`
- `woodshed-woodchuck/templates/plunge_burrow.html`
- `woodshed-woodchuck/templates/scale_keyboard.html`
- `woodshed-woodchuck/templates/thirds.html`
- `woodshed-woodchuck/templates/wheel_of_woodchuck.html`
- `woodshed-woodchuck/tests/test_arcade.py`
- `woodshed-woodchuck/tests/test_arcade_authority_browser.py`
- `woodshed-woodchuck/tests/test_arcade_authority_migration.py`
- `woodshed-woodchuck/tests/test_arcade_economy.py`
- `woodshed-woodchuck/tests/test_arcade_public_authority.py`
- `woodshed-woodchuck/tests/test_dressed_to_the_nines.py`
- `woodshed-woodchuck/tests/test_interval_basic_training.py`
- `woodshed-woodchuck/tests/test_release3_arcade.py`
- `woodshed-woodchuck/tests/test_scale_keyboard.py`
- `woodshed-woodchuck/tests/test_sec003_authority.py`
- `woodshed-woodchuck/tests/test_thirds.py`

## Baseline failure IDs

The following IDs fail both on the approved base and in the final validation.

```text
FAILED tests/test_dressed_to_the_nines.py::test_entry_cost_insufficient_balance_and_nines_payout_tiers
FAILED tests/test_dressed_to_the_nines.py::test_nines_daily_reward_cap_is_per_game
FAILED tests/test_dressed_to_the_nines.py::test_nines_uses_sand_drop_and_shared_arcade_mute
FAILED tests/test_dressed_to_the_nines.py::test_personal_best_top_five_privacy_and_lower_score_behavior
FAILED tests/test_dressed_to_the_nines.py::test_seventh_cabinet_and_nines_route_are_authenticated
FAILED tests/test_interval_basic_training.py::test_answer_grid_has_exact_order_and_remains_three_by_three_on_mobile
FAILED tests/test_interval_basic_training.py::test_eighth_cabinet_stable_key_and_authenticated_route
FAILED tests/test_interval_basic_training.py::test_independent_personal_best_and_privacy_safe_top_five
FAILED tests/test_interval_basic_training.py::test_interval_daily_cap_is_independent
FAILED tests/test_interval_basic_training.py::test_interval_economy_cost_payout_idempotency_and_insufficient_balance
FAILED tests/test_interval_basic_training.py::test_interval_soundtrack_is_idle_only_and_uses_shared_mute_state
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[completed_legacy_request-postgresql]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[completed_legacy_request-sqlite]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[free_play-postgresql]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[free_play-sqlite]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[pack-postgresql]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[pack-sqlite]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[paid_play-postgresql]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[paid_play-sqlite]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[request_only-postgresql]
FAILED tests/test_release3_arcade_migration.py::test_downgrade_refuses_before_mutating_r3_history[request_only-sqlite]
FAILED tests/test_scale_keyboard.py::test_fifth_cabinet_and_authenticated_route
FAILED tests/test_scale_keyboard.py::test_scale_keyboard_uses_gerry_four_through_shared_soundtrack
FAILED tests/test_scale_keyboard.py::test_scale_personal_best_persists_through_play_api
FAILED tests/test_thirds.py::test_existing_arcade_frames_keep_distinct_outer_identities_and_dark_inner_panel
FAILED tests/test_thirds.py::test_sixth_cabinet_and_thirds_route_are_authenticated
FAILED tests/test_thirds.py::test_thirds_payout_tiers_and_per_game_daily_cap
FAILED tests/test_thirds.py::test_thirds_top_five_keeps_privacy_safe_names_and_olympic_ties
FAILED tests/test_thirds.py::test_thirds_uses_four_repeat_mp3_without_looping_and_shared_arcade_mute
FAILED tests/test_thirds.py::test_thirds_uses_shared_play_session_once_and_persists_personal_best
```
