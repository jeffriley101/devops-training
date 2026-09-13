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

Student owners occupy the first spot on creation. Adult owners occupy no spot.
Accepted active Verifier and Band Director rosters supply the adult's convenience
picker. The picker is reauthorized on submission. Membership ownership and seat
occupancy never grant access to student analytics or P-Chart review permissions.

Email invitations use separate rows, a SHA256 hash of a random token, seven-day
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
are unique per provider, payloads are hashed, duplicate content is applied once,
out-of-order events are ignored, and unknown subscriptions cannot create access.
Provider details and audit history are visible only to site administration.

Real adapter integration must implement verified checkout completion and initial
membership provisioning, provider signature validation, customer/plan mapping,
network retry/idempotency behavior, cancellation/portal UX and reconciliation.
It must test actual provider event ordering and cancellation semantics before
enabling purchasing. Test callbacks are not production payment support.

## Migration and testing

Revision `o5j6k7l8m9n0` follows `n4i5j6k7l8m9`. It only creates membership/billing
tables; downgrade drops only those tables. No startup code migrates or repairs
data. Tests use disposable SQLite databases. PostgreSQL uses the corresponding
partial indexes and row locks; live PostgreSQL/provider validation belongs to
integration testing before billing launch.

`app/feature_access.py` contains an initially empty registry of enabled features
and their required access. Unknown or disabled features deny access. Future
routes must first authorize the student's context, then call `can_use_feature`.
