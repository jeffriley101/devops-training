# One operator-authorized Stripe subscription

This command supports one Finance approval per Woodshed database. It runs in the application’s private operator shell. The first authorization permanently binds one existing adult verifier, plan, Stripe account, price and mode to one expiring checkout attempt. The audit stores the operator handle, Finance reference and authorization time. An identical authorization prints the same receipt, including after expiry or payment.

## Before the first purchase

1. Record Finance’s approval reference, the adult’s stable Woodshed **verifier ID**, the configured plan code and the operator’s internal handle. Supported plans are `full_monthly_7` (USD 7/month) and `full_annual_49` (USD 49/year). Select the single plan Finance approved. Confirm the verifier’s identity in site administration. The purchaser personally reviews the terms and agrees to recurring billing on Woodshed before opening Stripe.
2. Assign a support contact for the adult. Verify that the website's Stripe credential can read and update the intended subscriptions, the operator can cancel subscriptions and refund payments in the correct Dashboard, and a Woodshed site administrator can inspect the membership. In an authorized Stripe sandbox, check the hosted price, interval, amount due and affirmative final action before billing. Ordinary cancellation keeps Full Access through the paid period. After a confirmed full refund, the site administrator ends the corresponding paid membership using the procedure below.
3. After a separately approved release, use the Woodshed web service’s authenticated shell, in its `woodshed-woodchuck` application directory. Confirm the intended service and database, `STRIPE_MODE=live`, `STRIPE_BILLING_ENABLED=true`, `PUBLIC_SUBSCRIPTIONS_ENABLED=false` and the configured live price. The live webhook destination must deliver `invoice.paid` and `customer.subscription.deleted` to `/membership/webhooks/stripe` using the adapter’s pinned API version. Keep the operator shell private.

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

Give the printed Woodshed review link only to that adult through the verified private channel. The purchaser signs in with the approved verifier account, reopens the link, reviews the price and terms, then explicitly agrees to automatic renewal. Woodshed records the terms version and plan against the checkout attempt before redirecting to the operator-issued Stripe session. Keep the Stripe URL private. If issuance is uncertain, repeat `issue` with the same ID while the attempt is pending. The reference, idempotency key and expiry stay the same. An expired or completed authorization cannot create a replacement. At rollout, allow outstanding direct Stripe links from the earlier version to expire or confirm they were closed before distributing links under this procedure.

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

## Customer cancellation and refund requests

Customers can sign in on the website and select **Membership → Cancel automatic renewal** anytime without giving a reason. Woodshed asks Stripe to stop at period end and displays the confirmed date. Paid access continues to its paid-through boundary. Keep the Stripe adapter and its subscription permissions available while renewable subscriptions exist. Public purchasing can stay closed. A refund is a separate decision.

If a former subscriber still cannot purchase after the paid term ends, check that Stripe shows the original subscription as canceled and that Woodshed processed its termination. In **Site Admin → Memberships**, open the original membership, select **Reconcile Stripe subscription**, and review the recorded result. If the deletion event arrived before the first paid invoice, match and process that invoice, then retry the recorded deletion event in **Billing Recovery**. If Stripe is unreachable or the match remains uncertain, keep the purchase block and payment history intact and follow up in the same case.

Support also accepts cancellation requests, refund requests and reports of an unconfirmed website cancellation at **support@woodshedwoodchuck.com**. Record when the request arrived, its scope and the payer’s billing reference. Honor the request time if handling crosses a deadline or renewal. For cancellation, verify the billing owner and act promptly without asking for practice evidence or justification. If the website outcome is uncertain, inspect the subscription in Stripe before retrying. Reply with the outcome and effective date. Route refunds required by law for review regardless of the voluntary policy.

### Disputed charges

1. Support opens a restricted case for a reported unauthorized, duplicate or incorrect charge, any other billing dispute, or an issuer chargeback. Record the report time, disputed date, amount and currency. Acknowledge receipt and send the case to D4 Finance. Confirm the purchaser through the existing billing channel before sharing account details.
2. Finance matches the disputed charge and PaymentIntent to the invoice, customer, subscription and Woodshed membership in the correct Stripe account and mode. For a claimed duplicate, compare both charges and their paid terms. Check pending and completed refunds, credits and the Stripe dispute status before authorizing money back.
3. For an open issuer dispute, record the dispute ID, status and actual response deadline shown in **Stripe Dashboard → Disputes**. Finance accepts the dispute or submits relevant evidence by that deadline, including prior credits. Handle reimbursement through the open dispute to prevent a second refund. Route legal obligations or uncertain deadlines to D3 promptly and observe applicable law. [Stripe dispute response instructions](https://docs.stripe.com/disputes/responding)
4. Check whether future renewals remain enabled and whether cancellation is requested or required. Process any cancellation separately and confirm its effective date. Record the decision, references, operator, follow-up date and final financial outcome. Update the customer through the verified channel while the case is pending and at closure, including any credit and renewal or access change.

### Identify the purchase and the current paid term

Match the authenticated billing owner, Stripe customer, subscription and original invoice/payment in the correct Stripe account and mode. Public sales use the same checkout, subscription and payment records. In PostgreSQL `psql`, look up the paid invoice history for the verified owner.

```sql
\prompt 'Verified Woodshed billing account ID? ' verified_billing_account_id
SELECT m.id AS membership_id, s.external_subscription_id, s.plan_code,
       p.payment_reference AS invoice_id, f.facts ->> 'occurred_at' AS paid_at,
       f.facts ->> 'period_start' AS term_start, p.paid_through AS term_end
FROM memberships m
JOIN provider_subscriptions s ON s.membership_id = m.id AND s.provider = 'stripe'
JOIN billing_payment_effects p ON p.subscription_id = s.id
JOIN billing_event_applications f ON f.event_id = p.event_id
WHERE m.billing_account_id = :'verified_billing_account_id'::integer
ORDER BY (f.facts ->> 'occurred_at')::timestamptz, p.id;
```

For the Practice Promise, find the owner's **first successful annual purchase**. Check the original Stripe invoice, including the creation versus renewal billing reason, if history is incomplete or conflicting. Use its payment timestamp for both deadlines. An annual renewal **never resets** the Promise. For a current-term request use the paid invoice whose coverage contains the request time, including its start and excluding its end. Check that exact invoice and its original payment for prior or pending refunds. The local subscription's period fields and the newest invoice alone are insufficient.

### Apply the approved refund policy manually

- **Under 13, either plan.** The purchasing parent or guardian can request an unconditional full refund of the current paid term anytime during the term. No explanation or practice evidence is required. Earlier terms are not automatically refundable. Match the verified payer and invoice to the membership, the covered profile's dated seat and age record. Independently confirm this same adult's authority through the existing consent-bound parent channel. If records are missing, withdrawn or inconsistent, preserve the request date and use the existing private authority review. A payer, director or seat owner alone does not establish guardianship. Never ask for a child's PIN. Share only the billing outcome, without a roster or practice history. [Existing guardian verification, steps 1–2](parent-deletion-support.md#verify-the-request)
- **Monthly.** There is no voluntary satisfaction refund. Apply the under-13 and legally required exceptions.
- **Initial annual, unused.** A full refund is available within 14 days of the original annual purchase if unused. Review the purchaser's statement and available evidence of paid use. Free practice before purchase does not disqualify anyone. Free charts or a lack of logged activity cannot settle whether someone used a paid feature. Refer ambiguous usage for manual review.
- **Initial annual, used.** A full refund is available within 30 days of that purchase after all three requirements are met in the initial 30 days. Use the covered profile and seat history.
  1. Count **3 distinct submitted Practice Charts** by chart ID and server `created_at`. Check the reported `practice_date` as well. Submission does not require verifier approval.
  2. Sum **20 minutes of instrumental practice** using `app.practice_duration.chart_seconds` (1,200 seconds total across eligible charts). BOOK records reported minutes and Pristine records detected seconds. Leave Bonus Challenge target minutes out of the instrumental total.
  3. Find **1 completed Bonus Challenge**. Match the profile, date, quest and `QuestCompletion.completed_at` to the corresponding Bonus `RewardGrant`. Older check-ins can be verified using the profile's dated `CampPointAward` with the `bonus-challenge:self-report-v2:YYYY-MM-DD` key and `occurred_at`. Refer unclear historical records for manual review.
- **Boundaries and shared memberships.** Compare payment, request, submission and completion timestamps in UTC. Chart practice dates are Central calendar days and may refer to the previous day. Exclude clearly pre-purchase and out-of-window activity. Review ambiguous dates, changed seats and activity across multiple students manually. Record the decision and billing references without copying student notes.

### Complete and confirm the billing actions

- **Stop renewal.** For assisted cancellation, open **Billing → Subscriptions** in Stripe, match the customer and subscription, and choose **Cancel subscription** at period end with no refund. Confirm the effective date. If a timely cancellation crossed a renewal during handling, review that charge separately. [Stripe cancellation instructions](https://docs.stripe.com/billing/subscriptions/cancel)
- **Return money when authorized.** Check the matched invoice and original payment for pending or prior refunds, credits and issuer disputes. Follow the [disputed-charge procedure](#disputed-charges) for an open dispute. Otherwise, open that payment under **Payments → ⋯ → Refund payment**, confirm the amount and original payment method, select the operator reason and submit once. This works for an initial payment or a renewal. When a full reversal ends the membership, also stop future billing on the matched subscription and confirm the effective date. Use a combined cancellation and refund only when the payment shown matches the authorized invoice. [Stripe refund instructions](https://docs.stripe.com/refunds#issue-refunds)
- **Confirm the full refund.** In the correct Stripe account and mode, match the customer, subscription, invoice and original payment to the Woodshed purchase record. Check that the refund (`re_…`) is **succeeded** and the entire payment has been refunded in its original currency. Ordinary cancellation, a partial refund, or a pending or failed refund leaves local paid access in place.
- **End the matched paid access.** Sign in at `/admin/login`, open **Site Admin → Memberships**, and inspect the reconciled membership ID. Match the owner, `cus_…` and `sub_…`. Under **End paid access after a full Stripe refund**, enter the matched `in_…` and `re_…`, check the confirmation, and submit. If another paid invoice has extended coverage beyond the refunded period, leave that coverage in place and reconcile the later payment before taking further action.
- **Close the record and notify the customer.** Verify the selected membership shows **revoked**, the end time, and the refund and invoice IDs in its audit history. An identical submission is safe to repeat. Confirm the renewal setting separately in Stripe so a reversed membership does not renew unintentionally. Record the operator, membership ID, UTC time, Stripe references, original paid-through date, refund amount/currency/status and effective cancellation date. Reply to the verified purchaser with the refund decision, amount and term, renewal status and access end. If the refund is pending or failed, say so and follow up. Complimentary and lifetime access continue independently.
