# Executive Result

**SEC-003 REPAIR REQUIRED — full intended October beta remains blocked.**

Audit date: 2026-09-25. Local draft; not sent or published. Baseline and audited HEAD: `fb2d2270f966d0f5cd594c83accf2efa83e2b75c`. Branch: `security/sec-003-server-authoritative-results-20260925`. Worktree: `/home/geph/Training_scripts-sec003`; application directory: `woodshed-woodchuck`. No application fixes, staging, commits, pushes, merges, deployments, production requests, or production data changes were performed.

Six P1 findings demonstrate or trace persistent reward/competitive forgery. No P0 was found within this scope. Authentication and idempotency are substantial existing protections, but neither establishes that an activity actually happened. Eight of nine implemented Arcade games accept an arbitrary bounded score. History Mystery independently scores ordered answers. Plunge's separate event endpoint grants up to **10 XP per Central day without any play session**. BOOK and Pristine submissions introduce larger, effectively unbounded lifetime XP exposure; arbitrary BOOK dates also bypass any real-world daily earning limit.

Local HTTP proofs used synthetic accounts and an in-memory SQLite database. All eight unchecked Arcade games accepted `2147483647` immediately after start, awarded 5 dandelions, and exposed that score to a second eligible synthetic account. Exact replays did not pay again. One invented `band_complete` event produced 10 Plunge XP with zero play rows. Two BOOK charts (today and 1900-01-01) each earned 75 dandelions and together added 2,882 XP. Two immediate Pristine submissions each claimed 86,400 seconds and added another 2,880 XP. These are acceptance proofs, not attacks against production.

**Inventory: 36 matrix rows**, including disabled features and shared downstream reward paths; aliases are grouped in their row rather than counted as additional games. Nine playable Arcade games were found. Authority totals: **7 SERVER AUTHORITATIVE; 1 SERVER VALIDATED; 10 PARTIAL TRUST; 9 CLIENT ASSERTED; 2 NO PERSISTENT REWARD; 7 NOT REACHABLE / DISABLED**. Finding totals: **P0 0 / P1 6 / P2 0 / P3 1**. Row severities refer to findings, not additional finding counts. “—” means no demonstrated vulnerability in that boundary.

# Game / Result Matrix

For Arcade rows, `start` means `POST /arcade/plays` with browser-selected `game_key` and `request_id`; `complete` means `POST /arcade/plays/{play_token}/complete` with `score`. The registered compatibility alias `POST /arcade/scores/{game_key}` accepts `score` and `play_token`, checks game binding, and rolls back on a mismatch. Each game's row includes both result routes. Even `/arcade/scores/plunge-burrow` can reach the shared completion handler; its GET counterpart is not a valid Arcade score key. Public Plunge reads use `/xp/plunge-best`.

| Game/feature | Submission endpoint | Persistent effect | Authority classification | Severity | Evidence |
|---|---|---|---|---|---|
| Plunge Burrow result | start → complete / score alias | Play completion, profile best, shared Top 5, 0–5 dandelions | CLIENT ASSERTED | P1 F01 | `app/arcade_rewards.py:362`; `static/js/plunge-burrow.js:531,706` |
| Blue | start → complete / score alias | Completion, best, shared Top 5, dandelions | CLIENT ASSERTED | P1 F01 | `app/arcade_rewards.py:362`; `static/js/arcade.js:465,496` |
| Radio Tuner | start → complete / score alias | Same | CLIENT ASSERTED | P1 F01 | Same server handler; `static/js/arcade.js:24,465` |
| Wheel of Woodchuck | start → complete / score alias | Same | CLIENT ASSERTED | P1 F01 | `static/js/wheel-of-woodchuck.js:313,478` |
| Scale Keyboard | start → complete / score alias | Same | CLIENT ASSERTED | P1 F01 | `static/js/scale-keyboard.js:249,280` |
| Thirds | start → complete / score alias | Same | CLIENT ASSERTED | P1 F01 | `static/js/thirds.js:173,201` |
| Dressed to the Nines | start → complete / score alias | Same | CLIENT ASSERTED | P1 F01 | `static/js/dressed-to-the-nines.js:200,228` |
| Interval Basic Training | start → complete / score alias | Same | CLIENT ASSERTED | P1 F01 | `static/js/interval-basic-training.js:288,328` |
| History Mystery | start → `POST /arcade/plays/{token}/answer`; complete aliases | Daily quiz, server-scored best/Top 5, dandelions | SERVER AUTHORITATIVE | — | `app/history_attempts.py:45–114`; `app/arcade_rewards.py:462`; `static/js/history-mystery.js:205` |
| Plunge event XP | `POST /xp/plunge-points` | Event rows; capped lifetime XP contribution | PARTIAL TRUST | P1 F02 | `app/xp_routes.py:44`; `app/xp.py:141–215`; `static/js/plunge-burrow.js:464` |
| Legacy device Plunge best importer | `POST /xp/plunge-best` | Would update personal best; currently no eligible caller can pass both gates | NOT REACHABLE / DISABLED | — | `app/xp_routes.py:80–103`; `app/account_routes.py:135`; `static/js/app.js:2438` |
| Note Names | No play/result route | Locked Classroom placeholder | NOT REACHABLE / DISABLED | — | `app/arcade_access.py:9–23`; `templates/skill_building.html:8` |
| Instrument Fingerings | No play/result route | Same | NOT REACHABLE / DISABLED | — | Same |
| Rhythm — Hear & Pick | No play/result route | Same | NOT REACHABLE / DISABLED | — | Same |
| Key Signatures | No play/result route | Same | NOT REACHABLE / DISABLED | — | Same |
| Transposition | No play/result route | Same | NOT REACHABLE / DISABLED | — | Same |
| Rhythm Baseball | No play/result route | Presentation-only cabinet/coming-soon Top 5 | NOT REACHABLE / DISABLED | — | `templates/arcade.html:155–162`; absent from `ARCADE_PLAY_GAME_KEYS` |
| Current Bonus Challenge | `POST /contests/bonus-challenge/progress` | Completion, 5 dandelions, 2 Camp Points/XP, quest streak | PARTIAL TRUST | P1 F06 | `app/contests.py:2966–3125`; `static/js/app.js:1335` |
| Legacy quest completion | `POST /contests/quest/completions` | Same daily completion/reward; saved display practice log | PARTIAL TRUST | P1 F06 | `app/contests.py:3128–3268` |
| BOARD hours | `POST /contests/camp-points/awards`, type `hours` | 1 Camp Point/XP and dandelion daily; competition/crown progress | PARTIAL TRUST | P1 F05 | `app/contests.py:880–931,3428`; `static/js/app.js:2544` |
| BOARD care | Same, type `care` | Same | PARTIAL TRUST | P1 F05 | Same |
| BOARD marching | Same, type `marching` | Same | PARTIAL TRUST | P1 F05 | Same |
| BOARD trivia | `POST /contests/trivia/answer`; award alias type `trivia` | One fixed question/attempt daily; correct answer earns point, dandelion, crown progress | SERVER AUTHORITATIVE | — | `app/contests.py:3350–3483`; `static/js/app.js:2873` |
| BOOK P-Chart | `POST /practice-charts` | History, minutes + chart-count XP, dandelions, streak, Open/team/instrument standings | PARTIAL TRUST | P1 F03 | `app/practice_chart_routes.py:360`; `app/practice_charts.py:73–319`; `static/js/app.js:4607` |
| Pristine Practice | `POST /practice-charts/pristine` | Claimed detected time; XP, history, streak, Open/Pristine/team standings | CLIENT ASSERTED | P1 F04 | `app/practice_chart_routes.py:469`; `app/practice_charts.py:322`; `static/js/pristine-practice.js:216` |
| Family/private practice | `POST /family/practice` | BOOK-derived XP/currency/history; contest flags forced false | PARTIAL TRUST | P1 F03 | `app/family_routes.py:178–190` |
| Login daily/streak reward | Account create/login and `POST /account/login-streak` | Server-date streak, daily currency, crown every 7 consecutive days | SERVER AUTHORITATIVE | — | `app/account_routes.py:84,260,302,313`; `app/login_streaks.py:169–270` |
| Daily secret | `POST /account/daily-secret` | 20 dandelions once per Central day for matching passcode | SERVER AUTHORITATIVE | — | `app/account_routes.py:394–436` |
| Mum weekly snack | `POST /store/mum/snacks` | One allowed inventory item per Central week | SERVER AUTHORITATIVE | — | `app/store_routes.py:147`; `app/store_inventory.py:804` |
| Store purchase / placement | `POST /store/purchases`; inventory placement/size PUT/DELETE | Catalog-priced debit and item copy; owner-only placement | SERVER AUTHORITATIVE | — | `app/store_routes.py:69–200`; `app/store_inventory.py:419–937` |
| Generic account state / signup import | `PUT /account/state`; initial state on create | Preferences/display state; protects authoritative balance, progression, ownership | SERVER AUTHORITATIVE | — | `app/economy.py:33–66`; `app/account_routes.py:576–680`; `app/accounts.py` |
| Weekly contest finalization / reconciliation | Admin `/contests/weeks/{week}/finalize`, `/admin/contests/*`; job; crown-progress GET reconciliation | Computed ranks/results, medals, 50/25/15 dandelions, placement Camp Points, crowns/cups | PARTIAL TRUST | P1 upstream F03–F06 | `app/contests.py:1780,1871,2629,3486`; `app/contest_admin.py`; `app/contest_jobs.py` |
| Director contests | `POST /director/contests`, `POST /director/contests/{id}/finalize` | Computed team contest result/history, Hall output | PARTIAL TRUST | P1 upstream F03–F04 | `app/director_dashboard.py:334–431,459–502` |
| Authorized adult verification | `POST /trusted-verifiers/practice-charts/{id}/respond`; `/family/director/review/{id}` | Review status permits Verified division | SERVER VALIDATED | — | `app/verifier_routes.py:687`; `app/practice_charts.py:354–447`; `app/family_routes.py:248` |
| Guest tools / local display history | Guest pages, browser storage | Local preferences, timers, old display stats; no authoritative account grant | NO PERSISTENT REWARD | — | `app/main.py:371`; `static/js/guest.js`; `static/js/state.js`; `static/js/app.js:2470–2534` |
| Arcade/Pristine entry analytics | GET page render server hook; no event POST | Internal deduplicated entry observation; no reward/score consumer | NO PERSISTENT REWARD | — | `app/analytics.py:60–112`; `app/analytics_routes.py` |

Classification rationale: the eight game **results** are client asserted even though the separate accounting layer verifies identity, play ownership, bounds and deduplication. Pristine's purported detection result is similarly asserted. PARTIAL TRUST rows have fixed/capped server reward or aggregation rules but retain an unverified activity input. These categories do not mean any P1 row is safe. SERVER VALIDATED adult review means an independently authorized reviewer must make the decision; it is not a guarantee against colluding adults.

## Browser input and server knowledge

| Path family | Browser-controlled fields | Server recomputation/checks | Missing independent evidence |
|---|---|---|---|
| Eight Arcade result paths | `game_key`, arbitrary fresh `request_id`, owned `play_token`, integer `score` | Known game; current account/eligibility; entry pack/entitlement; score 0–2,147,483,647; payout table; per-game daily completed-play cap; one completion | No movement, timing, collisions, questions, answers, lives, win, seed, or correct-answer count is submitted/verified. Non-History `started_at` is not used to validate duration. |
| History Mystery | Start ID; ordered `question_index`/`choice`; optional completion score acknowledgment | Own daily token, current date, signed server question/progress record, valid choice, order, one answer per index; recomputed sum 0–5 | Human effort cannot be proved; submitting correct answers is the intended action. Direct score injection cannot replace those answers. |
| Plunge XP | `event_key`, `event_type`, `points_scored` | Type whitelist and exact table match, server UTC timestamp, per-key replay, 10/day XP aggregation | No play token or proof of pickups/band completion; client UUID is merely a deduplication label. |
| BOOK/private BOOK | Date, minutes, note, details; optional key/reviewer; public BOOK contest flags and `credits_awarded` | 1–1440 minutes per row, details dedup/30 maximum, accepted reviewer relationship, currency recomputed/capped 75 per supplied date; family forces contest exclusion | No date window, total time/day cap, start/finish record, actual-practice evidence, or required verification before XP/currency/Open credit. |
| Pristine | Detected seconds, fresh key, contest flags | Strict integer 1–86400; server date; minutes = seconds // 60; no dandelions; DB key uniqueness | No server detection or elapsed-time evidence; repeat keys changed at will; no aggregate duration cap. |
| Bonus current / legacy | Current date and instance; optional current minutes ignored; legacy quest ID/minutes/logged_minutes | Configured challenge and server target; fixed 5 + 2 reward; one completion per account/day; legacy requires reported logged minutes ≥ target | Current handler assigns `logged_minutes = target_minutes; completed = True`. Neither handler establishes practice. |
| BOARD non-trivia | Today's date, activity type | Allowlist; server time/team; 1 point and currency; one row/type/day | No proof of hours, care, or marching. |
| BOARD trivia | Today, selected answer ID; award alias supplies type/date | Compares with server question; remembers first attempt; alias requires saved correct attempt | No independent proof of human solving is necessary for this result contract. |
| Login, secret, snack, purchase | Authenticated request; passcode or item choice | Calendar, streak and fixed amounts; secret equality; allowed snack; catalog price/balance/ownership | No extra gameplay prerequisite claimed by these routes. Intentional daily claim is not a forged game result. |
| Account sync | Nearly arbitrary JSON and revision | Rebuilds identity/profile fields, preserves credits/level/streak/lastCompletedDate, inventory ownership/crowns/medals, quiz state and appearance; revision conflict | Other JSON remains presentation data; its mere persistence is not trusted XP, currency, medal or inventory ownership. |

Client evidence also includes `static/js/arcade-economy.js:155–224` (start retries and score payload), `static/js/scale-keyboard.js:3–7,56–88` (local timing and score), `static/js/thirds.js:20–88`, `static/js/dressed-to-the-nines.js:30–99`, `static/js/interval-basic-training.js:31–127`, and `static/js/wheel-of-woodchuck.js:25–213` (local random challenges/answers). Radio's client cap is 50,000 (`static/js/arcade.js:24`); the server accepted 2,147,483,647. UI checks cannot protect these routes.

# Plunge Burrow Deep Dive

1. **Start:** browser Start calls shared `startPlay("plunge-burrow")` (`static/js/plunge-burrow.js:701–718`). Server creates/resumes an account-owned `ArcadePlaySession` with random `token_urlsafe(32)`, game key and `started_at`. Plunge is always free. There is no authoritative game board, seed, movement stream or start nonce for the XP stream.
2. **Local result:** `collectAt` awards dandelion +1, carrot +3, instrument +5 and full band +20 (`static/js/plunge-burrow.js:281–313`). Local hearts/collisions/timing determine game over. No server checks these facts.
3. **Two independent submissions:** each pickup emits `/xp/plunge-points`; game-over calls `completePlay(score)` (`static/js/plunge-burrow.js:469–486,531–559`). The event ID is a browser UUID/random string + sequence + event type. It is unrelated to the server play token and the sequence need not exist server-side.
4. **XP calculation:** the API strictly rejects unknown fields (including client timestamps), non-integer points, unknown types, and wrong type/point pairs. It writes `PlungePointAward`; `plunge_xp()` groups persisted events by server-time Central date, sums each day's points, and caps each day at 10. It does not directly increment a writable XP total.
5. **Completion:** shared completion accepts any integer score in range. Payout thresholds are 10→1, 25→2, 50→3, 100→5. First ten completions per game/Central day can pay; later completions still update best/leaderboard. This allows 50 forged dandelions/day from Plunge alone and another 50 from free Blue. It does not allow arbitrary per-request currency amounts.
6. **Persistence:** completion atomically mutates `WoodchuckState.state_json.progress.credits` and revision, updates `WoodchuckProfile.plunge_best_score`, and records submitted score, payout, completed time and reward-granted time in the play row. Plunge event XP and play completion occur in separate transactions: either can exist without the other.
7. **Replay:** same event key/data returns `created=false`; changed data under that key returns 409; a fresh key creates a new raw event but cannot exceed today's effective 10 XP. Same completed play/score returns the saved payout without adding it again; a different score returns 409. Fresh start IDs after completion yield new plays. Profile/play row locks serialize PostgreSQL writes; unique event keys also have an IntegrityError recovery path.
8. **History/public effect:** Plunge best/Top 5 persists server-side and appears in Arcade and BOARD Burrow panels. Public entries require eligible 13+/adult consent state and attempts started/completed after `public_from`. A forged **new** play satisfies those provenance timestamps. Plunge points/bests are not consumed as weekly contest scores or Camp Points. Direct Plunge forgery therefore affects XP, spendable currency and shared Arcade/Burrow ranking, not a direct medal issuance API.
9. **Browser best import:** `static/js/app.js:2438–2457` still contains a local-best importer, but current eligible accounts have `AccountPrivacy` and get 409 on the POST; accounts without it fail `current_profile`'s age gate with 403. This registered legacy endpoint is effectively disabled at this baseline. Editing localStorage alone does not change server XP/balance; arbitrary requests to the live event/completion paths do.

**Answer:** yes, an eligible signed-in user can manufacture Plunge XP and rewards without playing. One valid-looking invented band event earns the full **10/day**, not unbounded immediate XP; immediate fabricated completions earn up to **50 dandelions/day** and arbitrary bounded personal/public scores. A server play token is necessary for completion, but it is freely obtainable; it proves authorization to enter, not gameplay.

# Reward / Economy Authority

There is a shared locked state row, not a single universal grant endpoint. `app/economy.py:11–30` locks profile then state and refreshes JSON after waiting. Actual dandelion writers are:

- Arcade `_set_balance` / `complete_arcade_play` (`app/arcade_rewards.py:119,416–445`): table payout; provenance in `ArcadePlaySession`, not `RewardGrant`.
- BOOK creation (`app/practice_charts.py:223–273`): `min(minutes // 5 + distinct_details, remaining 75 for practice_date)`; `RewardGrant(source_key="practice-chart:{id}")`.
- Contest `_add_dandelion` and `_grant_once` (`app/contests.py:1383–1434`): internal amounts; current placements use **50/25/15**, plus **3 Camp Points** per placement and gold crown progress (`:1673,1780–1821`). BOARD grants 1; both Bonus routes grant 5 and 2 Camp Points.
- Login streak (`app/login_streaks.py:247–269`) and secret (`app/account_routes.py:414–429`): server date/streak/table, provenance grant.
- Store purchase (`app/store_inventory.py:841–884`) subtracts the catalog price and creates an owned copy. Mum's weekly item uses `OwnedItemCopy.acquisition_key`; it is not a currency increment.

Internal helpers accept amounts from trusted Python callers, but no generic HTTP “grant arbitrary amount” route was found. Arcade and XP schemas forbid extra reward/identity fields. Some older schemas ignore extras, but those extras are not used as reward amounts. BOOK's `credits_awarded` input is checked then recomputed in the HTTP path; choosing `0` does not prevent minting via forged minutes.

XP is computed by `app/xp.py:218–242`: positive chart seconds / 60 + sum of lifetime Camp Points + count of positive `p-book` charts + per-day-capped Plunge points. Review rejection does **not** retract chart XP. No writable lifetime XP API exists. `/account/profile/level` changes the descriptive instrument proficiency string, not XP thresholds or calculated `progress.level` (`app/accounts.py`, `app/xp.py:245`).

`preserve_server_values()` and revision checks prevent signup/state sync from importing currency, progression, inventory ownership, crowns or medals. Unknown display keys, local practice logs and local Band Camp winners can still be edited, but authoritative services query tables. One limited exception to “preferences only” is selection of a configured current Bonus Challenge via `daily.questId` (`app/contests.py:2820–2840`); this cannot change its fixed payout/day uniqueness and is included in F06's repair. Do not trust this synchronized key as a server challenge assignment in a repair.

# Replay / Idempotency

| Mechanism / DB constraint | What it actually guarantees | Limit |
|---|---|---|
| `uq_arcade_start_request(profile_id, request_id)` | Same account/start ID returns original play, even after completion | Browser chooses new IDs; not proof of play |
| `uq_arcade_play_session_token(play_token)` + profile/play locks and completed marker | One accepted score/payout per owned play; conflicting replay rejected | No expected events, elapsed-time bound or expiration for non-History plays |
| `uq_arcade_pack_attempt(pack_id, attempt_number)`; pack attempts 0–3 | Paid attempt cannot be consumed twice under normal locked transactions | Unfinished non-History play reuse is application logic under profile lock, not a DB partial unique constraint |
| `uq_arcade_play_session_profile_game_daily_date` | History Mystery one play/day; resume same unfinished quiz | Non-History `daily_play_date` is NULL; uniqueness does not limit those starts |
| `uq_plunge_point_award_profile_event` | Same event cannot be inserted twice; conflict checked | New client event keys accepted without play context; raw rows can grow beyond effective daily XP cap |
| `uq_practice_chart_profile_submission` | Retry with supplied non-null key returns prior chart | BOOK key optional; NULL keys and fresh keys create new charts. Changed payload under same key/source returns prior chart rather than comparing all fields |
| `uq_camp_point_award_profile_key` | Fixed server date/activity source once | Repeating another allowed activity/day remains possible by design |
| `uq_daily_trivia_attempt_profile_date` | First answer retained, including wrong answer | Retry returns first result rather than allowing guess changes |
| `uq_quest_completion_profile_date` | Both Bonus endpoints share one daily completion | Prevents combining endpoints for two same-day completion grants; does not verify practice |
| `uq_reward_grant_profile_source_type` | Same source/type grant once | Source correctness is caller responsibility; no generic amount/provenance verification in DB |
| `uq_crown_award_profile_source`; `uq_crown_progress_profile_category` | Crown source/progress uniqueness | Unverified earning records still qualify |
| `uq_owned_item_copy_profile_acquisition` | One Mum acquisition/week key | Purchases have NULL key, intentionally allowing multiple charged copies |
| `uq_contest_result_week_contest_division_subject`; `uq_director_contest_result_team` | One finalized result per subject/division/contest | They do not validate underlying practice/activity |

Evidence: `app/models.py:196,259,696,887,918,966–1140,1502,1631,1652,1704`. Migration evidence: `4d9fb7211ac8` (chart keys), `d3e4f5a6b7c8` (Plunge keys), `c4d5e6f7a8b9` (plays), `j0e1f2a3b4c5` (daily History), `c15arcade001` (packs/start keys), `0c6d66da9ea3` (Camp), `d91f6a7b2c40` (trivia), `f4c7b19a2e60` (quest), `8a899a61d621` (contest/grants), `e4f5a6b7c8d9` (owned copies), `f5a6b7c8d9e0` (crowns). This is repository schema evidence, not verification of a production migration state.

Refresh/shared-client retry uses the same IDs/tokens and is safe for existing completion contracts. Refresh/new IDs in practices or invented XP events is a **new accepted assertion**, not a failure of the same-key uniqueness rule. Purchase retries create another paid copy; there is no purchase request ID, but also no free-item manufacture from retry alone.

History's server-owned daily question list, signed progress JSON and answer order provide actual result integrity. Its signature key is server-only and prevents importing forged pre-release JSON. No recommendation here requires a JavaScript secret. Arcade tokens provide account/game authorization and one-use accounting; they are not answer/result attestation. BOOK/Pristine keys and Plunge browser event keys provide only deduplication. The Bonus instance is a predictable date/instrument/challenge identifier, not an authorization secret or proof of elapsed practice.

Within an Arcade completion, balance, score and completion are one transaction; route errors/mismatched game roll back. History final answer plus payout commit together. Quest completion/grant/points/state commit together and recover duplicate completion after rollback. BOOK chart/grant/state commit together; review email delivery happens afterward and cannot undo an already accepted chart. Contest job finalization uses one week transaction and rolls back that week's partial results/rewards on failure. Database uniqueness protects duplicate rows even where application checks race. PostgreSQL lock behavior requires PostgreSQL tests; SQLite passes cannot certify it. No partial-grant exploit was demonstrated by this audit.

# Authentication / Guest Boundary

All student result/economy APIs call `current_profile(request, session)` (`app/account_routes.py:113–155`): signed session identity, active profile, matching current integer session version, and age/privacy eligibility. Optional `X-Woodshed-Account` must match the authenticated account; it cannot authenticate a caller. Revocable session middleware also checks server-retired browser nonces and absolute age (`app/session_revocations.py`). Session identity is never taken from result JSON or localStorage.

Arcade start checks server membership: Plunge/Blue free, normal games free for actual Full Access or 100 dandelions/3 attempts. Completion validates the already-authorized account-owned token; it does not charge again or recheck Full Access for that granted attempt. Classroom authorization always returns false and its keys cannot enter the playable allowlist. `/practice/skill-building` only renders the locked list. No playable Premium bonus-game endpoint was found; `app/feature_access.py` marks bonus game/advanced exercise/seasonal side features disabled.

Eligible private children can earn private rewards where permitted; protected-child BOOK writes enforce sharing restrictions, and family practice explicitly disables contest participation. Public leaderboards require `can_publish` plus attempt start/end timestamps after `public_from` (`app/arcade_scores.py:9–24`). Age/consent is enforced at entry; it is not proof that the submitted outcome is true.

Adult review requires the assigned current verifier, accepted relationship, pending request and applicable director/private consent. Family forms also require CSRF. Contest finalization requires a configured admin token or site-admin session; director routes require the server capability and contest ownership. These roles cannot be obtained through account-state flags. Their safe authorization does not cleanse forged student source records.

Guest flags, Guest URLs and localStorage do not create an authenticated account. The in-memory proof rejected unauthenticated Plunge writes with 401 and other-account completion with 404. Existing tests cover additional Guest/account-switch/session cases; limitations are listed below. No cross-account or account-compromise finding was demonstrated. This audit does not assert production session configuration has been verified.

# Leaderboard / Competition Impact

- **Arcade Top 5 is server persistence**, not merely device-local scores. `ArcadeHighScore`, profile Plunge best and completed attempt rows feed authenticated shared lists (`app/arcade_scores.py:63–153`; `app/xp.py:62–139`). Owner sees private lifetime best; others see publishable attempt best. The proof checked a second eligible account, not just the submitting account's UI.
- Plunge appears in BOARD's Burrow panel through `/xp/plunge-best`. No direct dependency from Arcade bests or Plunge event rows to weekly `ContestResult`, medals or Camp Points was found. Forged Arcade currency can still buy permanent store inventory.
- BOOK/Pristine chart rows feed Open, instrument and eligible team totals; Pristine division simply selects `source == "pristine"` (`app/contests.py:949–981,1920–1963`). Verified division additionally requires adult approval by deadline. Forging a student chart alone does not grant Verified status.
- BOARD hours/care/marching rows feed weekly/season Camp standings, team attribution where membership exists, XP and activity crowns every ten earning dates (`app/contests.py:880–931,2629–2690`). Bonus gives two points with **no team attribution**; it still feeds individual activity standings and XP. Placement points are distinguished from direct activity points by contest logic.
- Contest finalizers compute scores/ranks from these rows and issue durable results/medals and 50/25/15 currency, 3 placement points and gold crown progress. Thus upstream forgery can become Hall/history/crown/traveling-cup outcomes after normal authorized finalization. The audit traced these effects in code; it did not accelerate a production contest or simulate years of rewards.
- Director contest standings use stored eligible chart time, with metric-specific member caps, and finalize saved team results/Hall output (`app/director_dashboard.py:334–431`; `app/contests.py:2520–2570`). They inherit chart integrity, even though normal students cannot finalize the contest.
- Browser-local old `bandCamp` totals/winners, saved practice display records, Plunge cached best and entry animations are mutable presentation. Their mutation alone does not mint table-backed rewards. Rhythm Baseball's “TOP 5” is explicitly coming-soon presentation.

# Existing Protections

The existing economy repair is real: server-owned balances, server catalog prices, fixed reward tables, monotonic best storage, account ownership, current session/age gates, privacy-aware publication, transactional grants, database unique keys, locked rows and revision conflict handling substantially constrain attacks. The current History Mystery ordered answer protocol closes score-only and generic-state forgery for that game. Trivia records both correct and incorrect first attempts. Plunge point type/amount matching and a server-time daily cap prevent arbitrary XP amounts/timestamps. Disabled legacy score import prevents local cached history from becoming a new public result. None of these protections establishes the unobserved activity in F01–F06.

# Findings

## SEC003-F01

**Severity:** P1. **Affected:** Plunge, Blue, Radio Tuner, Wheel, Scale Keyboard, Thirds, Dressed to the Nines, Interval Basic Training; both completion routes.

**Attack precondition:** eligible signed-in account; normal paid games need a valid paid/free entitlement attempt. **Client controls:** final score and fresh start IDs. **Server verifies:** owned play, bounds, game alias binding, daily cap, replay, server-selected payout. **Persistent effect:** arbitrary best/shared ranking; max 5 currency per completion, ten rewarded completions/game/day. Plunge and Blue permit 100 combined free forged dandelions/day. Normal paid entry may exceed rewards; that does not protect their shared scores, and entitled accounts can play free.

**Evidence:** `app/arcade_routes.py:42–60,137–243`; `app/arcade_rewards.py:217–351,362–481`; `app/arcade_scores.py:63–153`; local acceptance proof for all eight; existing `tests/test_security_authorization.py:92–108` explicitly characterizes Blue forgery.

**Smallest safe repair:** first disable authoritative payout/public publication from unchecked scores while keeping gameplay/local scores available. Retain existing token/pack/idempotency layer. Move answer checking for Scale/Thirds/Nines/Interval/Wheel into small server-owned challenge sessions; score only accepted events, enforce order/duration/question limits and one finalization. For movement/timing games, validate a bounded event stream against server-owned run state/seed before publishing results; if that work is deferred, keep their rewards/public rankings disabled. Per-game bounds and elapsed time reject absurd submissions but **alone do not make invented in-range scores legitimate**. Capped participation rewards may be an explicit low-stakes product policy, but must not masquerade as verified scores or feed competitive ranks.

## SEC003-F02

**Severity:** P1. **Affected:** `POST /xp/plunge-points`. **Attack precondition:** eligible signed-in account only; no Arcade play needed. **Client controls:** event identity/type. **Server verifies:** points match type, server timestamp, key uniqueness, effective 10/day XP cap. **Persistent effect:** fabricated event ledger and up to ten unearned lifetime XP each Central day; no direct currency or medal grant on this endpoint.

**Evidence:** `app/xp.py:141–215`; `app/xp_routes.py:20–26,44–72`; `static/js/plunge-burrow.js:464–486`; local no-play event proof.

**Smallest safe repair:** stop accepting standalone earning events; bind earning to the validated Plunge play outcome/event state from F01, choose points server-side, and use a server play/event ordinal uniqueness key. Preserve cap and retries. A token alone plus the existing caller-chosen `band_complete` would still be forgeable. If validated movement is deferred, disable this XP source or explicitly replace it with a bounded server-timed participation policy that makes no score claim.

## SEC003-F03

**Severity:** P1. **Affected:** BOOK `/practice-charts`, private `/family/practice`, downstream XP/Open/team/contest rewards. **Attack precondition:** eligible student account. **Client controls:** minutes, practice date, details, optional/fresh submission key; public contest flags. **Server verifies:** per-row bounds, details, reviewer relationship if requested, server currency formula, 75 per submitted date, key replay. **Persistent effect:** arbitrary lifetime chart XP (minutes + one XP per positive BOOK chart), currency across chosen dates, streak/history and public competition credit where allowed; family path forces contests off but still grants XP/currency.

**Evidence:** `app/practice_charts.py:95–163,191–273`; `app/xp.py:218–242`; `app/family_routes.py:178–190`; local today/1900 BOOK proof: 150 total dandelions and 2,882 XP from two assertions. There is no date range check beyond a valid date or aggregate minutes/day limit. Optional NULL keys do not deduplicate.

**Smallest safe repair:** retain self-reported logs as logs, but separate them from automatic spendable/competitive earning until qualified. Require a submission key, a narrowly allowed practice-date window, no future earning, server earning-day caps, and a single shared account/day duration/reward budget across BOOK/family/Pristine. For meaningful competitive rewards use authorized verification or a server-validated session, not a user-editable log. Caps alone limit abuse but do not prove practice; under this audit's strict P1 standard they cannot justify continuing fully competitive rewards for unverified assertions. Do not erase legitimate historical logs as part of a prospective repair.

## SEC003-F04

**Severity:** P1. **Affected:** `/practice-charts/pristine` and its automatic Pristine/Open/team classification. **Attack precondition:** eligible signed-in account; microphone/browser detector not required. **Client controls:** `detected_playing_seconds`, submission key, contest flags. **Server verifies:** 1–86400 strict integer, today, unique key, consistent derived minutes. **Persistent effect:** arbitrary repeat time/XP and competition entries with `source="pristine"`; no immediate dandelions, but downstream contest placements grant rewards.

**Evidence:** `static/js/pristine-practice.js:200–225`; `app/practice_chart_routes.py:142–146,469–514`; `app/practice_charts.py:118–133,322–350`; `app/contests.py:980,1934–1963`. Two immediate 86,400-second requests under different keys both succeeded locally.

**Smallest safe repair:** exclude unvalidated Pristine assertions from authoritative earning/Pristine competitive status immediately. Add a one-use server practice session with start/finish and capped elapsed time, overlap prevention, expiry, idempotency and shared daily duration budgets. This proves at most elapsed participation, **not microphone-detected playing**. Keep detection local/private or require an appropriate independent verifier before treating it as verified competitive practice. No microphone upload or heavy anti-cheat system is required for containment.

## SEC003-F05

**Severity:** P1. **Affected:** BOARD `hours`, `care`, `marching` at `/contests/camp-points/awards`. **Attack precondition:** eligible signed-in account. **Client controls:** activity type/today assertion. **Server verifies:** whitelist, calendar, dedup, fixed amount/team. **Persistent effect:** up to three unearned points/XP and dandelions/day from these types, team/individual contest credit and activity crown progress. Ten different earning dates qualify an activity crown; same-day repeated requests do not.

**Evidence:** `app/contests.py:880–931,3339–3347,3428–3483,2629–2690`; `static/js/app.js:2544`; local requests for all three succeeded without activity evidence. Trivia's alias correctly requires a saved correct attempt and is not part of this finding.

**Smallest safe repair:** make unverified activity check-ins private/self-reported and remove them from competitive/redeemable reward inputs, or require an independently authorized activity record/reviewer for earning. Preserve current one/day fixed-key bookkeeping. Do not add only a “completed” flag or a nonce: either would still be freely assertable.

## SEC003-F06

**Severity:** P1. **Affected:** current Bonus progress and legacy quest completion. **Attack precondition:** eligible signed-in account; read current challenge (or choose a configured legacy quest). **Client controls:** submission occurrence, today/instance or claimed logged minutes. **Server verifies:** configured challenge, date, fixed amounts, one daily completion; legacy lower bound. **Persistent effect:** 5 dandelions + 2 points/XP, completion and streak without practice; individual Camp standing contribution, no team credit. Current path even fills target minutes when none were sent.

**Evidence:** `app/contests.py:2811–2861,2966–3087,3128–3208`; `static/js/app.js:1335–1344`. Local current-instance POST with only date/instance returned completed=true, logged_minutes=10, +5/+2. Shared date uniqueness prevents double payment across the two routes.

**Smallest safe repair:** use a server-owned assignment and qualifying practice/challenge evidence; derive completion from that evidence, reuse daily uniqueness, and remove or route the legacy endpoint through exactly the same acceptance service. Arbitrary `daily` state must not choose trusted assignments/progress. Until evidence exists, keep “I Played It” as a self-report without spendable/competitive credit. A cosmetic completion can remain available.

## SEC003-F07

**Severity:** P3. **Affected:** regression/concurrency evidence for audited routes. **Attack precondition:** none demonstrated. **Client controls / server verifies / persistent effect:** not an additional exploit; existing tests cover important bounds and replay, but most unchecked games lack rejection tests for in-range fabricated results, Plunge lacks a required-play test, and practice tests often assert acceptance of caller-supplied time. Several legacy fixtures do not fully model current age/session boundaries. PostgreSQL checks require an explicitly disposable test database.

**Evidence:** test inventory and exact results below; `tests/test_security_authorization.py:92` intentionally asserts the vulnerability; `tests/test_release3_arcade.py:287` and `tests/test_history_score_integrity.py:195–263` require PostgreSQL for races.

**Smallest safe repair:** after authority changes, convert characterization into rejection tests for each alias and source; test fresh-key forgery as well as identical replay, account changes, expiry, impossible timing/answer combinations, caps across paths/dates, rollback and PostgreSQL concurrent completion/earning. Repair stale fixtures without relaxing eligibility/revocation. No test/application changes were made during this audit.

# Test Evidence

Two broad existing-test batches completed: **383 passed, 77 failed, 124 skipped** (584 collected cases). The suite is **not clean**. Tests ran with a fresh temporary virtual environment, `PYTHONDONTWRITEBYTECODE=1`, pytest cache disabled, `DATABASE_URL=sqlite://`, a synthetic session secret, and a cleared inherited environment. Fixtures used in-memory or temporary local databases. No production URL or real credentials were loaded; PostgreSQL environment variables were deliberately absent. Temporary tooling/logs lived under `/tmp`; this report is the only repository output.

## Existing-test commands and results

Commands used the common prefix below, followed by each file list. Separate logs were written to `/tmp/sec003-existing-tests.log`, `/tmp/sec003-game-tests.log`, and `/tmp/sec003-focused-tests.log` (temporary diagnostic artifacts, not committed deliverables).

```sh
env -i PATH=/tmp/sec003-audit-venv/bin:/usr/bin:/bin \
  PYTHONDONTWRITEBYTECODE=1 DATABASE_URL=sqlite:// \
  SESSION_SECRET=sec003-local-audit-only \
  /tmp/sec003-audit-venv/bin/python -m pytest -q -p no:cacheprovider
```

Batch A file arguments:

```text
tests/test_arcade_economy.py tests/test_release3_arcade.py
tests/test_history_score_integrity.py tests/test_security_authorization.py
tests/test_xp.py tests/test_server_economy.py tests/test_guest_boundary.py
tests/test_session_revocation.py tests/test_quest_completion_persistence.py
tests/test_pristine_practice.py tests/test_login_streaks.py tests/test_plunge_best.py
tests/test_store_inventory.py tests/test_contests.py tests/test_contest_jobs.py
tests/test_age_screening.py tests/test_private_practice.py
```

Result: **258 passed, 46 failed, 124 skipped**, 304.01 seconds. Failure counts by module: XP 10; Guest boundary 2; quest completion 9; Pristine 3; login streaks 7; Plunge best 4; store inventory 10; contests 1.

Batch B file arguments:

```text
tests/test_arcade.py tests/test_plunge_burrow.py tests/test_scale_keyboard.py
tests/test_thirds.py tests/test_dressed_to_the_nines.py
tests/test_interval_basic_training.py tests/test_wheel_of_woodchuck.py
tests/test_history_mystery.py tests/test_session_hardening.py
tests/test_batch_b_contests_and_crowns.py tests/test_director_dashboard.py
```

Result: **125 passed, 31 failed**, 108.56 seconds. Failure counts: Plunge client 1; Scale 3; Thirds 6; Nines 5; Interval 6; Wheel 4; History client 1; session hardening 1; director dashboard 4.

Failure assessment (no fixes applied):

- Older fixtures patch route session factories but omit `session_revocations.SessionLocal`. With the safe fallback `sqlite://` and no global tables, middleware fails closed with 503 before the requested result handler. Representative evidence: `tests/test_xp.py:35–36`, `tests/test_store_inventory.py:40–42`, `app/session_revocations.py:155–171`. Those failed assertions cannot be reported as successful result-route coverage. The separate acceptance proof redirects **all** loaded factories and uses age-eligible synthetic accounts.
- Quest/login/store fixtures also omit age eligibility; they fail with 403/AgeScreenRequired or missing login reward state. Representative: `tests/test_quest_completion_persistence.py:40–67`, `tests/test_login_streaks.py:73`, `app/login_streaks.py:175`. These failures do not demonstrate an authorization bypass.
- Two Guest cases send `/arcade/plays` without required `request_id`, receive schema 422 rather than expected 401, and grant no play/reward. This is an outdated error-order expectation, not a Guest earning exploit.
- Several older game tests expect one-dandelion entry pricing, old soundtrack cache versions or old markup; baseline now uses 100/3 attempt packs and soundtrack v9. Examples: `test_scale_keyboard.py:103,118`, `test_thirds.py:273`, `test_interval_basic_training.py:271`. Newer release3 tests exercise the current economy contract.
- Plunge aggregate-only leaderboard tests conflict with current publishable-attempt provenance. Pristine/director/historical-Hall assertions also include empty/changed public projections; these remaining behavior/fixture mismatches were **not all independently resolved**. They remain failures to reconcile before release, not evidence that every underlying protection passed.
- `test_session_hardening.py::test_production_limiter_off_allows_https_login_without_backend` timed out in its isolated local subprocess after 30 seconds. No production request was made. Its cause is not established here.
- PostgreSQL-only/parameterized cases were skipped because no explicit disposable PostgreSQL URL was configured; SQLite-only branches also intentionally skip lock-race cases. Age/private suites requiring that database were not dynamically certified. No claim of PostgreSQL concurrent integrity or production schema status is made.

A focused rerun of `test_arcade_economy.py`, `test_release3_arcade.py`, `test_history_score_integrity.py`, `test_security_authorization.py`, `test_server_economy.py`, and `test_session_revocation.py` isolates the current authority/security contracts from the older failures. Result: **115 passed, 59 skipped, 0 failed**, 38.82 seconds. These include the Blue forgery characterization, History score/state/answer rejection, current pack and replay policy, protected economy sync, and session revocation tests. It repeats selected tests; it must not be added to the 584 unique broad-batch cases above.

## Minimal local acceptance proof

An inline Python `TestClient` harness loaded the application, created all tables in in-memory SQLite using `StaticPool`, redirected every loaded application's `SessionLocal` to that factory, and created two synthetic adult profiles. It then used normal login and endpoint requests. Synthetic starting balances were reset between paid-game cases solely to test each route independently; those resets are not part of an exploit.

| Check | Observed result |
|---|---|
| `/xp/plunge-points` with `{event_key:"sec003-invented-band",event_type:"band_complete",points_scored:20}` before any start | 200, created=true; `/xp` Plunge contribution=10; play-row count=0 |
| Same event again / wrong points / unauthenticated | created=false / 400 / 401 |
| Each of eight unchecked games: start, then immediate score 2147483647 | 200; payout=5; score visible to second eligible account |
| Same completed token/score / second account using token | No extra currency, already_completed=true / 404 |
| Legacy `/xp/plunge-best` import for eligible account | 409 |
| Current Bonus POST with date + instance only | Completed, target minutes filled, +5 dandelions/+2 points |
| BOARD hours/care/marching without activity evidence | Each 200, each one saved point; fixed dandelion grant follows same transaction |
| BOOK 1440 minutes, credits_awarded=0, today + 1900-01-01 | Each 201, each 75 currency; combined XP delta 2882 |
| Two Pristine 86400-second requests with distinct keys | Both 201, each 1440 minutes; additional XP delta 2880 |

All assertions in this harness passed. No sustained farming, high-volume fuzzing or finalization against live users was performed.

## Coverage inventory

| Required concern | Existing evidence / limitation |
|---|---|
| Forged/absurd scores | Blue characterization in `test_security_authorization`; History score/alias/state rejection in `test_history_score_integrity`; audit proof covers all eight unchecked games. Generic integer maximum is not a game-specific plausibility bound. |
| Negative/invalid/upper bounds | `test_arcade.py:616`, `test_xp.py:409–452`, `test_plunge_best.py`, Pristine input boundary tests; negative points/type mismatch and extra timestamp rejected. |
| Repeat result / duplicate ID / retry after grant | `test_arcade_economy` completion/replay/start tests; `test_release3_arcade` request IDs/pack attempts; `test_xp` event duplicates/conflicts; quest/trivia/BOOK daily/key tests. |
| Concurrent identical requests | PostgreSQL `test_release3_arcade.py:287`, History start/last-answer/conflicting-answer tests; `test_server_economy` locks/caps/reward/purchase races. Not established by SQLite sequential tests. No dedicated concurrent Plunge-event HTTP test was located. |
| Unauthenticated / wrong account / session | Arcade token/game ownership tests, `test_guest_boundary`, `test_session_revocation`, `test_session_hardening`; separate proof rejects no-login XP and wrong-account game completion. |
| Guest | `test_guest_boundary.py` flags/stale page identity tests and Guest no-persistence checks. Guest UI checks supplement, rather than replace, API authentication. |
| Reward bounds | Arcade payout table/daily cap, XP cap/Central midnight, BOOK 75/date and login/day tests. They do not reject a new fake qualifying activity. |
| Transaction rollback | `test_release3_arcade.py:98`, `test_quest_completion_persistence.py:430`, `test_contests.py:1611`, `test_contest_jobs.py:283`; mismatched-game alias rollback tests. |
| Entitlements/privacy | Release3 free/Full/pack/Classroom policy; publication-before/after timestamps; age/private suites require their configured PostgreSQL fixture. |

# October Beta Impact

**Full intended October beta should remain blocked on SEC-003.** F01–F06 are unresolved P1 persistent earning/competition forgery under the supplied standard. Existing caps make some attacks bounded; they do not make them legitimate or reduce them to local-only P2 issues. Manual check-ins/practice logs may be intentional product features, but their current promotion into spendable rewards and competitive outputs is the trust boundary requiring repair or explicit removal from the intended launch scope.

A reduced local/private practice experience can retain unverified gameplay and self-reported logs with affected earning/public competition disabled. That would be containment, not verification of the full intended beta. History Mystery, trivia, authorized daily/weekly claims and protected economy sync need no broad redesign. Preserve them while repairing the unverified inputs.

# Recommended Repair Plan

1. **Contain unverified earning and publication at the shared server acceptance points.** Prioritize BOOK/family/Pristine (unbounded lifetime XP and date-based currency) and shared Arcade completion (eight public rankings plus free currency), including all aliases. Keep logs/gameplay accessible where feasible; do not promote new unchecked rows into contest inputs. Inventory already-stored affected records for a separate, reviewable remediation decision; do not erase data automatically.
2. **Close standalone Plunge XP.** Require validated play-derived evidence, not arbitrary event IDs; preserve 10/day and event uniqueness. Coordinate with the Plunge result repair so one stream cannot bypass the other.
3. **Reuse History's small challenge pattern for answer-based games.** Server assignment, expected sequence, valid answers, deterministic score, bounded session time, one accepted result. Keep shared token/pack/retry logic and add only necessary state.
4. **Separate practice/check-in claims from earning evidence.** Add shared server-owned sessions/budgets and date limits where elapsed participation is enough; require independent review for claims that must establish actual practice. Repair BOARD and both Bonus endpoints; retire duplicate legacy acceptance logic. Capped/idempotent participation can be sufficient for an explicitly noncompetitive low-stakes reward, but a cap alone does not cure the current competitive forgery.
5. **Verify downstream qualification.** Only eligible source records may enter Open/Pristine/team/instrument/director totals and crown reconciliation. Do not assume a row in a server table was server-verified. Preserve Verified adult review and current privacy boundaries.
6. **Run focused rejection and PostgreSQL race/rollback tests.** Cover all 36 inventory rows where applicable, every write alias, fresh-key/NULL-key variants, actual server calendar boundaries and old stored state. Re-audit before marking verified; no JavaScript secrets, blanket game rewrite or heavyweight anti-cheat is required.

The top implementation task is a server-side qualification/containment change covering practice earning and Arcade completion/publication, with explicit regression cases from this report. **Source changes should begin next in a separately authorized repair pass; none are included here.**

# Master Control Update

```text
Status: SEC-003 REPAIR REQUIRED
Blocker: YES — six P1 findings: eight client-scored Arcade results; standalone Plunge XP; BOOK/private practice earning; Pristine time/competition assertions; BOARD hours/care/marching credit; both Bonus completion paths.
Evidence: docs/sec-003-server-authority-audit-20260925.md; baseline fb2d2270f966d0f5cd594c83accf2efa83e2b75c. Local synthetic proofs accepted fabricated results/rewards; 36 matrix rows audited. Broad local tests: 383 passed / 77 failed / 124 skipped; focused security rerun: 115 passed / 59 skipped / 0 failed; synthetic acceptance proof passed. No production activity or application code changes.
Next Action: Begin a separate minimal server-authority repair pass, containing unverified practice earning and Arcade payouts/public results first, then close Plunge XP and BOARD/Bonus evidence gaps.
Exit Criterion: F01–F06 cannot manufacture persistent earning or competitive results through any alias, fresh key or replay; validated qualification and atomicity tested locally including disposable PostgreSQL races; regression suite failures reconciled; report re-audited as SEC-003 VERIFIED before full intended beta.
Last Updated: 2026-09-25
```
