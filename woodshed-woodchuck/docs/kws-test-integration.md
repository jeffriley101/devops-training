# KWS Test private-practice candidate

This is a local candidate on Release 3 (`ccbfd9b01b4435a0c673a9584ce6751c29be6ae5`). Production activation remains closed. C001 enrollment, complimentary grants and the finalizer are separate. No verification requires a Full purchase.

## Flow

1. `/family/request` emails the existing parent permission capability; it creates no child account or director invitation.
2. `/family/approve/{token}` records guardian attestation, current notice acceptance and account consent. Named-director sharing and chart review are separate optional selections. Declining sharing allows Free private practice; declining account consent closes the request without a KWS email.
3. The server reserves an opaque 256-bit request binding and durable email budget, then makes one KWS Test send-email call. Acceptance is not verification. Uncertain delivery is not automatically retried.
4. Either signed callback completes the same locked request once. Reopen the original permission email link for activation, including when the webhook arrives before the browser response.
5. Activation reuses the existing account or creates one new Free child account. Only explicitly selected director permissions are created. The director still must accept and authenticate. Parent access still uses its separate expiring email link; KWS never creates a parent session.

The previous email-only approval/follow-up method is disabled. Notice v3 requires fresh permission; old evidence is retained, not rewritten. Parent analytics remain entitlement-aware. Existing 13+ transition protections and withdrawal/revocation remain in force.

## Exact callback paths

* **POST** `/family/kws/test/webhook`
* **GET** `/family/kws/test/response`

The running temporary local HTTPS origin and reachability evidence are recorded in the latest external `Woodshed_KWS_Test_20260918/Checkpoint.md`. Hostnames are temporary; do not reuse an expired URL. The URL pattern is:

* `https://<assigned-test-host>/family/kws/test/webhook`
* `https://<assigned-test-host>/family/kws/test/response`

These patterns are placeholders, not production URLs. Use the actual verified hostname from the checkpoint for Test portal configuration.

## Environment variables

Set these only on a separate Test instance with a disposable/dedicated Test database:

| Variable | Meaning |
|---|---|
| `APP_ENV` | Exactly `kws-test`; `production` cannot open this Test integration. |
| `KWS_TEST_ENABLED` | Exactly `true`; default disabled. |
| `KWS_ENVIRONMENT` | Exactly `test`; no environment inference from API hostname. |
| `KWS_TEST_DATABASE_CONFIRMED` | Exactly `true`, only after confirming the database is isolated from production/C001. |
| `KWS_TEST_CLIENT_ID` | Server-only **Test** client ID from the portal. |
| `KWS_TEST_API_KEY` | Server-only **Test** API key. |
| `KWS_TEST_ORG_ID` | Expected organization ID. |
| `KWS_TEST_PRODUCT_ID` | Optional expected product ID; supplied non-null webhook product IDs must match. Null organization-level events are supported. |
| `KWS_TEST_WEBHOOK_SECRETS` | JSON array of 1–4 active webhook secrets; supports rotation. |
| `KWS_TEST_VERIFICATION_SECRETS` | Separate JSON array of 1–4 verification-response secrets; supports rotation. |
| `KWS_TEST_LOCATION_JSON` | Required JSON string country/subdivision code. For this US Test journey, enter exactly `"US"`, including the double quotes; `"US-NY"` is a subdivision example. No default country. |
| `KWS_TEST_RECIPIENTS_JSON` | JSON array of 1–10 explicitly designated adult Test inbox addresses before outbound delivery can be enabled. All Test KWS/SMTP recipients must be listed. Local preparation uses `[]` with all outbound delivery disabled. |
| `KWS_TEST_RUNTIME_MODE` | `local` for the isolated local launcher; validates the database before engine creation, binds loopback only and disables API explorers. |
| `KWS_TEST_DATABASE_SOCKET` | Independently pinned absolute private Unix-socket directory in local mode. TCP database hosts, other database names and extra connection query options are rejected. |
| `KWS_TEST_PUBLIC_HOST` | Exact actual tunnel hostname; local public origin must match. |
| `KWS_TEST_OUTBOUND_ENABLED` | Exactly `true` before local KWS/SMTP delivery can occur; local preparation fixes this to `false` until adult recipients are designated. |
| `KWS_TEST_DATABASE_HOST` | Exact hostname from the independently confirmed Test database's internal URL, used by the guarded Test migration/start commands. |
| `KWS_TEST_LANGUAGE` | Language; defaults to `en`. |
| `PUBLIC_BASE_URL` | Reachable Test HTTPS origin for existing permission/access email links. |
| `DATABASE_URL` | Dedicated Test database only. |
| `SESSION_SECRET` | Separate strong Test session secret; also keys the existing capabilities and email-budget hashes. |
| `SESSION_COOKIE_SECURE` | `true` on HTTPS. Render already requires secure cookies. |

Existing SMTP settings are reused for Woodshed permission/access emails. Automated tests capture them; the running local setup blocks all outbound delivery. Never enter real credentials in source, browser code, command transcripts, logs, chat or artifacts. Config repr is disabled; upstream bodies/errors are not logged. Credentials are never forwarded across HTTP redirects.

The location environment value is parsed exactly once: `"US"` becomes Python string `US`; the outgoing JSON contains `"location": "US"`. An object, unquoted `US`, or literal `"GB or AD-07"` is rejected. Epic's latter example shows alternatives. The US value is only the selected location for this designated Test journey; other Test locations require their correct country/subdivision code. It is not a production country assumption or a default for future users.

The Test-only recipient restriction checks all SMTP To/Cc/Bcc and Resent recipients before connecting, and KWS recipients before OAuth. Existing non-Test email behavior is preserved. Use separate Test SMTP credentials and only designated adult parent/director inboxes; never import student/parent/director lists or production records. A Test director must be allowlisted before invitation delivery. The guard does not prove adulthood: the operator must explicitly designate adult recipients.

For the proposed Render setup, use `python -m app.kws_test_runtime migrate` as the pre-deploy command and `python -m app.kws_test_runtime serve` as the start command. Before any database connection, these require explicit Test environment, fixed database name `woodshed_kws_test`, independently pinned Test hostname, strong separate session secret, secure cookies, the assigned `woodshed-kws-test-*.onrender.com` HTTPS origin and recipient allowlist. This launcher is Test-only and does not change production startup. Uvicorn access logging is disabled so callback query signatures/capabilities are not written by its access logger.

The paid Render proposal is superseded by the user's local-testing choice. The external local controller uses a clean environment, separate session secret and disposable synthetic-only PostgreSQL over a private Unix socket. Its database container has no network and no TCP listener. Only the loopback app socket is tunneled; API explorers and access logs are disabled. The public HTTPS origin is pinned and session cookies remain Secure. Callback secrets can be entered privately while outbound email stays disabled; no recipient is implicitly authorized by secret entry.

After explicit adult recipient designation, a private operator-owned `delivery-authorization.json` alongside the pinned database socket may opt this local app into delivery to exactly one inbox. Missing, invalid or shared-readable policy stays closed. App reload applies the policy while preserving the disposable database/tunnel; `delivery-runtime.json` records the current app PID and effective gate without secrets. The launcher still starts with closed delivery environment defaults. Private `callback-deliveries.jsonl` records only accepted callback method/path/status/time, never raw body, query, signatures or cookies. Database state must separately confirm verified binding/activation; a 2xx receipt alone is not proof of consent or account activation. These files are outside source control and no public debug endpoint is added.

Epic's official Testing page requires the recipient to be an organization developer/admin with appropriate product access (or a permitted alias). It directs credit/debit-card testing to Stripe's documented test numbers. For the successful interactive Visa case use `4242 4242 4242 4242`, a future expiry and any three-digit CVC, only in the explicitly confirmed KWS Test flow. Never use real card details or attempt a real charge. Test verification does not add the inbox to the AgeGraph. Sources: [Epic Testing](https://dev.epicgames.com/docs/kids-web-services/kws-testing), [Stripe Testing](https://docs.stripe.com/testing).

## Security and local freshness policy

Webhook HMAC signs the original timestamp, a period and exact raw UTF-8 bytes before parsing. Browser-response HMAC signs the once-decoded status string, a colon and once-decoded payload string without JSON reserialization. Both use constant-time comparisons across rotated secrets/signatures. Duplicate status/payload query fields and duplicate JSON fields are rejected.

Epic's supplied webhook example `t=1621535329` is consistent with epoch seconds. The implementation validates its numeric epoch-seconds value for freshness but signs the original timestamp text (including any leading zero), never a reformatted integer. Freshness was not weakened. Confirm the actual header representation, retry timestamps and event/scope shape during real KWS Test delivery; those untested provider behaviors do not prevent setup preparation.

Local policy: signed timestamps must be within the previous 24 hours, no more than five minutes in the future, and no earlier than the request start minus five minutes. The request expires after 48 hours; activation after verification expires after 72 hours. These are application safety limits, not claimed provider-mandated freshness or approved retention periods. A signed webhook timestamp protects delivery freshness; a browser response uses its signed status timestamp. A late expired request is durably cancelled. Pending-row locks, unique payload/transaction hashes, frozen permission/notice/environment bindings and profile-session checks prevent duplicate completion, transaction reuse or resurrection after withdrawal/revocation/supersession. Withdrawal and activation use the same lock order.

Durable rolling email budget: at most three send attempts/hour/email, at least five minutes apart, below KWS's ten/hour limit. Budget commits before outbound delivery. OAuth retries up to three attempts with 0.25/0.5-second backoff for explicit transient HTTP failures; verification email POSTs attempt once and never retry an ambiguous timeout/5xx. Access-token cache refreshes before expiry.

Migration `a13screen004` adds only the binding and budget tables after `a13screen003`; no account/history/entitlement rewrite. No downgrade is supported. Compatible Guest-only recovery preserves Release 3, accepts the retained 004 schema, blocks both KWS callbacks, and allows withdrawal/revocation. Any recovery deployment still requires a separate decision.

## Smallest next step

The temporary local HTTPS tunnel is authorized; paid hosting and production remain untouched. Use the checkpoint's currently reachable URLs to configure the portal's Test webhook and verification response, then use the external local controller's hidden terminal prompts for Test API credentials and the two distinct callback secrets. Do not send verification or permission emails until the operator designates adult Test recipients. Leave production/C001, both existing Auto-Deploy settings and the finalizer untouched.

Obtain the service's actual assigned HTTPS hostname and verify TLS/reachability before publishing it. An unsigned webhook/result should return 400 when configured (503 while disabled); test valid signed callbacks using synthetic records. Publish the two exact URLs above in the portal's **Test** configuration, then enter the two distinct callback secrets and Test API credentials through the host's secure server-side secret controls. Confirm Test selection/published card methods explicitly; the shared API/auth hosts do not establish environment.

Then run an actual KWS Test journey with provider-approved Test inputs and explicitly designated adult recipients, confirming email delivery/card-method presentation, scope/event shape, both callback methods/order and safe activation. The supplied official contract resolves location as a JSON country/subdivision string; there is no remaining location-shape ambiguity. Exact provider-approved Test card inputs and any provider/mail charges must be confirmed from the portal/official Test instructions before a card-method attempt; no guessed card numbers or real payment card should be used.

Actual KWS delivery, card flow and public callback reachability are not established by local mocks. Policy/provider/notice/retention review and production activation remain separate gates.

Contract sources: [Parent Verification](https://dev.epicgames.com/docs/kids-web-services/parent-verification-service), [Webhook](https://dev.epicgames.com/docs/kids-web-services/parent-verification-service/set-up/pv-service-configure-webhook), [Verification response](https://dev.epicgames.com/docs/kids-web-services/parent-verification-service/set-up/pv-service-configure-verification-response). Implementation follows the contract copied by the user; the URLs were checked but their contents were unavailable to this session.
