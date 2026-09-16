# Woodshed Woodchuck — Security Round 1 department handoff

September 16, 2026. **Ready for bounded code-only release review; not approved for
release, limiter activation, or broader beta security clearance.** This is a local
engineering handoff, not a legal conclusion. No department has been contacted.

## Candidate and evidence

Worktree: `/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck`.
Branch: `security/round1-server-economy`. Retained commit:
`a4db762da0092bf585493ad4cdacf61c5e872334`.

- **Committed locally:** authoritative synchronization balances and legitimate
  backend awards, stale browser-response safeguards, student/verifier and separate
  site/contest-admin throttling (including blocked-IP quota-poisoning correction),
  production session requirements and cross-user authorization regression coverage.
- **Uncommitted/unstaged:** sanitized application/Uvicorn/browser diagnostics;
  History Mystery server-selected questions, ordered answers, derived scores and
  atomic payouts; matching templates/scripts/tests; cumulative report and this
  handoff. Closeout adds account-switch guards for delayed reward/error messages,
  safe rejection of malformed legacy quiz signatures, and explicit production-mode
  limiter-off/HTTPS login coverage.
- **Deployment:** none performed or verified. Local commit/tests are not deployed
  protection. No schema migration, economics change, disabled game, historical
  balance/score alteration, infrastructure change or paid upgrade in this batch.
- **Prior evidence, separate runs:** 437 passed / 3 failed / 11 skipped integrated
  Python selection; then 35 passed resolving two stale cache assertions and an
  empty disposable default-schema fixture; 59 Node tests passed. Earlier full-suite
  results predate this candidate. Logs still exist; the cumulative report identifies
  exact failures and commands. Existing real PostgreSQL races (3 passed) and mobile/
  desktop Chromium (2 passed) remain applicable to unchanged transaction/game flow.
- **Current closeout:** 152 Python tests passed, 0 failed, 3 PostgreSQL-only skips;
  66 Node tests passed, 0 failed/skipped. Compile/import and syntax checks passed.
  Exact commands are in the cumulative report's final closeout section. New checks use synthetic credentials and isolated SQLite;
  they do not validate Render or provider logs.

## Release-review boundary

No unresolved local defect found in the reviewed changed surface prevents review.
Product/Operations must review the cumulative patch, production base/schema/build
compatibility, known baseline test debt and residual findings below. Before a
separately authorized code release, complete the report's isolated HTTPS/session/
CSRF and normal-workflow smoke matrix; privately verify existing production session
configuration without sharing/rotating its secret.

Proposed independent release keeps `LOGIN_RATE_LIMIT_MODE=off` and
`LOGIN_RATE_LIMIT_REQUIRED=false`; a saved Redis URL must not activate it.
Deploy backend/templates/scripts together (shared Arcade v5; History v2), drain old
workers and require old quiz pages to reload/resume. Review Central-midnight expiry,
legacy unfinished quizzes and retained static-bank automation limits. The existing
proxy settings remain unchanged for this off-mode proposal. Rollback reopens logging/
quiz weaknesses; an interval with old writers requires an explicit unfinished-quiz
reinitialization policy before restoring protection. No historical database restore
or automatic credit adjustment is proposed. See the cumulative release sequence.

## Remaining work and proposed owners

Owners below are proposed handoffs, not notifications or approvals.

| Finding / scope | Priority and proposed owner | Next action and closure evidence |
| --- | --- | --- |
| Eight paid games: Blue, Radio Tuner, Wheel of Woodchuck, Scale Keyboard, Thirds, Dressed to the Nines, Interval Basic Training, Plunge Burrow | **HIGH / BETA BLOCKER**, Product game engineering + Security | Design game-specific server-owned conditions/rules and validated transitions. Both paid completion routes still accept invented scores that earn currency/records; non-History unfinished tokens have no completion expiry. Close with forged-result/replay/ownership/concurrency negatives and legitimate gameplay/browser proofs per game. A token, ceiling or elapsed-time check alone is insufficient. |
| Separate Plunge `/xp/plunge-best` and `/xp/plunge-points` | **HIGH / BETA BLOCKER**, Product game engineering + Security | Tie best scores and XP events to the same validated run. Unpaid arbitrary records and fabricated capped XP remain possible. Prove alternate endpoints cannot bypass validation, preserving rates/caps. |
| Production authentication throttling and proxy trust | **HIGH / BETA BLOCKER**, Product/Operations + Security; Jeff obtains provider contract | Obtain supported Render ingress/forwarding guarantees, then execute the prepared distinct-client/spoofed-header/multiworker and HTTPS/session/CSRF matrix. Do not infer CIDRs from one address. Production enforcement remains unverified; changing proxy behavior is a separate release. |
| Key Value activation prerequisites | **HIGH / BETA BLOCKER for activation**, Product/Operations verifies; Jeff approves expense | User-confirmed service is Available, Free, same region, noeviction; URL saved privately using “Save only.” **Unchecked: upgrade to $10/month paid plan before activation.** Verify connectivity AND shared-counter enforcement, expiry, blocked-IP non-poisoning and required-backend 503 behavior. Then separately authorize redis/required mode. PING or configured status is not closure. |
| Provider-controlled sensitive logs and prior retained logs | **MEDIUM / LAUNCH BLOCKER**, Product/Operations + Security + Legal | Confirm Render/proxy/log-drain collection, redaction, access and retention for capability URLs, queries and errors. Local Uvicorn success/rejection/error log evidence covers only repository-controlled logging. Obtain provider/configuration evidence and approved synthetic checks; do not claim old/provider logs sanitized. |
| Historical balances, chart credits, Arcade scores and awards | **MEDIUM / SHOULD FIX**, Product/Operations + Finance + Security | Approve provenance/reconciliation policy and evidence before any data correction. Existing balances/scores are preserved, not certified. |
| Old unsynchronized earnings and duplicate/retry history | **MEDIUM / SHOULD FIX**, Product/Operations + Support | Decide evidence-based restoration/support policy without double grants. Closure is approved policy plus separately tested reconciliation if needed; no automatic retro-awards. |
| Self-reported practice minutes/dates and Board/quest activity | **MEDIUM / SHOULD FIX**, Product + Security | Decide intended verification/date boundaries; backend amount/cap checks do not prove real-world practice. Document policy and test any separately approved enforcement. |
| Legacy syncable quest selection/status, equipment preferences and display histories | **LOW / BACKLOG**, Product engineering | Keep entitlements/rewards server-owned; schedule only a separately scoped cleanup if needed. No state-model redesign here. |
| History static-bank lookup, answer sharing and bots | Retained product limitation, Product + Security | Accept/document or separately propose stronger assessment controls. Ordered correct answers are validated; human effort is not. Do not market this as bot-proof gameplay. |
| PIN recovery | Separate unresolved priority, Product + Security + Support | Define and verify authorized recovery before publishing reset instructions; no verified reset procedure exists in this handoff. |
| Release verification, mixed clients/workers, session configuration and rollback | Release gate, Product/Operations + Security | Review/approve candidate, test isolated rollout and rollback plan, then separately authorize deployment and verify build identity/ordinary synthetic-user smoke checks. No current staging/production verification. |
| Ten historical baseline test failures | Tracked regression debt, Product engineering + QA | Review the exact identities/causes in the cumulative report (standings, contests, schema assertion, assets/markup, teams, verifier metrics). Resolve separately; do not relabel older full-suite runs as current green evidence. |

## Department work that can proceed

- Product/Operations: current small tester observation, demonstrated bug triage,
  release review and September 28 season-transition preparation. Coordinate any
  overlapping code/branch work; preserve this review candidate.
- Marketing/Artwork: prepare materials and collect qualitative feedback using
  verified live features/screenshots coordinated with Product. Unreleased feature
  branches and this candidate are not live-feature or verified-security claims.
- Legal/Finance: continue data-description/compliance planning and billing setup.
  Keep deployed analytics facts, proposed trials/refunds and actual functionality
  distinct. $29 Friendship/$49 regular Full Access pricing does not establish
  checkout readiness. Only Jeff approves the Key Value expense.
- Support: prepare reproduction/known-issue documentation and direct reports to
  woodshedwoodchuck@gmail.com; do not invent PIN-reset instructions.
- Leadership/People: keep concise ownership/decision handoffs. Separate tester
  activity from adoption (Kaylee public, Bucky friend tester, other existing accounts
  provisionally Jeff's tests; new/unfamiliar accounts unclassified).

Broader beta clearance, production throttling, authoritative all-game earning,
provider-log protection and verified-security claims remain gated.

**Single next action for Jeff/Product Operations:** review this handoff and the
cumulative candidate together, accept/assign the open owners and decide whether to
advance the bounded limiter-off candidate to the separate release-review step.
This decision requires no deployment or spending; any staging/release, proxy change,
paid upgrade or limiter activation needs its separately authorized step.

## Saved deliverables

- Current cumulative report: `/home/geph/Training_scripts-security-round1-server-economy/woodshed-woodchuck/docs/security-round1-server-economy.md`.
- Report export: `/home/geph/Downloads/Woodshed_Security_Round1_Report_20260916T062401283120Z.md`.
- This handoff export: `/home/geph/Downloads/Woodshed_Security_Round1_Handoff_20260916T062401283120Z.md`.
- Cumulative patch: `/home/geph/Downloads/Woodshed_Security_Round1_Cumulative_20260916T062401283120Z.patch`.
- Patch base: `efee4d1f3a79ed57505c6b7db897968dd956e256` (pre-security main); includes committed and uncommitted work.
- Patch check/application and byte-for-byte file reproduction passed in a disposable snapshot.
- Final status: 19 tracked modifications, 8 intended untracked files; nothing staged.
  HEAD retained; no commit, push, merge, deployment or production change during closeout.
