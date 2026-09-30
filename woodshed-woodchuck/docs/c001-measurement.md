# C001 measurement machine map

Program: **PRE-BETA**. Cohort: **C001**. Verified against base
`cbeb6838c5e6366e2a3d9ab6e7999277fbbe3b60` and this attribution repair.
These definitions use persisted evidence. They do not add telemetry, change
credit/reward rules, or rewrite enrollment history.

## Joined v1 — VERIFIED, with a timestamp qualification

- **Table:** `tester_enrollments` (`TesterEnrollment`).
- **Condition:** one existing row with `cohort_key = 'C001'`, identified by
  `profile_id`; unique `(profile_id, cohort_key)` prevents duplicate membership.
- **Timestamp:** stored `joined_at`, interpreted as UTC before converting to
  `America/Chicago` for calendar calculations.

For 13+ registration, authorized account creation and enrollment are committed
together; `joined_at` is the registration clock. Guest use, opening a link, and
entering a Secret Symbol create registration context, not an enrollment.

For under-13 entry, `child_pending_consents.cohort_key/cohort_source/cohort_claimed_at`
retain the claim. Starting the parent request and successful parent approval
alone do not create enrollment. Successful parent-authorized account activation
creates it. **Its `joined_at` preserves the earlier parent-request claim time**;
it is not the activation/insert time. Thus a pending request contributes zero
Joined rows, but a later activated row has the original claim timestamp. There
is no enrollment-created-at column from which to recover its insertion time.

`enroll_tester()` returns an existing row without changing source or time.
Duplicate actions and account reauthorization retain that history. Deletion
anonymizes/deactivates a profile and retains enrollment; current access is still
age/status gated. A cumulative historical Joined count must retain such rows.
The existing dashboard's **Enrolled active testers** count filters active
profiles and is a different population. Physical profile deletion would cascade
its enrollment; it is not the current deletion workflow.

**OPEN:** enrollment does not independently certify that an account belongs to
a genuine external student rather than staff/test activity. Confirm the approved
external-student population operationally; no route-based classification or
historical provenance rewrite is authorized here.

## Source — VERIFIED

- **Table:** `tester_enrollments`.
- **Field:** `source`, read verbatim for the stored C001 enrollment.
- Director #1 display QR/link: `/prebeta/C001?entry=director1` establishes the
  server-owned `C001 / DIRECTOR1` signed registration context.
- Secret Symbol C001 entry establishes the same context.
- Bare `/prebeta/C001` establishes C001 without inventing a Source. Later bare
  navigation retains a previously established DIRECTOR1 claim.

Only fixed `entry=director1` and existing `entry=secret-symbol` markers map to
the fixed DIRECTOR1 constant. Query `source`, account form fields, later routes,
and reporting do not establish or
reconstruct Source. Registration and approved child activation persist the
validated claim. Existing enrollment wins over a later claim, including legacy
or missing Source. The cohort report now reads stored `joined_at/source`; it
does not infer them from profile creation or navigation.

## Product Activation v1 — VERIFIED from current qualification

- **Tables:** `practice_charts`, `practice_chart_verifications`, and the C001
  `tester_enrollments` row.
- **Qualification:** positive persisted duration (`chart_seconds_sql() > 0`)
  and the existing `qualified_practice_clause()`: chart `source = 'p-book'`, with
  an `approved` verification having non-null `responded_at`. For an as-of
  snapshot, both chart creation and qualifying approval must be at/before as-of.
- **Timestamp:** `practice_charts.created_at`, the server submission timestamp;
  require **strictly greater than** the student's stored C001 `joined_at`.
- **Selection:** first qualifying submission per `profile_id`, ordered by
  `(created_at, id)`; one Product Activation v1 per student.

`xp_sources()` credits these qualifying rows as practice minutes and P-Charts.
XP is calculated from durable evidence, not a separate XP-event table.
`credits_awarded > 0` or a dandelion `reward_grants` row is not required by the
current chart-credit rule: an approved small chart or a capped earning day can
legitimately award zero currency while still qualifying for chart/XP credit.
Conversely, legacy currency alone cannot qualify an unapproved chart.

An approval can arrive after submission: qualification becomes knowable at
`practice_chart_verifications.responded_at`, but Leadership's submitted-chart
event retains `created_at`. A read today can therefore retrospectively qualify
an earlier submission. This is not an assertion that it was credited at the
submission instant. Submission keys are unique per profile and retries return
the same chart; repeated review is rejected. Multiple qualifying reviews cannot
multiply a chart when using the existing EXISTS qualification.

Unqualified BOOK self-reports, pending/rejected/missing-response reviews, and
browser `pristine` microphone logs do not satisfy Product Activation v1.

## Return v1 — qualifying durable source map

First establish Product Activation above. A qualifying event's Central date
must be **later than** the activation's Central date; elapsed 24 hours is not
the rule. Deduplicate by `(profile_id, Central date)` across source categories;
the first qualifying later day supplies one Return v1 per student. Retain other
qualifying days for retention. Login grants, analytics entry observations,
page loads, navigation, purchases, and automatic contest placements are not
Return evidence.

| Category | Status | Table / qualification | Timestamp |
| --- | --- | --- | --- |
| Recorded practice | VERIFIED shared qualified subset; OPEN separate practice authority | `practice_charts` with positive duration and `qualified_practice_clause()` | `created_at` |
| Credited Practice Chart | VERIFIED | Same independently approved positive-duration BOOK rows as Product Activation | `created_at`; approval must exist by as-of |
| Arcade/game | VERIFIED supported subset; OPEN unqualified or stale completions | `arcade_play_sessions`: `completed_at` and `authoritative_score` non-null. History Mystery completion follows all five server-validated answers, including a zero score. Timed challenge games require server-owned `challenge_state.version = 1`, `index > 0`, and valid `last_elapsed` proving an accepted action on the completion's Central date (details below). | `completed_at` for the supported same-action-day subset |
| BOARD daily attestations | VERIFIED | `camp_point_awards` under `qualified_camp_point_clause()`, restricted to `hours/care/marching`: 1 point and `board-self-report-v2:YYYY-MM-DD:{activity}`; or `quest`: 2 points and `bonus-challenge:self-report-v2:YYYY-MM-DD` | `occurred_at` |
| BOARD trivia answer | VERIFIED | `daily_trivia_attempts`, persisted server-validated daily answer attempt (correct or incorrect), unique profile/date | `created_at` |

There is no separate independently qualified practice-session ledger: the safe
recorded-practice and credited-chart sources currently collapse to the same BOOK
chart evidence. Private BOOK/Pristine logs preserve reported duration but do not
prove independently credited practice. Counting them as a separate qualifying
Return category remains **OPEN**; this pass does not promote them.

Current server-scored timed games are `thirds`, `dressed-to-the-nines`,
`interval-basic-training`, and `scale-keyboard`. An untouched timer can be
sealed with score zero, so authority/completed fields alone do not prove
meaningful interaction; the retained accepted-action index supplies that
evidence. Also, starting a later game can automatically close an older expired
run: its new `completed_at` alone does not prove interaction that day. For the
verified timed-game subset, reconstruct the retained last-action instant as
`UTC(started_at) + seconds(challenge_state.last_elapsed)` and require its Central
date to equal the completion's Central date. `last_elapsed` must be a finite
numeric value greater than zero and at most the server's
`RUN_SECONDS + ACTION_GRACE_SECONDS` (currently 32 seconds), with the reconstructed
instant no later than completion. Missing/invalid timing evidence is excluded.
Thus yesterday's actions auto-sealed by today's game start do not count a Return
today. Cross-midnight runs whose last action and eventual completion have
different Central dates, and completions lacking sufficient persisted timing,
remain **OPEN** outside this supported subset. Game start alone, old attempts
closed without verified actions, and completed caller-scored games are excluded
from the verified subset. Meaningful
completion authority for `plunge-burrow`, `blue`, `radio-tuner`, and
`wheel-of-woodchuck`, and unavailable legacy proof, remains **OPEN**.

INC002 v2 BOARD keys are issued by server actions, not accepted from callers.
They certify an intended deliberate self-attestation, not independently
observed real-world practice. For both daily and Bonus rows, `occurred_at` is
the server's action clock, not an invented midnight/practice time; a supplied
activity date must equal the current Central day. `created_at` is the row-insert
clock and is not the earning timestamp used by BOARD. Bonus completion is in
`camp_point_awards`; it does not create `quest_completions` or fabricate practice
minutes. Legacy
`band-camp` hours/care/marching and old `bonus-challenge` quest rows do not
qualify merely because their activity type matches. Trivia uses the answer
ledger rather than treating all historical trivia awards as proof of an answer.
Placement awards may be qualified for economy purposes but are automatic and
excluded from Return. Unique profile/key or profile/date constraints and
transactional retries retain the same evidence; read-only deduplication avoids
multiple Return events from several qualifying sources on one day.

## Retention v1 — VERIFIED calculation for supported sources

For each Product-Activated student, compute
`qualifying_activity_Central_date - activation_Central_date`. Retention v1 is
true when any Return-qualifying day has offset **6, 7, or 8**. Day 0, day 5, and
day 9 do not count. This uses Central calendar dates, including DST boundaries,
and is not an elapsed-hours window. It is calculable from the verified sources
above; unresolved practice/game categories limit coverage, not the frozen rule.

The existing 30-day analytics dashboard's `day1_active`,
`returned_after_day1`, `returning`, and broad activity counts are observation
metrics, not these Leadership activation/return/retention definitions. The
focused persisted-fixture selectors in `tests/test_c001_measurement.py` verify
this machine map without installing a new reporting subsystem.
