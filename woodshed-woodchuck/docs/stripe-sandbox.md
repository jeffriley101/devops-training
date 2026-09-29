# Minimal Stripe sandbox billing

This adapter supports hosted card-only Checkout for `full_monthly_7` ($7 USD/month)
and `full_annual_49` ($49 USD/year). Stripe quantity is always one. Woodshed retains
its five-student membership/seat rules. Open Access never requires a card.
Checkout explicitly hides Link, which can otherwise offer bank payments even with
`payment_method_types=["card"]`.

## Configuration

All settings come from environment configuration. Do not put credentials in source
control. Missing/invalid configuration retains `DisabledProvider`; flags alone do
not enable Stripe. Configure at least one of the two supported Prices. Only
configured Prices are offered; a monthly-only sandbox does not need an annual Price.

| Variable | Value |
| --- | --- |
| `PUBLIC_SUBSCRIPTIONS_ENABLED` | `true` to permit new sandbox purchases |
| `STRIPE_BILLING_ENABLED` | `true` for the adapter, webhooks, and inspection |
| `STRIPE_MODE` | Exactly `test`; live mode is unsupported |
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
access and Checkout creation access. Checkout itself creates its Customer. No live
objects, products, Prices, keys, destinations, or Dashboard settings are created by
the application or by its tests.

Keep the two Price mappings fixed while sandbox checkouts/subscriptions exist.
Changing them does not migrate existing subscriptions; old-Price deliveries will
fail verification and require operator reconciliation.

## Payment and storage boundary

1. Existing Woodshed authorization commits its immutable local price, opaque
   reference, and expiration before calling Stripe.
2. The same reference is the Stripe idempotency key and Checkout/subscription
   metadata. Retrying preserves the original parameters and expiry. Stripe rejects
   a new Session outside its allowed expiration window; do not extend an old local
   authorization to work around that. Its one-hour lifetime is shorter than Stripe's
   idempotency retention window.
3. A return URL only displays a pending-confirmation message. It never grants access.
4. The adapter verifies the webhook signature, rejects live/Connect events, verifies
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
Operate test subscriptions in the Stripe sandbox Dashboard. Full refund/lifecycle
automation and production readiness require a separate reviewed change.

## Validation and rollback

`tests/test_stripe_billing.py` uses real SDK serialization/signature verification
with fake HTTP transport, and disposable SQLite/PostgreSQL databases. It makes no
Stripe API calls. Also run the existing membership, billing hardening, checkout
expiry, recovery, reconciliation, replacement, and tester suites. PostgreSQL cases
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
