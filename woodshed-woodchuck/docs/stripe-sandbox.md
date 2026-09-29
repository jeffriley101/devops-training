# Minimal Stripe billing: sandbox and explicit live mode

This adapter supports hosted card-only Checkout for `full_monthly_7` ($7 USD/month)
and `full_annual_49` ($49 USD/year). Stripe quantity is always one. Woodshed retains
its five-student membership/seat rules. Open Access never requires a card.
Checkout explicitly hides Link, which can otherwise offer bank payments even with
`payment_method_types=["card"]`.

## Sandbox configuration

All settings come from environment configuration. Do not put credentials in source
control. Missing/invalid configuration retains `DisabledProvider`; flags alone do
not enable Stripe. Configure at least one of the two supported Prices. Only
configured Prices are offered; a monthly-only sandbox does not need an annual Price.

| Variable | Value |
| --- | --- |
| `PUBLIC_SUBSCRIPTIONS_ENABLED` | `true` to permit new sandbox purchases |
| `STRIPE_BILLING_ENABLED` | `true` for the adapter, webhooks, and inspection |
| `STRIPE_MODE` | `test` for the sandbox; required, never inferred from the key |
| `STRIPE_SECRET_KEY` | Test secret key (or test restricted key with required permissions) |
| `STRIPE_WEBHOOK_SECRET` | Signing secret for this sandbox endpoint |
| `STRIPE_ACCOUNT_ID` | Expected sandbox `acct_...` identity |
| `STRIPE_PRICE_FULL_MONTHLY_7` | Test recurring $7 USD/month Price, licensed/per-unit, interval count one |
| `STRIPE_PRICE_FULL_ANNUAL_49` | Test recurring $49 USD/year Price, licensed/per-unit, interval count one |
| `PUBLIC_BASE_URL` | Sandbox HTTPS origin; HTTP allowed only for localhost/127.0.0.1 |
| `CHECKOUT_VALIDITY_SECONDS` | Existing default 3600; use 3600 for this sandbox |

Use an isolated local/staging Woodshed database. Stripe test mode does not itself
isolate Woodshed's database. No production environment changes are part of this work.

The pinned SDK is `stripe==15.6.1`; API version is `2026-08-26.dahlia`. Configure
the sandbox webhook destination for **that version**, subscribing to `invoice.paid`
at `/membership/webhooks/stripe`. Do not use a Connect destination. Changing SDK or
webhook versions requires rerunning adapter tests against the new response shapes.

The key needs account/event/Price/Checkout/InvoicePayment/PaymentIntent/invoice read
access and Checkout creation access. Checkout itself creates its Customer. Tests
use fake HTTP transport and create no Stripe objects. The application does not
provision products, Prices, keys, webhook destinations, or Dashboard settings.

Keep the two Price mappings fixed while sandbox checkouts/subscriptions exist.
Changing them does not migrate existing subscriptions; old-Price deliveries will
fail verification and require operator reconciliation.

## Production configuration (keep disabled)

Production must remain disabled until its live configuration is explicitly supplied,
reviewed, and activation is separately authorized. This code change does not enable
production, deploy, change Render, or create any Stripe resources. Keep both
`STRIPE_BILLING_ENABLED=false` and `PUBLIC_SUBSCRIPTIONS_ENABLED=false` during setup.

| Variable | Live configuration for later review |
| --- | --- |
| `STRIPE_MODE` | `live` |
| `STRIPE_SECRET_KEY` | `rk_live_...` with the permissions above (preferred), or `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` for the live direct-account webhook endpoint |
| `STRIPE_ACCOUNT_ID` | Expected live account `acct_...`; verified using the configured key |
| `STRIPE_PRICE_FULL_MONTHLY_7` | Fixed live recurring $7 USD/month Price, licensed/per-unit, interval count one |
| `STRIPE_PRICE_FULL_ANNUAL_49` | Fixed live recurring $49 USD/year Price, licensed/per-unit, interval count one |
| `PUBLIC_BASE_URL` | Production HTTPS origin |
| `CHECKOUT_VALIDITY_SECONDS` | Keep the existing default of 3600 |
| `STRIPE_BILLING_ENABLED` | Keep `false` pending explicit activation |
| `PUBLIC_SUBSCRIPTIONS_ENABLED` | Keep `false` pending explicit activation |

At least one supported Price must be configured. Keep Price mappings fixed while
checkouts/subscriptions exist. Store credentials in the platform's secret settings,
never in source control. The live webhook must use API version
`2026-08-26.dahlia`, deliver `invoice.paid` to `/membership/webhooks/stripe`, and use
its own signing secret. Provisioning that endpoint is outside this change.

`STRIPE_MODE` accepts only `test` or `live` after trimming surrounding whitespace.
Missing, blank, or other values disable the adapter. Test mode requires `sk_test_`
or `rk_test_` keys, verified evidence with `livemode=false`, and `cs_test_` Checkout
IDs. Live mode requires `sk_live_` or `rk_live_` keys, verified evidence with
`livemode=true`, and `cs_live_` Checkout IDs. A matching key alone never selects a
mode. Wrong-mode evidence is rejected during Checkout, webhook processing, and
inspection/reconciliation.

The flags retain their separate roles: public subscriptions gate new purchases;
Stripe billing gates the adapter, webhooks, and inspection. After a future launch,
stopping new purchases alone must leave Stripe billing enabled to process outstanding
payments. Keep sandbox application data isolated from production; switching modes
does not migrate subscriptions or stored evidence. Live credentials, permissions,
Prices, and webhook delivery have not been verified by these offline tests.

## Payment and storage boundary

1. Existing Woodshed authorization commits its immutable local price, opaque
   reference, and expiration before calling Stripe.
2. The same reference is the Stripe idempotency key and Checkout/subscription
   metadata. Retrying preserves the original parameters and expiry. Stripe rejects
   a new Session outside its allowed expiration window; do not extend an old local
   authorization to work around that. Its one-hour lifetime is shorter than Stripe's
   idempotency retention window.
3. A return URL only displays a pending-confirmation message. It never grants access.
4. The adapter verifies the webhook signature, rejects wrong-mode/Connect events, verifies
   the key's account, and retrieves the event under that account. A paid invoice must
   match the allowlisted Price, quantity one, local checkout snapshot, Customer, and
   actual successful card PaymentIntent. Tax, discounts, prorations, adjustments,
   out-of-band settlement, and unsupported invoice shapes fail closed.
5. Verified facts enter the existing durable inbox before membership application.
   Payment time comes from the invoice's `status_transitions.paid_at`; coverage comes
   from its subscription line's period. Delayed deliveries retain that original time.
   Invoice ID deduplicates the entitlement effect, including different event IDs.

**No migration is needed.** Existing fields retain:

- `CheckoutAttempt.provider_checkout_id`: Checkout Session ID.
- `ProviderSubscription.external_subscription_id` / `external_customer_id`: Stripe IDs.
- `BillingPaymentEffect.payment_reference`: paid invoice ID.
- `BillingEventApplication.facts.stripe_invoice`: account, Customer, Checkout, Price,
  PaymentIntent IDs, and the verified local plan/amount/currency/interval.

No raw webhook, card, or customer contact data is stored by this adapter. Existing
event facts without `stripe_invoice` retain their previous serialization and behavior.
No customer exists for free users solely because they use Woodshed.

Paid invoices are payment evidence, **not current subscription lifecycle evidence**.
This first adapter leaves subscription status `unknown` and does not invent
cancellation or termination timestamps. Paid access extends monotonically; without
a further paid invoice it expires at `access_until`. Complimentary memberships and
independent lifetime tester access retain their existing rules.

## Recovery and current limits

Existing admin recovery can retry committed facts without Stripe transport.
Existing admin inspection can retrieve a Stripe event, or recover the first paid
invoice from a stored Checkout Session. Inspection uses the same verification and
application path. A stable synthetic receipt for that invoice and a later webhook
share one invoice payment effect. Missing/conflicting evidence never overrides local
authorization. Stripe event retrieval is subject to Stripe's event retention window.
An unknown Checkout creation outcome remains retryable with its original local reference.

This first path does not implement Portal, subscription lifecycle synchronization,
cancellation, replacement after Stripe termination, refunds, tax collection, launch
pricing, plan changes/proration, separate invoicing UI/APIs, alternate payment methods,
custom dunning, schedules, or background jobs. Ignored webhook types grant nothing.
Operate test subscriptions in the Stripe sandbox Dashboard. Refund and lifecycle
automation remain outside this adapter. Live activation requires separate human review
and configuration. Automatic tax remains disabled; tax collection is outside this change.

## Validation and rollback

`tests/test_stripe_billing.py` uses real SDK serialization/signature verification
with fake HTTP transport in both test and live modes, and disposable SQLite/PostgreSQL
databases. It makes no Stripe API calls. Also run the existing membership, billing
hardening, checkout expiry, recovery, reconciliation, replacement, and tester suites. PostgreSQL cases
use the existing `WW_BILLING_TEST_POSTGRES_URL` guard: local host only and database
name `ww_billing_a2_test`.

Before any deployment decision, separately exercise hosted Checkout and an actual
`invoice.paid` delivery in an isolated sandbox. That external smoke test is not
claimed by the automated tests.

To stop new purchases, set `PUBLIC_SUBSCRIPTIONS_ENABLED=false`. Keep
`STRIPE_BILLING_ENABLED=true` and the adapter configuration available so outstanding
payments and inspection continue to work. Preserve the inbox and payment history;
there is no schema downgrade. Reverting the adapter entirely stops incoming Stripe
processing and older processors cannot read the new optional invoice facts. Keep the
updated processor available for stored events; a full code rollback requires explicit
handling of outstanding test subscriptions and inbox work.
