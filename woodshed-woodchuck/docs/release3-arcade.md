# Release 3: Arcade economy and experience

## Baseline and scope

Production-derived base: `6aca60e` (Release 2 / PR #15). The existing
`ArcadePlaySession` ledger, profile/state row locks, authoritative balance,
completion/payout deduplication, daily reward cap, History answer validation,
and age/privacy rules remain the authority. This release does not add billing,
Classroom organizations, educational game content, mobile shells or SHED/SHOP
touch regions. No final artwork is generated.

## Classification and access

| Classification | Games | Access |
| --- | --- | --- |
| Always free | Plunge Burrow; Blue | No Dandelion cost; existing account/age gates remain |
| Normal | Radio Tuner; Wheel of Woodchuck; Scale Keyboard; Thirds; Dressed to the Nines; Interval Basic Training; History Mystery | 100 Dandelions for three attempts, or free with existing personal Full Access |
| Reserved Classroom | Note Names; Instrument Fingerings; Rhythm — Hear & Pick; Key Signatures; Transposition | Locked by default; personal Full Access does not unlock |

`app/arcade_access.py` centralizes classification and uses the existing
`student_has_full_access` entitlement, including qualifying PILOT-D1/C001 testers.
Full Access does not remove age/privacy restrictions. `classroom_authorized` is
the fail-closed R6 capability seam; it always returns false until real Classroom
relationships are implemented. The five reserved games have no playable routes.
Future Classroom authorization makes their policy free, but does not implement
their gameplay. No environment variable or client flag can unlock them.

History Mystery retains one quiz per America/Chicago day, server-owned ordered
answers, five-question scoring and day-end expiry of a *started* daily quiz.
A purchased pack spans days; unused attempts have no expiry. Full Access removes
the cost, not this game-specific daily restriction. All games retain their
existing payout thresholds and reward eligibility for the first ten completed
plays per game/Central day.

## Authoritative purchase and attempts

`ArcadeAttemptPack` records a 100-Dandelion purchase with exactly three slots;
`ArcadePlaySession` records each consumed slot with `(pack_id, attempt_number)`
uniqueness. First deliberate Start purchases the pack and consumes slot 1 in
one transaction; GET/page loads never purchase or consume. Subsequent Starts
consume slots 2/3 without charging. A fourth Start purchases another pack only
if funds suffice. The balance remains in the existing locked state row.

Profile -> state locking serializes purchases across tabs/devices. Failed
transactions roll back the debit, pack, attempt and retry mapping together.
Insufficient funds create none of them. At most one unfinished ordinary run is
resumed per account/game. Recovery does not replenish slots. Paid slots remain
saved while Full Access supplies free runs, and remain usable afterward.

`POST /arcade/plays` now requires a client-generated `request_id`. The browser
retains an unanswered request ID in per-account/game sessionStorage and reuses
it for network retries. `ArcadeStartRequest` durably maps each key to its play,
including simultaneous starts that resume the same play. Even a retry arriving
after completion returns that old play; the UI does not restart a closed play.
Old clients missing this field receive 422 without a charge and must reload.

Existing score/completion routes still require an owned authoritative token.
Same-score replay acknowledges the original result; conflicting scores and
game/token mismatches are rejected without another payout/score/attempt.
History validates actual ordered answers. Other games retain their existing
bounded client-reported scoring; this release does not claim server simulation
or comprehensive anti-cheat for those games.

## Shared standings and privacy

The room already fetched server standings, not local high-score storage.
A reproducible backend defect was using only lifetime-best rows: a private old
best excluded by the publication boundary also hid a lower, eligible new score.
Shared standings now supplement the existing projection with best completed
attempts whose start and completion pass `can_publish`. Plunge receives the same
correction without changing its private-best boundary. Old private scores are
not made public. Unknown-age, under-13/private and deleted identities remain
excluded from other students' standings. The viewer can still see their own
private lifetime best, using the existing personalized projection.

Descending score, name/ID ordering, Olympic ties, five-row Arcade standings,
Plunge's own-row behavior, and History's existing personal-best-only UI remain.
Room request failures now say standings are unavailable instead of masquerading
as an empty ranking. This does not establish that every production report of
missing standings has this cause: genuinely private/legacy scores must stay
hidden. No production data was inspected or republished.

## Artwork delivery contract

Reusable manifest and loader: `static/js/arcade-art.js`; responsive pixel rules:
`static/css/arcade-art.css`; mount: `data-arcade-art="<game-key>"`. Every playable
and reserved game has a manifest entry. Existing sprite art/text serves as a
placeholder; `animated` is null until Artwork delivers reviewed assets.

| Slot | Logical size / ratio | Delivery |
| --- | --- | --- |
| `tile` (required final delivery) | 128 × 128, 1:1 | PNG or lossless WebP |
| `static` (required) | 256 × 256, 1:1 | PNG or lossless WebP; matches animation's composition |
| `animated` (required final delivery) | 256 × 256, 1:1 | Animated WebP or APNG; static is always sufficient to play |
| `entrance`, `transition`, `win` (optional) | 320 × 180, 16:9 | PNG/WebP; animated WebP/APNG if needed |

Use `/static/img/arcade/<game-key>/<slot>.v<revision>.<extension>`. Manifest URLs
must be same-origin, versioned and free of account data. Use nearest-neighbor
export at logical resolution; transparent backgrounds are preferred. Include a
clear silhouette against both light/dark cabinet backgrounds. Do not bake text,
score, price or essential instructions into the image. Existing legacy sprite
fallbacks keep their crop CSS; separate final assets should replace that crop
class along with their manifest entry.

Target 8–12 fps; maximum 15 fps, gentle 2–4 second loops, no rapid flashing.
Budgets: tile <=50 KB, static <=150 KB, animation <=500 KB, optional asset <=750 KB.
The reused shared sprite (~1.3 MB) is an interim legacy placeholder, not the
budget for newly delivered assets.
Use responsive container sizing, `object-fit: contain` for standalone images,
`image-rendering: pixelated`, and intrinsic aspect ratios. No desktop-only width,
hover interaction, or animation-dependent control/meaning is permitted.

Images load lazily and decode asynchronously. Animated sources are selected only
when visible, the document is visible, and reduced motion is off. Media-query
changes/hidden pages swap to static; animation errors fall back to static; a
failed static image leaves the text/icon placeholder. Without IntersectionObserver
the static fallback stays in use. Optional slots are dormant by default and
must never block Start or force a loading/entrance sequence. Future use must
provide a static/reduced-motion equivalent and the same deferred behavior.

## Migration and rollout

`c15arcade001` adds packs, retry mappings and nullable pack/slot links on existing
plays, and permits costs 0/100 while retaining legacy cost-1 rows unchanged.
It grants no retroactive attempts or refunds and alters no balances. Downgrade
refuses when R3 activity exists: attempt packs, R3/non-legacy entry costs
(including free plays), or durable `ArcadeStartRequest` retry mappings, even
when they only reference legacy cost-1 plays. Old code cannot preserve that history.
Use a forward fix after R3 activity. Deploy schema/backend and updated client
assets together; stale clients must reload. Drain old backend workers before
admitting R3 play traffic: mixed old/new workers would apply different prices.
No deployment or configuration
changes are performed as part of implementation.

Tests use isolated SQLite and disposable local PostgreSQL, synthetic profiles,
and captured services. Required fixture updates declare synthetic eligible ages,
send the new start request IDs, and expect the new prices/free-game balances.
Existing soundtrack assertions are adjusted to the current v8 source and named
initializer; no audio behavior is changed. The older enrollment migration test
compares ORM metadata after upgrading to the current head, since R3 adds tables.
The browser fixture now includes completed setup state for its new lobby check;
its wait retries only transient Chromium execution-context loss during navigation.

## Validation (2026-09-23)

| Run | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Combined Arcade/economy, migrations, age, tester, membership, analytics, private-practice, KWS, authorization, logging, state-sync and soundtrack regressions | 446 | 0 | 7 |
| Chromium History recovery + lobby at 390px / 1440px | 2 | 0 | 0 |
| R3 Node client/artwork tests | 4 | 0 | 0 |

The seven skips are SQLite variants requiring row locks; their local PostgreSQL
counterparts ran successfully. Migration upgrade/downgrade and R3 concurrency
checks ran on disposable local PostgreSQL as well as applicable SQLite checks.
The final local-server/browser run used RAM-backed temporary files after disk-backed
startup timeouts. No production database, real email or real KWS call was used.
JavaScript syntax checks and `git diff --check` passed. No commit, staging,
push, merge, deployment or Render configuration change was performed.
