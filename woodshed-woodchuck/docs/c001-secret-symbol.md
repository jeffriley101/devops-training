# C001 classroom entry and abuse controls

## Student behavior

“On school computers: Open Woodshed in Guest Mode. Once you reach the SHED,
enter C001 in the Secret Symbol.”

The Guest SHED reuses `wireShedSecret()` and `_shed_secret.html`. Case and
surrounding spaces are ignored **on the server**. Every attempt submits the
existing panel as a native POST to `/guest/secret-symbol`, which returns to
Guest Mode. There is no separate code-entry page. Guest preferences restore
locally and background fetch remains blocked. Invalid symbols show the existing
friendly error. Deliberate submissions write short-lived protection counters,
never student activity, accounts, enrollments, access, consent or KWS records.

Both entry paths use `establish_registration_context()`. Secret Symbol and the
Director #1 QR/link `/prebeta/C001?entry=director1` store
`cohort_key=C001, source=DIRECTOR1` in the signed session. The C001 display page
encodes that Director #1 URL. The original bare `/prebeta/C001` link remains
available without inventing attribution and preserves an existing signed source.
Only the fixed `director1` and existing `secret-symbol` entry markers map to the
server-owned `DIRECTOR1` constant; query/form `source` values are ignored.
Historical records are never reassigned. C001 is a public invitation, not an
authentication credential. See [C001 measurement](c001-measurement.md) for the
verified durable measurement sources and remaining gaps.

Registration persists the source on `TesterEnrollment`. Under-13 requests keep
it on `PendingConsent.cohort_source` through the existing verified parent flow.
The existing tester entitlement derives complimentary lifetime Full Access from
an active, eligible account and enrollment; no billing membership/seat is added.
Existing-account Secret Symbol entry remains informational. Other signed-in
symbols retain their rewards, subject to the submission rate limits.

## Limits and emergency controls

There is **no permanent C001 enrollment cap**, including no 125-account cap.
The existing 125 pending-parent-requests/hour safeguard is separate and unchanged.

All rolling windows are server-enforced, shared across application workers:

| Boundary | Browser/session | Network source |
| --- | --- | --- |
| Secret Symbol attempts | 10 / 10 minutes | 30 / 10 minutes |
| Account creation attempts, including validation failures | 5 / hour | 40 / hour; 100 / 24 hours |
| Successful C001 activations | — | 40 / hour; 100 / 24 hours |

Network identity uses the existing trusted-proxy policy in `login_limits.py`:
Render's overwritten CF-Connecting-IP, or the direct peer/explicit trusted proxy
chain elsewhere (production outside Render requires `FORWARDED_ALLOW_IPS=""`
and an unmodified peer address). Untrusted forwarded headers cannot choose the bucket. Sessions
use a signed random nonce, but counters are server-side: replaying an older
cookie cannot reset a bucket. Clearing cookies can change a session bucket;
network limits still apply. Missing trustworthy Render identity fails closed.
PostgreSQL is the production backend; SQLite is for local functional tests only.

`enroll_tester()` enforces activation permission and capacity. Network transaction
locks serialize velocity checks; the locked, refreshed singleton control row
serializes the global fuse. Checks, enrollment, derived complimentary access,
and activation event commit together. Rejected activation rolls back the new
account and consent evidence. Established parent approval/claim remains retryable.
Under-13 pending requests do not count as activations. Reauthorization of an
existing restricted C001 account also checks activation permission and velocity.

Default emergency ceiling: **1,000 currently active C001 accounts**. Count is
`TesterEnrollment.cohort_key=C001` joined to `WoodchuckProfile.status=active`.
Deleted/deactivated accounts are excluded; historical enrollment remains.
Deletion does not refund request/activation velocity counters within their windows.
Temporary age/privacy restrictions on an existing active account do not free its
reservation: changing a privacy setting must not allow the fuse to be exceeded.
This is an emergency infrastructure fuse, never an advertised enrollment limit.
Capacity warnings begin at 750; high-priority review begins at 900; new activation
stops at 1,000. Raising the ceiling requires an explicit reviewed operator action.

`C001_ACTIVATION_ENABLED` defaults to `true`. False (or invalid configuration)
blocks new activation, including claims established before the switch changed.
The database operator switch adds an immediate cross-worker control without a
restart. Both switches must allow activation. Neither changes existing access,
Guest functionality, enrollment history or pending parent requests. C001 remains
recognizable. The older `C001_REGISTRATION_DISABLED` entry switch is independent;
it blocks new claims only and is **not** the activation kill switch.

Operator commands (run only in the deliberately selected environment):

```sh
python -m app.c001_abuse status
python -m app.c001_abuse disable
python -m app.c001_abuse enable
# Only after explicit Leadership/Operations review:
python -m app.c001_abuse set-ceiling --ceiling REVIEWED_NUMBER
```

`status` rolls back its transaction and does not persist changes. No public
control endpoint exists. A disabled environment switch overrides DB `enable`.
Re-enabling requires no account reconstruction or historical repair.

## Security signals and privacy

`woodshed.security.c001` emits `c001_security_event` records for:

- >20 symbol failures/network/10 minutes;
- >25 C001 creations/network/15 minutes;
- >50 activations/network/hour (normally prevented by the 40/hour hard limit);
- >100 activations globally/hour or >200 globally/24 hours;
- >2 creation/deletion cycles/network/hour, where deletion follows creation
  within an hour;
- >2 rejected age/parent-activation attempts/network/10 minutes;
- >10 rejected creation attempts/network/10 minutes;
- >2 requests after rate-limit responses/network/10 minutes;
- capacity warning/review/fuse, protection backend failure, retention failure,
  and explicit operator control changes.

These are investigation signals, not proof of abuse. A classroom burst can be
legitimate. Emit counts and fixed reason codes only: no PIN, symbol, account ID,
parent email, KWS/consent payload, raw address, URL or cookie enters these logs.
Short-lived database events contain keyed address/session fingerprints and,
for creation/deletion correlation only, a temporary internal profile ID. These
are protection metadata, not student analytics. Compacted rejection events bound
storage from repeated denied requests. Allowed-attempt windows use exact event
timestamps, not reset-at-the-hour buckets.

Expired metadata is deleted on protected attempts and by an hourly app-lifespan
sweep: 24-hour windows, at most 25-hour operational retention while the app runs.
Run `python -m app.c001_abuse prune` during offline maintenance before reopening
an app that has been stopped. Ordinary history/consent/contest rows are never
pruned. Review backup retention separately; restored protection metadata should
be pruned before traffic resumes. Alert delivery/monitoring must watch the
security logger and retention failure signals before production release.

Progressive response: throttle the source, inspect alerts, disable activation
when necessary, investigate without editing student history, block confirmed
abusive sources at the trusted edge, then re-enable once understood. Increase
the emergency ceiling only after reviewed infrastructure/access/support impact.
Existing legitimate students keep their access throughout.

## Migration and release sequencing

Uncommitted migration `e18tester001` follows **d17contest001** directly. It adds:

- nullable `TesterEnrollment.source` and `PendingConsent.cohort_source`, without
  defaults/backfills;
- new protection-event and singleton activation-control tables; the singleton
  starts enabled with a 1,000 ceiling.

No existing account, consent, entitlement, contest or analytics row is rewritten.
The old `eb1216c` app can use this additive schema, but it cannot enforce the new
abuse policy. Migrate first, then deploy the exact reviewed release SHA. If policy
must hold throughout the cutover, pause registration/activation traffic while old
workers drain; changing only the old entry switch does not stop established claims.
Keep the schema on an application rollback. An old app rollback also removes
these protections, so pause new activation or roll forward with a reviewed fix.
Downgrade removes new attribution/protection data and requires deliberate review.
Never stamp revisions or run historical contest repair for this release.

The team-continuity maintenance tool retains its reviewed d17 schema allowlist;
this release does not relax that separate historical-data safety gate.

## Production smoke policy (not executed)

There is no official test-account flag, analytics exclusion, or established
production smoke identity convention. A synthetic active C001 account counts
toward capacity and analytics exactly like another active account.

Recommended policy: create **one** clearly synthetic 13+ account after deployment
is separately authorized, document its generated public ID securely as the release
fixture, verify access/source, then delete it using the supported credential-
confirmed account-deletion route. Do not use real student information.

Normal deletion anonymizes/deactivates the profile and invalidates access and
sessions. It retains tester/source history and immutable scoring/reward/history
records; it does not delete or fabricate historical records. The active-account
fuse and current analytics cohort/activity queries exclude the deleted profile.
An already exported report can retain the temporary fixture; record the short
smoke interval operationally. No independent paid membership is created.

Under-13 smoke is **routing only**: choose the under-13 age option and stop at the
parent-permission page. Do not submit a parent address/request, invoke KWS, or
create consent evidence. Route rendering alone creates no durable account or
parent-request record. Existing QR entry should be checked in a fresh session.

No production migration, account creation, deployment or Stripe work is part of
this preparation. The isolated release branch is based on eb1216c, not Stripe
commit 74956dc. Local evidence and final test results are reported with the release.
