# One operator-authorized Stripe subscription

This command supports one Finance approval per Woodshed database. It runs in the application’s private operator shell. The first authorization permanently binds one existing adult verifier, plan, Stripe account, price and mode to one expiring checkout attempt. The audit stores the operator handle, Finance reference and authorization time. An identical authorization prints the same receipt, including after expiry or payment.

## Before the first purchase

1. Obtain the adult’s agreement to the recurring amount and interval. Record Finance’s approval reference, the adult’s stable Woodshed **verifier ID**, the configured plan code and the operator’s internal handle. Supported plans are `full_monthly_7` (USD 7/month) and `full_annual_49` (USD 49/year). Select the single plan Finance approved. Confirm the verifier’s identity in site administration.
2. Assign a support contact for the adult. Verify the operator can cancel subscriptions and refund payments in the correct live Stripe Dashboard and that a Woodshed site administrator can inspect the membership. Ordinary cancellation keeps Full Access through the paid period. After a confirmed full refund, the site administrator ends the corresponding paid membership using the procedure below.
3. After a separately approved release, use the Woodshed web service’s authenticated shell, in its `woodshed-woodchuck` application directory. Confirm the intended service and database, `STRIPE_MODE=live`, `STRIPE_BILLING_ENABLED=true`, `PUBLIC_SUBSCRIPTIONS_ENABLED=false` and the configured live price. The live webhook destination must deliver `invoice.paid` to `/membership/webhooks/stripe` using the adapter’s pinned API version. Keep the operator shell private.

## Authorize and issue

Read the four values from the approved record. Authorization starts the existing checkout validity window (normally one hour) and makes zero Stripe requests.

```bash
read -r -p 'Approved adult verifier ID? ' adult_verifier_id
read -r -p 'Approved plan code? ' plan_code
read -r -p 'Your operator handle? ' operator_handle
read -r -p 'Finance approval reference? ' approval_ref
python -m app.operator_checkout authorize \
  --adult-verifier-id "$adult_verifier_id" --plan "$plan_code" --mode live \
  --operator "$operator_handle" --approval "$approval_ref"
```

Check the printed adult, plan, mode, attempt ID and UTC expiry against the approval. Record the authorization and attempt IDs in the Finance ticket. Issue promptly using the **authorization ID** printed on that receipt.

```bash
read -r -p 'Recorded authorization ID? ' authorization_id
python -m app.operator_checkout issue --authorization-id "$authorization_id"
```

Give the private hosted URL only to that adult using a verified private channel. Treat it as a bearer link and keep it out of tickets and shared logs. The adult reviews the recurring terms and completes payment on Stripe. If the result of issuance is uncertain, repeat `issue` with the same ID while the attempt is pending. The same reference, idempotency key and expiry go to Stripe. An expired or completed authorization cannot create a replacement.

## Reconcile the first invoice

Match the live Stripe customer, subscription, invoice and PaymentIntent to the local attempt. The following read-only query is for PostgreSQL `psql`. Use the authorization ID from the receipt. For the first paid invoice expect a completed attempt, a processed event, one payment effect for that invoice, a Stripe membership owned by the approved adult and matching paid-through dates. If payment is visible but entitlement is missing, use the existing billing recovery controls for that recorded attempt or event.

```sql
\prompt 'Authorization ID? ' authorization_id
SELECT a.id AS authorization_id, b.verifier_id, c.id AS attempt_id, c.plan_code,
       c.amount_cents, c.currency, c.status AS checkout_status, c.provider_checkout_id,
       s.external_customer_id, s.external_subscription_id,
       e.external_event_id, e.status AS event_status, p.payment_reference AS invoice_id,
       f.facts -> 'stripe_invoice' ->> 'payment_intent_id' AS payment_intent_id,
       m.id AS membership_id, m.source, m.status AS membership_status, m.revoked_at,
       p.paid_through, m.access_until, r.details ->> 'refund_id' AS confirmed_refund_id
FROM membership_audit_events a
JOIN checkout_attempts c ON c.id = a.checkout_attempt_id
JOIN billing_accounts b ON b.id = c.billing_account_id
LEFT JOIN provider_subscriptions s ON s.id = c.subscription_id
LEFT JOIN memberships m ON m.id = s.membership_id AND m.billing_account_id = b.id
LEFT JOIN billing_payment_effects p ON p.subscription_id = s.id
LEFT JOIN billing_provider_events e ON e.id = p.event_id
LEFT JOIN billing_event_applications f ON f.event_id = e.id
LEFT JOIN membership_audit_events r ON r.billing_event_id = p.event_id AND r.membership_id = m.id
                                  AND r.action = 'stripe_full_refund_access_ended'
WHERE a.id = :'authorization_id'::integer AND a.action = 'operator_stripe_checkout_authorized';
```

## Manual cancellation and refund

- **Stop renewal.** In the live Stripe Dashboard, open **Billing → Subscriptions**, find the reconciled `sub_…`, verify the customer, and choose **⋯ → Cancel subscription**. To retain the purchased period, select its end and no refund. Confirm the effective date shown by Stripe. [Stripe cancellation instructions](https://docs.stripe.com/billing/subscriptions/cancel)
- **Return money when authorized.** Check the matched invoice and original payment for prior or pending refunds. For an approved immediate cancellation and full reversal of that first payment, select immediate cancellation and a full refund of the last payment in the same dialog. If the subscription was already canceled without a refund, open the matched original payment under **Payments → ⋯ → Refund payment**, enter the approved amount and reason, and submit once. Verify cancellation separately when refunding from Payments. [Stripe refund instructions](https://docs.stripe.com/refunds#issue-refunds)
- **Confirm the full refund.** In the correct Stripe account and mode, match the customer, subscription, invoice and original payment to the Woodshed purchase record. Check that the refund (`re_…`) is **succeeded** and the entire payment has been refunded in its original currency. Ordinary cancellation, a partial refund, or a pending or failed refund leaves local paid access in place.
- **End the matched paid access.** Sign in at `/admin/login`, open **Site Admin → Memberships**, and inspect the reconciled membership ID. Match the owner, `cus_…` and `sub_…`. Under **End paid access after a full Stripe refund**, enter the matched `in_…` and `re_…`, check the confirmation, and submit. If another paid invoice has extended coverage beyond the refunded period, leave that coverage in place and reconcile the later payment before taking further action.
- **Close the record.** Verify the selected membership shows **revoked**, the end time, and the refund and invoice IDs in its audit history. An identical submission is safe to repeat. Record the operator, membership ID, UTC time, Stripe references, original paid-through date, refund amount/currency/status and any effective cancellation date in the support record. Complimentary and lifetime access continue independently.
