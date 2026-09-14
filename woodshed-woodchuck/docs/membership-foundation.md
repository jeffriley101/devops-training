# Membership foundation

Open Access (`open`) is the default. Full Access (`full`) comes from an active
student seat on a membership that has started, has not been revoked, and has not
passed its exclusive UTC `access_until` boundary. No profile tier is stored.
No existing product feature is paywalled by this foundation.

## Ownership and seats

`BillingAccount` identifies exactly one existing student or adult account, using
two nullable foreign keys and an exclusive-owner constraint. An adult's Verifier
or Band Director relationship role does not affect ownership. If both account
sessions exist, `/membership` requires choosing which account to manage.

One active membership per billing account is supported. Each membership has five
numbered student spots. Partial unique indexes prevent two occupied copies of a
spot or two active seat reservations for the same student. Service transactions
serialize seat assignment; seat removals retain history and free spots immediately.
Expired memberships do not confer access, even before their old reservations are
retired. A new assignment retires an expired prior reservation transactionally.

Student owners occupy the first spot on creation and cannot remove themselves
through owner seat management. Adult owners occupy no spot. Owners add students
by exact Woodchuck ID, immediately assigning access without email or acceptance.
There is no public student directory. Membership ownership and seat occupancy
never grant access to student analytics or P-Chart review permissions.

Legacy email invitation infrastructure remains for compatibility, not the current
owner assignment UI. It uses separate rows, a SHA256 hash of a random token, seven-day
expiry and authenticated student claims. Email is a delivery destination only.
Claims recheck active membership, capacity and the student's existing seat. Pending
invitations are bounded by currently free spots, but do not reserve an active
seat against direct assignments. Owners can cancel and recreate invitations.
Tokens are not stored in sessions, audits or database plaintext. Invitation pages
have `no-store` and `no-referrer` headers; production HTTP access logs should redact
invitation URL tokens, consistent with other private invitation links.

## Administration

`SITE_ADMIN_TOKEN` protects `/admin/login` and `/admin/membership`, independently
of contest administration. Login accepts the token in a POST body; the signed
browser session holds an HMAC fingerprint. Token rotation invalidates sessions.
Membership and admin writes use session CSRF tokens. Admin grant/revoke and seat
changes are audited. The single administrator is recorded as actor type `admin`;
there is no invented multi-user administrator identity.

Complimentary memberships are manual, have no provider subscription or fake
payment, and may be revoked independently. The service supports an optional UTC
access deadline. Account foreign keys restrict hard deletion of billing history;
the existing student soft-deletion flow remains authoritative, and deleted
students never resolve to Full Access.

## Pricing and providers

`app/billing_config.py` owns the immutable code catalog (integer USD cents):

| Code | Public label | Amount | Interval |
| --- | --- | ---: | --- |
| full_monthly_7 | Full Monthly | 700 | month |
| full_annual_49 | Full Annual | 4900 | year |
| friendship_annual_30 | Launch Sale | 3000 | year |

The four flags default to false: `PUBLIC_SUBSCRIPTIONS_ENABLED`,
`PAYPAL_BILLING_ENABLED`, `STRIPE_BILLING_ENABLED`, `LAUNCH_SALE_ENABLED`.
Launch availability controls new subscription selection only. Subscription rows
retain plan code and the price/interval/currency agreed at creation. No coupon
system exists. Terminated subscriptions cannot be reactivated by the event
processor; a later subscription must undergo new plan eligibility checks.
Cancellation can retain Full Access until its paid-through date.

PayPal and Stripe adapters deliberately raise `BillingUnavailable` even if flags
are enabled. They make no network calls and require no credentials or SDKs.
The test adapter exists only in tests. Checkout derives pricing on the server,
and never grants access or treats a browser return as payment proof.

The event processor accepts only adapter-verified normalized events. Event IDs
are unique per provider, payloads are hashed, and duplicate effects are applied
once. Unknown subscriptions retain verified facts for explicit retry; they can
provision only with a valid durable checkout and confirmed paid entitlement.
Provider details and audit history are visible only to site administration.

Real adapter integration must implement provider signature validation, customer/plan mapping,
network retry/idempotency behavior, cancellation/portal UX and reconciliation.
It must test actual provider event ordering and cancellation semantics before
enabling purchasing. Test callbacks are not production payment support.

## Phase A1 checkout and entitlement contract

`authorize_checkout()` persists a server-generated random reference, billing
owner, provider and immutable authorized plan/amount/currency/interval snapshot.
Concurrent ordinary submissions reuse the owner's valid pending attempt for the
same provider/plan. An explicit reference retries only that authorized owner's
attempt. `new_attempt=True` requests a distinct authorization when allowed;
recoverable paid checkouts block another purchase pending review. Expired pending
authorizations do not permanently reserve a checkout slot. The reference, not
CSRF/session material, is the provider idempotency/correlation key.

An unprocessed payment in the durable inbox also blocks a new purchase, even if
a database failure prevented marking its checkout recoverable. Checkout states
support `pending`, `completed`, `expired`, `failed`, and `recoverable`; validity
is checked against `expires_at` even without a background expiration worker.

`CHECKOUT_VALIDITY_SECONDS` has **no default**. A positive server-configured
duration must be selected before checkout can run. The 600-second values in
tests are fixtures, not a product hold policy. All four existing flags still
default false, and neither live adapter implements transport.

`checkout()` commits authorization, closes the transaction, then invokes the
adapter. A subsequent transaction records the returned provider checkout ID.
If transport fails with an unknown outcome, retry uses the same opaque reference;
future adapters must honor idempotency and authorization expiry remotely.

The normalized event optionally includes `paid_through` and `payment_reference`.
Together these assert adapter-verified payment entitlement. The adapter must
verify the authorized provider product/amount/currency/interval, environment,
subscription ownership and payment before supplying them. Neither lifecycle
status, billing period nor browser checkout completion proves payment.
`Membership.access_until` is the exclusive confirmed paid-through boundary for
paid memberships. Manual complimentary semantics remain unchanged.

`process_webhook()` verifies outside transactions, commits a minimal event inbox,
then separately calls `apply_verified_event()`. `BillingEventApplication` stores
normalized facts, correlation, attempts and `pending`/`recoverable`/`processed`
state. A savepoint prevents partial membership/subscription/seat/audit writes.
An unresolved payment leaves the parent event `failed`, with no `processed_at`,
and the checkout `recoverable`; an operator can retry `apply_verified_event()`
in a new transaction after correlation or the conflict is resolved. No retry
sends another payment/checkout request. No worker or scheduler is installed.
If ingestion cannot commit, delivery fails and must be retried by the provider;
database durability cannot be promised during database unavailability.

Checkout authorization, inbox ingestion and application serialize on the billing
account before event/subscription/membership locks. Existing accounts do not
first lock their student identity. Creating a missing billing account temporarily
locks its identity; a concurrent creator releases that savepoint lock before
locking the newly visible account. Checkout correlation is refreshed rather than
trusting cached ORM state. Correlation discovered after lock selection requires
a new transaction/retry, rather than taking an account lock out of order.

Launch fulfillment reads the stored snapshot rather than today's sale flag.
Automatic initial application requires both payment occurrence and initial
receipt within the checkout validity window. Evidence arriving outside that
window remains recoverable for review, without granting access or inferring a
refund. A timely received payment may finish local recovery after the window
closes. Provider-specific delayed-delivery reconciliation remains future work.

Confirmed entitlement advances monotonically. A unique subscription/payment
reference records each payment effect; reuse with conflicting entitlement facts
requires review. Different events at equal timestamps can still apply distinct
payments. Conflicting lifecycle facts at equal timestamps are retained for
reconciliation without undoing paid entitlement. Older lifecycle events cannot
overwrite newer metadata or suppress older independent payment effects. Refunds
and reversals never reduce entitlement in A1. New payments after terminal
subscription state remain recoverable rather than silently restoring launch
pricing. The adapter must map actual provider payment identities to this hook.

Deferred to integration: remote pending-checkout cancellation, provider
environment/account verification, operator reconciliation tooling, and
refund/grace/cancellation policies.
There is no inferred team, relationship, payer-privacy or feature-gating change.

Revision `p6k7l8m9n0o1` follows `o5j6k7l8m9n0` and adds only
`checkout_attempts`, `billing_event_applications`, and `billing_payment_effects`.
Downgrade removes only these additions; it does not rewrite existing history.

## Phase A2 replacement contract

`app/billing_replacement.py` supplies the shared purchase decision for checkout
authorization and initial paid provisioning. An old paid membership can become
historical only when its confirmed paid-through date has elapsed, its provider
subscription has a verified `terminated_at` at or before now, and correlated
billing work is resolved. A past-due or expired access date alone cannot prove
that a recurring provider contract has stopped charging. No provider cancellation
is inferred or performed. Manual/complimentary grant and expiry behavior remains
unchanged.

The decision checks all owner membership history, checkout references and
provider/subscription references (including events without checkout references).
Pending/recoverable applications, inconsistent processed state, recoverable
attempts, and payment effects beyond the materialized paid-through date block
replacement without a timeout. A replacement may reuse its pending checkout;
another live pending checkout blocks a distinct replacement attempt.

Safe normalization changes only `Membership.status` from `active` to `ended`,
with one `membership_ended` audit record. This occurs transactionally at checkout
authorization and is rechecked during provisioning. Provider IDs, paid-through
history, payment records and discretionary seats are retained. Replacement
creates new membership/subscription IDs, automatically seats a student owner,
and does not copy discretionary seats. The existing seat helper retires an old
owner reservation when it adds the new owner's seat.

Payments received after normalization remain in the recoverable inbox. They
never reactivate the historical membership or extend the replacement. If such
work arrives before replacement provisioning, it blocks provisioning too.
Successful payment with an owner-seat conflict is retained for explicit review;
A2 supplies no refund, cancellation or automatic resolution policy.

PostgreSQL uses account `FOR NO KEY UPDATE` locks, compatible with ordinary FK
`KEY SHARE` checks, at READ COMMITTED isolation. SQLite serializes writers with
an update. Billing locks precede membership and student-seat locks; provider
transport runs outside transactions. Savepoints retain failed application facts
while rolling back partial entitlement writes. Serialization/deadlock errors
require whole-transaction retry; a separately committed inbox remains durable.

`tests/test_billing_replacement.py` runs SQLite cases plus opt-in PostgreSQL cases
using `WW_BILLING_TEST_POSTGRES_URL`. This must explicitly name a loopback-hosted
`ww_billing_a2_test` disposable database; it never uses `DATABASE_URL`. Tests create
random isolated schemas, retained for failure inspection. PostgreSQL tests observe
actual lock waits for recovery versus checkout and payment versus seat management.
Production topology, higher isolation levels and provider transport still need
their own integration verification. No A2 migration or checkout duration is added.

## Migration and testing

### Phase A3: Site Admin recovery

`GET /admin/billing-recovery` shows pending/recoverable billing applications,
inconsistent payment effects and uncorrelated recoverable checkouts. Successful
historical events are excluded. Event pages are bounded to 50 rows with older-event
navigation; recent administrator actions remain visible after successful recovery.
Only selected internal correlation IDs, provider/category, timestamps and safe
reason text are rendered. Raw verified facts, payload hashes, signatures, email
addresses and external customer identifiers are not dumped into HTML.

`POST /admin/billing-recovery/{event_id}/retry` requires the existing Site Admin
session and unchanged CSRF/origin checks. Its only form field is the CSRF token;
prices, plans and entitlement values cannot be supplied. Public purchasing and
both live providers remain disabled. No provider transport is called by recovery.

`app/billing_recovery.py` owns retryability. Pending/recoverable normalized work
with sufficient correlation and payment evidence can enter the normal processor.
Missing correlation/evidence, expired authorization, conflicting payment/lifecycle
facts, ended memberships and inconsistent applied effects require reconciliation.
Unknown reason codes fail closed. Every decision is repeated under the same
account/event locks used by automatic processing. Both paths use
`apply_locked_event()`; neither has a privileged entitlement override. Correlation
that appears after lock selection is retained for a fresh transaction retry.

Each accepted administrator retry first commits a `billing_retry_requested`
MembershipAuditEvent targeted at the durable billing event. The result audit
commits atomically with processing effects. Success, still-recoverable, blocked
and already-completed outcomes are all recorded, including stale browser posts.
Unexpected local transaction failure rolls back processing and records a safe
failure outcome in a separate transaction. If the database becomes unavailable,
the already-committed request remains; a result cannot be guaranteed until the
database is writable. No exception strings or raw provider facts enter the audit.
Invalid credentials/CSRF and nonexistent event IDs never start a recovery action.

Revision `q7l8m9n0o1p2` follows `p6k7l8m9n0o1`. It extends only
`membership_audit_events`: nullable membership target, optional indexed
`billing_event_id`, RESTRICT foreign key, and a check requiring at least one
target. Existing membership audits remain intact. No placeholder membership is
created for failed initial provisioning. Successful result audits can reference
both targets and appear in the existing membership audit history.

Downgrade is supported before recovery audits exist. It locks the audit table
and refuses a downgrade once event-targeted audit history exists, because the
old schema cannot represent that history without loss. Never delete recovery
audits merely to downgrade; rolling back application code while retaining the
expanded schema is preferable to losing financial records.

A3 tests cover SQLite and opt-in disposable PostgreSQL using the A2 fixture,
including simultaneous admin retries, retry versus automatic processing, retry
versus replacement, transaction failure/reentry, and migration round trips with
foreign-key/deletion and downgrade guards. Historical migration fixtures use
their reflected audit schema instead of the expanded current ORM model.

Deferred to Phase A4: provider-backed reconciliation and explicit resolution
policy for conflicts local retry cannot resolve. No refund, capture, cancellation,
grace period, checkout duration or entitlement override is introduced here.

Revision `o5j6k7l8m9n0` follows `n4i5j6k7l8m9`. It only creates membership/billing
tables; downgrade drops only those tables. No startup code migrates or repairs
data. Tests use disposable SQLite databases. PostgreSQL uses the corresponding
partial indexes and row locks; live PostgreSQL/provider validation belongs to
integration testing before billing launch.

`app/feature_access.py` contains an initially empty registry of enabled features
and their required access. Unknown or disabled features deny access. Future
routes must first authorize the student's context, then call `can_use_feature`.
