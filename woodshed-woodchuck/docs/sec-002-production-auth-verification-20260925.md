# Executive Result

**SEC-002 BLOCKED** — production limiter confirmed disabled; Render compatibility repair implemented locally, not deployed or enabled.

Date: 2026-09-25. This is not a production launch clearance or a DONE result.

The reviewed authentication/session implementation provides production secret and cookie guards, revocable signed sessions, credential-entry throttling, and sanitized application logging. The follow-up authorizes a small code repair: Render production now selects a strictly validated platform `CF-Connecting-IP`, independently of Uvicorn's X-Forwarded-For rewriting; non-Render proxy safeguards remain. Redis, limits, HMAC keys and outage behavior are unchanged. Partial protections and original test results are retained below.

**New operator-supplied production evidence:** `LOGIN_RATE_LIMIT_MODE=off`, `LOGIN_RATE_LIMIT_REQUIRED=false`, Redis URL present, trusted proxy CIDRs unset, no custom dashboard `FORWARDED_ALLOW_IPS`, and start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Production login throttling is therefore disabled, a confirmed launch blocker under the SEC-002 standard. The platform default is wildcard forwarding, not an empty value. No independent Render control-plane access was used. Redis operation, deployed revision, secret policy, revocation schema and authenticated logout remain EXTERNAL PRODUCTION STATUS UNKNOWN. Full intended October beta remains blocked until approved activation and verification.

Two public GETs verified HTTPS responses and the attributes of an anonymous session cookie. No production login, credential submission, diagnostic backend probe, database access, environment change, deployment, or Redis creation occurred. No secret or cookie value was printed.

Scope safety: application `/home/geph/Training_scripts-sec002/woodshed-woodchuck`; branch `security/sec-002-production-auth-20260925`; HEAD and expected baseline `b1279cff21d16ca81528cf3dfa9768b84680f42e`. The original verification added only this report. This explicitly authorized follow-up changes `app/login_limits.py`, `tests/test_login_limits.py`, `tests/test_session_hardening.py`, and this report. Nothing was staged, committed, pushed, merged, deployed, or changed in Render. Excluded worktrees were not used. Disposable tests use `/tmp/ww-sec002-render.JubVhR` and the previously isolated interpreter under `/tmp/ww-sec002-20260925.TCmxbR`.

Classification meanings: VERIFIED means the stated behavior was observed at the specified boundary; CODE VERIFIED / PRODUCTION UNKNOWN means source/local evidence exists but live enforcement is not attested; PARTIAL identifies an actual coverage/operations limitation. Unknown production state is not itself proof of an exploitable defect.

| Required item | Classification | Evidence and limit |
| --- | --- | --- |
| Production mode enforcement | CODE VERIFIED / PRODUCTION UNKNOWN | Render/APP_ENV detection and actual import guards tested; live process/revision not inspected. |
| SESSION_SECRET enforcement | CODE VERIFIED / PRODUCTION UNKNOWN | Known development key, missing/short/whitespace keys rejected in production; live policy/entropy unknown. |
| Secure cookie enforcement | CODE VERIFIED / PRODUCTION UNKNOWN | Production startup guard tested; live cookie's Secure flag VERIFIED, but deployment guard/revision unknown. |
| Session lifetime | CODE VERIFIED / PRODUCTION UNKNOWN | 14-day authenticated age check; live cookie Max-Age=1209600 VERIFIED; authenticated expiry untested live. |
| Session revocation | CODE VERIFIED / PRODUCTION UNKNOWN | Replay, account switch, legacy retirement and DB outage tests pass; deployed schema/workers unknown. |
| Student login rate limit | BLOCKER | Code covered; operator confirms production off/false. |
| Verifier login rate limit | BLOCKER | Login/invitation code covered; operator confirms production off/false. |
| Admin login rate limit | BLOCKER | Five/IP/window in code; operator confirms production off/false. |
| Contest-admin login rate limit | BLOCKER | All ten credential-entry routes covered; operator confirms production off/false. |
| Redis atomicity | CODE VERIFIED / PRODUCTION UNKNOWN | One Lua EVAL serializes counters; real-Redis tests skipped here, live backend unqueried. |
| Limiter fail-closed behavior | CODE VERIFIED / PRODUCTION UNKNOWN | Enabled/required configuration and backend errors yield 503; explicit off/false bypass remains supported. |
| Trusted-proxy parsing | VERIFIED | Source review, existing local tests and supplemental edge checks; this is parser verification only. |
| Production proxy configuration | PARTIAL | Operator supplies current start command/dashboard settings; Render's public-header contract verified in official documentation. Repaired source not deployed; effective header path untested on this service. |
| Production Redis configuration | PARTIAL | URL presence confirmed by operator; connectivity/ACL/expiry/shared operation unknown; enforcement disabled. |
| Logout | CODE VERIFIED / PRODUCTION UNKNOWN | Student, verifier, site admin and contest-admin replay checks pass locally; family flow reviewed, PostgreSQL tests skipped. |
| Session-version invalidation | CODE VERIFIED / PRODUCTION UNKNOWN | Version increment, inactive/deleted profile and page mismatch denied locally. |
| Sensitive logging | CODE VERIFIED / PRODUCTION UNKNOWN | Default Uvicorn INFO/TRACE and browser logging tests pass; Render ingress/APM/provider logs unknown. |
| CSRF posture | PARTIAL | Lax cookie plus selective CSRF tokens/origin checks; several cookie-authenticated forms lack explicit CSRF protection. |
| Operational cleanup of revoked sessions | PARTIAL | Expiry index and purge helper exist; no scheduled caller in repository or attested production job. |

# Architecture Summary

The app uses Starlette signed, timestamped client-side cookies wrapped by [RevocableSessionMiddleware](../app/session_revocations.py). Cookies are authenticated, not encrypted. A random authenticated-session nonce and issuance time are included; only retired nonce hashes are stored server-side in SQL. Student identity additionally binds to the profile's current session version. Site/contest administrator sessions contain separate HMAC fingerprints of their configured tokens, not the raw admin tokens. Parent/director access has additional consent-bound, hashed, expiring server credentials.

Student and ordinary verifier PINs are four digits, hashed with salted scrypt (`N=16384`, `r=8`, `p=1`, 32-byte output); verification uses constant-time digest comparison. This makes operational online throttling important. See [security.py](../app/security.py), [accounts.py](../app/accounts.py), and [verifiers.py](../app/verifiers.py).

[main.py:82–108](../app/main.py) validates secret/cookie configuration at import and installs session/logging middleware. Limiter configuration is different: startup emits only a sanitized status warning, without probing Redis or refusing startup. Credential attempts enforce current limiter settings. A running/healthy process therefore does not establish that login protection is enabled.

# Auth Route Matrix

Limits below apply **only when the limiter is active**. S = student, V = verifier: 10 attempts per normalized identifier and 100 per source IP, per 900-second window. A = site admin and C = contest admin: separate 5/IP/900-second buckets. All attempts, including successful credentials, consume quota. Global logout is `POST /account/logout`, which clears the entire cookie session and retires its old nonce.

| Auth class | Route | Limiter | Limit | Session identity | Logout |
| --- | --- | --- | --- | --- | --- |
| Student: Woodchuck ID + PIN | `POST /account/login` | `student` | S | `woodchuck_profile_id`, `woodchuck_session_version`; new page generation | Global logout |
| Student registration: newly selected PIN | `POST /account/create` | None | Registration/age checks; not an existing-account PIN check | Same student keys | Global logout |
| Trusted verifier, including ordinary band-director role: email + PIN | `POST /trusted-verifiers/login` | `verifier` | V | `trusted_verifier_id` | `POST /trusted-verifiers/logout` or global |
| Verifier invitation: secret link + new/existing verifier PIN | `POST /trusted-verifiers/invitations/{token}/accept` | `verifier` | V, invitation email; invalid token uses empty identifier | `trusted_verifier_id` | Verifier or global logout |
| Site administrator: `SITE_ADMIN_TOKEN` + CSRF | `POST /admin/login` | `admin` | A | `site_admin_fingerprint`; CSRF token regenerated | `POST /admin/logout` + CSRF, or global |
| Contest administrator: `X-Contest-Admin-Token` | `GET /contests/admin` | `contest_admin` on credential entry | C | `contest_admin_token_fingerprint` | Global logout; no dedicated contest logout route |
| Contest administrator: same header or existing fingerprint | `POST /contests/admin/band-directors` | Same guard | C on credential entry | Same fingerprint | Global logout |
| Contest administrator | `POST /contests/admin/band-directors/{profile_id}/revoke` | Same guard | C on credential entry | Same fingerprint | Global logout |
| Contest administrator | `POST /contests/admin/teams/{team_id}/moderation` | Same guard | C on credential entry | Same fingerprint | Global logout |
| Contest administrator | `POST /contests/admin/team-reports/{report_id}/resolve` | Same guard | C on credential entry | Same fingerprint | Global logout |
| Contest administrator | `POST /contests/admin/finalize-current` | Same guard | C on credential entry | Same fingerprint | Global logout |
| Contest administrator | `POST /contests/admin/finalize-due` | Same guard | C on credential entry | Same fingerprint | Global logout |
| Contest administrator | `POST /contests/admin/readiness` | Same guard | C on credential entry | Same fingerprint | Global logout |
| Contest administrator | `POST /contests/admin/rollover` | Same guard | C on credential entry | Same fingerprint | Global logout |
| Contest administrator, header required every time | `POST /contests/weeks/{week_start}/finalize` | `contest_admin` every call | C | No session key set here; existing contest cookie alone insufficient | No persistent session from this route; presenting the real header is fresh authentication |
| Parent consent request: parent email | `POST /family/request` | `student` | S keyed by parent email | None; starts permission workflow | N/A |
| Private director link request: ID + email | `POST /family/director-access` | `verifier` | V + five-minute link issuance interval | None | N/A |
| Private director: emailed secret + verifier PIN/explicit acceptance | `POST /family/director-open/{token}` | `verifier` | V keyed to literal `private-director`, shared across private directors | `child_director_permission`, `child_director_secret`; 30-minute DB credential | `POST /family/logout` + CSRF, or global |
| Parent access link request: ID + email | `POST /family/parent-access` | `verifier` | V + five-minute link issuance interval | None | N/A |
| Parent: one-use emailed capability + CSRF/confirmation | `POST /family/parent-access/{token}` | None | 256-bit random secret, 30-minute expiry, one-use consumption, consent checks | `child_parent_consent`, `child_parent_secret`; 30-minute DB credential | Family or global logout |
| Parent-authorized new child activation | `POST /family/activate/{token}` | None | Bound activation capability, approved verification/consent, CSRF; existing account must already match | Student keys only for newly created child | Global logout |

Primary route evidence: [account_routes.py:162,276,338](../app/account_routes.py), [verifier_routes.py:448,494,756](../app/verifier_routes.py), [membership_routes.py:209–235](../app/membership_routes.py), [contest_admin.py:71–84,159–368](../app/contest_admin.py), [contests.py:3486–3506](../app/contests.py), [family_routes.py](../app/family_routes.py).

All ordinary PIN/admin-token entry points identified call the intended limiter. Band-director authorization is an account capability or verifier relationship, not another unprotected password login. Private director entry uses the verifier limiter but a shared identifier, creating an availability/lockout hardening issue.

Explicit omissions: parent capability redemption, account creation, and child activation do not call `enforce_login_limit()`. These do not expose an unthrottled existing-account PIN guess: parent redemption uses a one-use 32-random-byte token; activation is capability/consent-bound; creation selects new credentials. Additional abuse throttling is reasonable but these are not demonstrated P0/P1 credential bypasses. Capability URLs still need log protection.

Other credential boundaries reviewed: `/account/delete` requires an authenticated current profile, exact ID/PIN/confirmation, and its own persisted five-failure/15-minute throttle; it does not establish a login. Membership invitation redemption requires existing account identity and CSRF. KWS test/production webhook and response routes verify provider signatures/results and update verification state; they do not authenticate a browser as a parent/student. Provider billing callbacks likewise are not interactive administrator login endpoints. No score-security assessment was performed.

# Session Security

**Production detection:** [session_config.py:8–25](../app/session_config.py) returns production if trimmed/lowercased `RENDER` equals `true` **or** `APP_ENV` equals `production`. `APP_ENV=development` cannot override `RENDER=true`. With neither marker, local fallback behavior is allowed; hostname/HTTPS alone does not activate production mode.

At production import, `SESSION_SECRET.strip()` must be at least 32 characters and must not equal the known development sentinel. Missing, whitespace-only, short, and sentinel-with-padding settings raise `RuntimeError`, preventing application startup. Signing preserves the exact supplied key after validation. This is a length/default-key policy, not an entropy test: humans must ensure a randomly generated confidential key. Do not rotate it during this verification.

`SESSION_COOKIE_SECURE` defaults to true in production, false otherwise. Only lowercased `1`, `true`, or `yes` enable it; this function does not strip whitespace. False/empty/misspelled values in production raise `RuntimeError` at startup.

| Cookie property | Current code | Live observation |
| --- | --- | --- |
| Name | Starlette default `session` | `session` |
| Secure | Production enforced | Present |
| SameSite | Explicit `lax` | `lax` |
| HttpOnly | Starlette sets it | Present |
| Max-Age | `1209600` seconds (14 days) | `1209600` |
| Path | Starlette default `/` | `/` |
| Domain | Not explicitly set; host-only browser behavior | Attribute absent |

Inspected installed Starlette 0.47.3 middleware, compatible with pinned FastAPI 0.116.1; production's installed dependency versions were not read. Anonymous CSRF-cookie age is not proof of authenticated server-side expiry. Authenticated sessions must also carry an integer issuance time within the absolute 14-day lifetime, with future/invalid times rejected. Parent/director credentials expire in 30 minutes independently of the cookie.

Signing-key changes invalidate cookies and affect shared HMAC-derived identifiers/fingerprints; not performed. Admin token changes invalidate their respective stored fingerprints. Neither the signing secret nor admin token values were inspected.

# Login Throttling

Source: [login_limits.py:17–19,75–93,130–156](../app/login_limits.py).

| Setting | Code expectation / consequence |
| --- | --- |
| `LOGIN_RATE_LIMIT_MODE` | Trimmed/lowercased `off` (default), `memory`, or `redis`; other values cause protection failure. |
| `LOGIN_RATE_LIMIT_REQUIRED` | Defaults false; accepts 1/true/yes and 0/false/no after normalization; invalid values fail. True requires Redis mode even outside production. |
| `LOGIN_RATE_LIMIT_REDIS_URL` | Must be present in Redis mode, with `redis://` or `rediss://` scheme accepted by backend construction; never expose its value. An unused saved URL does not turn on off mode. |
| `FORWARDED_ALLOW_IPS` | Repaired source: irrelevant to Render limiter identity; retain normal platform default. Non-Render active production still requires the explicit empty string and no contrary Uvicorn override. |
| `LOGIN_TRUSTED_PROXY_CIDRS` | Parsed only for active mode; invalid networks/universal ranges fail. Render needs no CIDRs and uses CF identity. Outside Render, empty trust uses raw peer buckets and may collapse traffic behind a proxy. |

There are 10 direct limiter call sites: student login; verifier login; verifier invitation acceptance; site-admin login; contest-admin guard; header-only contest finalization; family request, director-access, director-open, and parent-access. The contest guard covers nine routes, so these resolve to **18 route/method entry points**. The source scan found no additional direct callers.

The window is fixed from the first attempt in each bucket, 900 seconds; it is not a sliding window. The next attempt after the allowance is blocked. Successes do not reset counters. Requests first consume IP quota, then identifier quota; an already-blocked IP never reserves account quota. Account rejection still consumes IP quota. Student identifiers are trimmed/uppercased; verifier identifiers trimmed/lowercased. Keys contain kind/category plus keyed SHA-256 digests, not raw identifiers/IPs/PINs. Workers must share backend and signing key for shared limits.

Active limiter/config/backend exceptions return generic **503**, without connection details or memory fallback. Exhaustion returns **429** and integer-ceiling remaining seconds in `Retry-After`. Existing authenticated contest sessions bypass credential-entry limiting on the nine guard routes; the header-only finalization route always consumes quota. This avoids treating every authenticated page visit as a credential guess.

**Off mode is a confirmed production gap:** `off` + required false permits production authentication without consulting Redis or proxy trust. Operator evidence now confirms those production values. Startup merely reports `disabled`; it does not abort. For intended beta protection, the human release gate must require Redis/required true after the repair is merged, deployed under approval, and validated. SEC-002 remains BLOCKED. No production activation was attempted.

# Redis Behavior

[RedisBackend and Lua](../app/login_limits.py) request one-second socket connection and operation timeouts, `Retry(NoBackoff(), 0)`, and decoded responses. There is no application retry/fallback. Backend clients are cached per mode/URL in each worker; shared Redis keys provide the cross-worker counter state.

Each consume uses a single Lua `EVAL`. `INCR`, expiry initialization/repair, threshold checking and IP-before-account ordering execute without concurrent interleaving on the Redis server. First use applies a 900-second expiry; attempts do not extend an existing expiry; missing TTL is repaired. Returned PTTL determines Retry-After. Lua atomic execution is not transactional rollback on a Redis command error; a partial reservation may remain on errors, while the HTTP attempt still fails closed. A compatible backend and ACLs must permit EVAL/INCR/EXPIRE/PTTL.

Configuration nuance: installed redis-py 6.4.0 `ConnectionPool.from_url()` gives parsed URL options precedence over keyword arguments. Privately verify that the live URL has no query options overriding the intended timeouts/TLS behavior; a hardcoded default alone does not attest effective connection configuration. No live URL was opened or printed.

`GET /admin/security/rate-limit` requires site-admin authentication and returns no-store JSON. `protection_status()` reports normalized mode/configured/required/state, always with `production_verified=false`. Its default backend check runs the same Lua/ACL path on one random `ww:login:v1:probe:*` key with a **two-second TTL**. It does not consume user/account quota, but it is a small Redis write, not a read-only PING. `check_backend=False` checks configuration only and cannot prove reachability. A human already authorized for this endpoint can run one probe and retain sanitized status. This task did not access it.

`backend_operational` proves that one script call succeeded, not source-IP accuracy, policy activation across every worker, sustained availability, or real account/IP enforcement. Real Redis integration tests were safely skipped because no explicitly authorized disposable backend existed; none was created.

# Proxy/IP Trust

**Follow-up root cause and repair, 2026-09-25.** Original source assumed `request.client` was a raw socket peer and rejected any nonempty `FORWARDED_ALLOW_IPS` in active production. The operator's unchanged start command enables Uvicorn's default proxy middleware. Render supplies `FORWARDED_ALLOW_IPS=*`; not custom-setting it in the dashboard does not remove the platform default. Simply switching mode to Redis under the original source therefore returns generic 503 on credential attempts, even if Redis works. Simply deleting the guard while continuing to use `request.client` would be unsafe.

Render documents both `RENDER=true` and Python's wildcard forwarding default. [Render default environment variables](https://render.com/docs/environment-variables). Uvicorn documents enabled-by-default proxy processing and the forwarding trust setting. [Uvicorn settings](https://uvicorn.dev/settings/). Inspection of the installed, pinned Uvicorn 0.35.0 implementation shows wildcard trust chooses the **leftmost** X-Forwarded-For entry and rewrites ASGI `scope['client']`. The new integration regression demonstrates this rewrite, rather than only mocking `request.client`.

Render's official public-service guidance states that public requests traverse Cloudflare/Render ingress, Cloudflare overwrites `CF-Connecting-IP`, and X-Forwarded-For can retain a caller-supplied prefix. It recommends the former for client identity and distinguishes private-network traffic from public ingress. [Render public-service client-IP guidance](https://render.com/articles/host-pocketbase-on-render)

**Implemented decision:** use that public-ingress contract for the limiter only when normalized `RENDER=true`. This is the same platform marker that already makes session policy production; `APP_ENV=production` alone cannot enable header trust. No caller header selects the platform mode. No new environment flag, CIDR list, migration, dependency or start-command change is required for this Render deployment.

In [login_limits.py](../app/login_limits.py), the Render branch:

- Requires exactly one `CF-Connecting-IP` header occurrence. Duplicate values, even identical duplicates, are rejected.
- Allows surrounding HTTP space/tab only, then parses one bare IPv4/IPv6 address. Rejects empty/missing values, comma chains, hostnames, ports/brackets, CIDR notation, IPv6 scope identifiers, invalid numeric forms and line breaks. Canonicalizes IPv6 and maps IPv4-mapped IPv6 to IPv4 so textual variants share a bucket.
- Never reads X-Forwarded-For, Forwarded, or `request.client` for the Render rate-limit identity. No raw-peer, forwarded-header or shared-bucket fallback occurs.
- Raises on missing/malformed/ambiguous platform identity; the existing limiter boundary returns generic 503 before consuming quota or checking credentials. The exception/header payload is not logged or returned. This intentionally sacrifices login availability if the platform contract fails, rather than allowing a chosen fallback bucket.
- Allows Render's normal wildcard forwarding environment because the rewritten peer is irrelevant to limiter keys. Redis mode and all existing required/backend failure checks still apply. Empty CIDRs are accepted; optional configured CIDRs remain syntax/universal-range validated by settings, but are not used to select the Render identity.

The ordinary Render start command can remain `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Preserving its existing scheme handling avoids an unnecessary HTTPS/link/CSRF behavior change. This is not a claim that rewritten `request.client` is safe for a future unrelated security decision: this repair removes that dependency specifically from login limiting.

**Trust boundary:** this is safe for public clients under Render's documented overwrite guarantee. The application cannot cryptographically authenticate a syntactically valid CF header delivered through a direct/private path. Do not set `RENDER=true` on a self-hosted deployment or expose this listener through an untrusted ingress that bypasses the Render/Cloudflare boundary. Before activation, confirm intended public/custom hosts use that ingress and that any same-workspace private callers are trusted. A request on such an alternate path without the header fails 503; a malicious internal caller forging a valid header is outside the public-ingress guarantee. No CIDRs are invented to conceal this boundary. Local tests cannot prove the deployed provider path.

**Outside Render**, existing behavior remains:

- Active production requires Redis and explicitly empty `FORWARDED_ALLOW_IPS`, plus no CLI/wrapper proxy rewriting. Non-production behavior is unchanged.
- Missing/malformed raw peer uses `unknown-peer`. Untrusted peers cannot supply XFF; `CF-Connecting-IP` is ignored.
- Only an explicitly trusted peer enables XFF parsing. Maximum 20 entries; malformed/empty chain entries or an oversized chain fall back to the peer. Walk right to left, skipping only trusted proxies, and choose the first untrusted address; all-trusted chains fall back to the peer.
- IPv4-mapped addresses normalize. Invalid CIDRs and IPv4/IPv6 `/0` are rejected; other broad networks are not categorically rejected and must still be reviewed for exclusive proxy ownership.

The original report's proposed raw-peer Render launch change (`--no-proxy-headers`, empty forwarding variable, provider ingress CIDRs) is **superseded for this Render repair**. It remains relevant only to the separate non-Render CIDR architecture. No production settings were changed.

# Logout and Revocation

[session_revocations.py:24–218](../app/session_revocations.py) includes student, verifier, both administrator fingerprints, and parent/director permission/secret fields in authentication facts. A student session's version is also part of those facts.

On initial authentication or changed authentication facts, the middleware issues `secrets.token_urlsafe(32)` and current integer time. Before acknowledging a changed authenticated identity or logout, it persists the old nonce's domain-separated SHA-256 digest in `revoked_browser_sessions`, with revocation time and expiry at **retirement + 14 days + one hour**. Retirement is idempotent; the primary key and IntegrityError handling resolve concurrent insert attempts. A conflicting second identity transition from an already-retired nonce returns 401 and suppresses Set-Cookie.

Every incoming authenticated session checks age and revocation before route use. Retired/expired sessions become anonymous. Middleware suppresses cookie-clearing output from rejected stale requests so they do not erase a newer session. Thus public routes may return ordinary anonymous content/200; protected routes deny access. Unchanged sessions are not refreshed on ordinary responses, reducing delayed-response cookie restoration. An older response that changed incidental session data can still carry an older signed cookie; its retired nonce will be rejected on the next request.

Legacy signed cookies without a nonce get a deterministic HMAC retirement key based on canonical authentication facts. This prevents untouched pre-upgrade cookies from evading logout. Other legacy sessions with identical facts may be invalidated together. The original signed-cookie timestamp still limits replay age.

| Logout route | Cookie/session behavior | Server retirement and failure behavior |
| --- | --- | --- |
| `/account/logout` | Clears every session key; a valid nonempty incoming session receives expiry Set-Cookie | Retires old authenticated nonce; replay denied. Anonymous logout needs no revocation row. |
| `/trusted-verifiers/logout` | Removes verifier identity; remaining student/admin identities may remain | Retires whole previous nonce; rotates if other auth remains. Success response waits for retirement. |
| `/admin/logout` | CSRF required; removes only site-admin fingerprint; CSRF/other identities can remain | Replaces cookie instead of necessarily deleting it; previous nonce retired. |
| Contest administrator | No dedicated route; global account logout clears contest fingerprint | Old cookie denied; supplying the actual admin header can legitimately authenticate again. |
| `/family/logout` | CSRF required; removes parent/director session keys | First clears matching DB session hashes/expiries, then middleware retires old browser nonce; other identities may remain. |

SQLAlchemy revocation lookup failures return 503/no-store without Set-Cookie or protected route execution. Retirement persistence failures return 503/no-store, suppress cookie output, and do not acknowledge successful logout. Unexpected uncaught infrastructure errors propagate as errors, not successful authentication/logout; normal Uvicorn exception logging is sanitized. If retirement fails, the old credential can remain usable after recovery: the client must treat logout as unsuccessful. Existing UI handlers keep Guest tools closed or show failure instead of claiming success.

Revocation is not cancellation of an already-running authorized request, and the response-time retirement transaction is not atomic with every route's domain transaction. No stronger claim is made. Missing revocation schema causes authenticated verification to fail, rather than silently ignoring retirement.

Operational requirements: migration `v2r3s4t5u6v7` (or a later compatible revision) must be applied and old workers that ignore revocation must be retired. [Migration](../migrations/versions/v2r3s4t5u6v7_revoke_browser_sessions.py), [release instructions](session-revocation-release.md).

`purge_expired(session, now=...)` performs a strict `expires_at < now` delete; the caller must commit. It has an expiry index and a passing retention regression, but **no timer, CLI job, request cleanup, or scheduler is provided**. A reviewed maintenance job/owner is needed. Not purging causes growth, not immediate replay acceptance. No production cleanup/database mutation was performed.

# Session-Version / Identity-Change Protection

[current_profile()](../app/account_routes.py) retrieves the profile from SQL and checks active status and the signed `woodchuck_session_version` against `profile.session_version`. Missing profile, inactive/deleted status, non-integer version or mismatch removes student identity/version and denies authentication. A missing version defaults to zero for legacy sessions. Non-integer profile IDs are unauthenticated. Python `isinstance(..., int)` permits booleans here, but these values are inside a signed cookie, not accepted as unsigned identity selectors.

Deletion sets deleted status, destroys the PIN hash and increments the version; consent withdrawal increments it once while preserving the account. Explicit version increments invalidate existing browsers. The signed cookie itself must validate first. A supplied `X-Woodshed-Account` header that mismatches the authenticated account returns 401; its presence is not mandatory, and it cannot establish identity. [Deletion](../app/account_deletion.py), [consent withdrawal](../app/child_authorization.py).

Verifier sessions validate the verifier row exists; there is no equivalent verifier session-version/status field checked by `current_verifier()`. Relationship/consent authorization is checked separately. Changing a PIN hash alone is not a generic global session invalidation mechanism. Admin sessions validate their current independent token fingerprints on protected use.

| Transition | Observed/code behavior |
| --- | --- |
| Anonymous to student | New authenticated nonce/issued time and page generation. |
| Student A to B | Retire A's nonce; new nonce and page generation for B; A replay rejected. |
| Logout then same student login | Independent nonce; old cookie stays retired; other independent browsers remain valid. |
| Same student logs in again without logout | Authentication facts unchanged: nonce/absolute issuance time stay the same, page generation changes. This is not a forced server-session rotation. |
| Verifier A to B; add/remove an admin identity | Changed facts retire old combined-session nonce and issue a new one if authentication remains. |
| Same verifier/admin authenticates again with unchanged facts | No guaranteed nonce rotation; site-admin login does regenerate its CSRF token. |
| Late ordinary response after identity change | No unchanged-cookie refresh; retired-cookie replay cannot restore access. Student browser code also rejects stale response bodies. |

[session-boundary.js](../static/js/session-boundary.js) uses a local change counter, Web Locks, account header, and page generation preflight before account script execution. It checks both fetch completion and subsequent JSON/text reads and stops old/bfcache-restored pages. Its explicit authentication transition list is student create/login/logout; do not generalize that browser coordination to every verifier/admin/family form. The server nonce boundary covers their listed identities independently. Arbitrarily concurrent anonymous login requests have no old authenticated nonce to serialize; the browser lock is relevant, not a claim of universal server ordering.

# Sensitive Logging

[safe_logging.py](../app/safe_logging.py), installed early in [main.py](../app/main.py), filters default Uvicorn access/error/ASGI loggers. It logs route templates instead of raw paths, strips query values and source-IP text, uses `<unmatched>` for unknown paths, omits ASGI TRACE payloads and WebSocket paths, and substitutes exception type plus frame filename/line/function for exception messages, SQL parameters, source lines, locals and chained exceptions. Correlation IDs and duration/status remain.

The inspected request/login paths do not intentionally log PINs, passwords, cookies, invitation capabilities, Authorization headers, parent access/consent secrets, Redis URLs, or payment credentials. Limiter errors are caught without logging their exception payload. Email warnings use record IDs/fixed delivery codes; analytics warnings use operation/error type. SQLAlchemy engine construction does not enable SQL echo. Browser account sync warnings were reviewed; no intended credential dump was found. Existing browser capability-log regressions passed.

Important scope limits: the sanitizer is attached to named Uvicorn loggers, not a universal scrubber for arbitrary new logger calls or external agents. Application access/error tests passed at INFO and TRACE, including real loopback requests and synthetic secret-bearing headers, URLs, bodies, cookies and exceptions. Parent/payment paths were inspected, not exercised end to end with providers. Ingress/CDN/APM, platform access logs, SQL debug configuration and email provider retention remain external checks. A future raw URL/body/header log could leak secrets, particularly capability tokens in paths/query strings. Intended credential responses/emails are not logging evidence.

# CSRF Posture

**PARTIAL; do not describe the app as “CSRF safe.”** The observed cookie explicitly uses SameSite=Lax, which restricts ordinary cross-site POST cookie attachment but is not comprehensive request authentication or protection against same-site sibling origins.

[site_admin.check_csrf()](../app/site_admin.py) requires a session-bound random token and constant-time match. It checks Origin when supplied, using `PUBLIC_BASE_URL`, then `RENDER_EXTERNAL_URL`, then request-host fallback. Normalization compares hostname/port; it is not a full scheme-bearing origin tuple. Missing Origin is accepted when the token is correct. `Origin: null` additionally requires `Sec-Fetch-Site: same-origin` and matching request-derived endpoint. No general Referer check was found.

Explicit token checks cover site-admin login/logout and membership/billing administration, membership writes, age/support forms, and all family POSTs via their shared form helper. Site-admin CSRF unit tests passed. Student and verifier login/logout, verifier invitation acceptance, several account/verifier writes, and contest-admin forms do not uniformly use this helper. Account deletion instead requires credential reconfirmation plus its own failure limit. State APIs use JSON; no application-wide CORS relaxation was found, but JSON conventions and client `credentials: same-origin` do not substitute for server CSRF checks. Standard login forms use FormData and can be submitted by other sites.

Residual material risks include login CSRF/account confusion on tokenless login endpoints, and cookie-authorized writes from same-site/untrusted sibling origins. Lax reduces ordinary cross-site form risk for existing sessions. Contest-admin POSTs relying on an existing fingerprint lack an explicit CSRF token/origin check; header-only token authentication is a different boundary. No victim-browser exploit or P0/P1 cross-site authorization bypass was demonstrated in this run. Treat as a scoped hardening/risk-acceptance item, not proof that production is safe. If the deployment includes untrusted sibling origins or a concrete browser exploit is demonstrated, reassess severity before launch.

Most browser mutations use POST/PUT/DELETE. Auth-adjacent GET exceptions include contest-admin header authentication establishing a session, anonymous CSRF-token issuance, and signed KWS verification responses committing provider verification state. Those callbacks are not browser session login and require separate provider authenticity checks. No production callback or state-changing request was made.

# Production Evidence

**CODE EVIDENCE:** reviewed baseline files, existing tests, installed dependency implementations and historical deployment notes. The follow-up adds the Render-specific client-IP branch and focused regression tests; these changes are local and uncommitted. Source presence does not attest deployed behavior.

**LIVE/PRODUCTION EVIDENCE:** two sequential, ordinary public GETs to the repository-documented origin `https://woodshed-woodchuck.onrender.com`, using no cookies or credentials and displaying only allowlisted response metadata:

| UTC timestamp, 2026-09-25 | Request | Observed result |
| --- | --- | --- |
| 11:05:54.282712 | `GET /guest` | HTTPS 200; HTML; Cache-Control no-store; no Set-Cookie. |
| 11:05:54.453055 | `GET /admin/login` | HTTPS 200; HTML; Cache-Control no-store; cookie `session`, Secure, HttpOnly, SameSite=lax, Max-Age=1209600, Path=/, no Domain/Expires attribute. |

No redirect occurred for these HTTPS URLs. Strict-Transport-Security was not present in these two responses; HTTP-to-HTTPS redirection and other/custom hosts were not checked. Cookie values and page CSRF values were never printed or retained in the report. No login attempt or rate quota was consumed by these GET routes in the reviewed code. Ordinary server access logging may record the visits.

**OPERATOR-SUPPLIED PRODUCTION EVIDENCE (follow-up):**

| Setting/status | Supplied finding |
| --- | --- |
| `LOGIN_RATE_LIMIT_MODE` | `off` |
| `LOGIN_RATE_LIMIT_REQUIRED` | `false` |
| `LOGIN_RATE_LIMIT_REDIS_URL` | SET; value hidden |
| `LOGIN_TRUSTED_PROXY_CIDRS` | NOT SET |
| `FORWARDED_ALLOW_IPS` dashboard override | Not custom-set; Render documents runtime default `*` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |

These findings supersede the initial report's unknown limiter-settings/start-command status. They were provided by the operator, not independently fetched by this agent. The platform default plus Uvicorn source establishes the compatibility cause; it is not a live request-header capture.

**EXTERNAL STATUS UNKNOWN / EXTERNAL PRODUCTION STATUS UNKNOWN:** runtime marker/process attestation, deployed commit/dependencies, SESSION_SECRET policy/entropy, Redis health/ACL/expiry/all-worker sharing, actual CF-header overwrite/presence through this deployment's intended public/custom hosts and any private ingress, SQL migration/permissions/all-worker retirement, authenticated expiry/logout, provider logging and maintenance schedule. No production login/header-spoof test was performed in this follow-up.

No Render-specific configuration/runtime connector was available among the exposed tools. Existing unrelated deployment connectors are not evidence of access to this Render service. No environment file, Redis connection string, primary-worktree credential store, or real account was opened or used. No live status endpoint was accessed without admin authorization.

Historical evidence: [session-revocation-release.md](session-revocation-release.md) and [September security report](security-round1-server-economy.md) described an off/false rollout and deferred activation. The new operator findings now establish current off/false independently of those old notes. Historical service pricing/plan requirements were not revalidated in this task.

# Test Evidence

**Follow-up repair run:** **131 passed, 0 failed, 5 skipped**, one existing anyio deprecation warning, 82.51 seconds. Used the previous isolated Python environment and fresh temporary data/pycache under `/tmp/ww-sec002-render.JubVhR`, with no inherited production credentials. No full suite was run. Command:

```sh
env -i \
  PATH=/tmp/ww-sec002-20260925.TCmxbR/venv/bin:/usr/bin:/bin \
  LANG=C.UTF-8 TMPDIR=/tmp/ww-sec002-render.JubVhR \
  PYTHONPYCACHEPREFIX=/tmp/ww-sec002-render.JubVhR/pycache \
  DATABASE_URL=sqlite:////tmp/ww-sec002-render.JubVhR/default.db \
  SESSION_SECRET=sec002-local-synthetic-session-key-at-least-32 \
  SESSION_COOKIE_SECURE=false LOGIN_RATE_LIMIT_MODE=off LOGIN_RATE_LIMIT_REQUIRED=false \
  /tmp/ww-sec002-20260925.TCmxbR/venv/bin/python -m pytest \
  -q -p no:cacheprovider --basetemp=/tmp/ww-sec002-render.JubVhR/pytest \
  tests/test_login_limits.py tests/test_session_hardening.py \
  tests/test_session_revocation.py tests/test_site_admin_csrf.py \
  tests/test_sensitive_logging.py
```

Added coverage includes IPv4, canonical IPv6 and mapped IPv4; valid student/verifier Render login with normal wildcard forwarding; malformed/missing/scoped/duplicate platform headers; no fallback or quota consumption on invalid headers; six credential-entry routes rejecting missing/malformed/duplicate identity; actual Uvicorn wildcard rewriting versus stable limiter buckets and distinct CF clients; all four limiter kinds failing closed on backend errors without logging; non-Render CF-header rejection, CIDR trust and universal-range rejection; Render memory/off-required rejection and unchanged off/false behavior.

Render tests deliberately substitute the deterministic local backend behind synthetic Redis settings; they verify routing/configuration/quota semantics, not live Redis connectivity. The same five existing real-Redis cases remain skipped because no explicitly disposable Redis was supplied. Redis Lua/backend production code is unchanged.

Test maintenance within this authorized repair: `test_login_limits.py` now redirects session revocation to its disposable DB, declares its synthetic student eligible, and gives the contest result stub the current keyword signature. The production off-mode session fixture in `test_session_hardening.py` now declares its student eligible. Thus the three relevant earlier fixture/mock failures are fixed and pass. The two old Guest payload failures are outside this repair and were not changed or rerun. Initial evidence below remains historical, not the result of the repaired-source run.

**Initial verification evidence (before repair):**

Execution was local only, with a new system-Python virtual environment in `/tmp/ww-sec002-20260925.TCmxbR`. The shell's default Python/pytest paths pointed into the excluded primary tree and were not executed. Installed this worktree's `requirements.txt` and pytest into the temporary environment with pip cache disabled. Relevant versions: Python 3.12.3; pytest 9.1.1; FastAPI 0.116.1; Starlette 0.47.3; Uvicorn 0.35.0; SQLAlchemy 2.0.54; redis-py 6.4.0; httpx 0.28.1; itsdangerous 2.2.0. These are local versions, not a production dependency attestation.

The runner used `env -i`, a synthetic SESSION_SECRET, `SESSION_COOKIE_SECURE=false`, limiter off/required false unless a test overrides it, an explicit disposable SQLite DATABASE_URL, and temporary pycache/pytest directories. Before pytest, it imported the app and created `Base.metadata` only in that disposable SQLite database, accommodating older fixtures that do not redirect every module's SessionLocal. No production/email/payment/Redis credentials were inherited. Pytest cache was disabled. No complete suite was run.

| Run | Exact results |
| --- | --- |
| Targeted existing Python selection below | **117 passed, 5 failed, 5 skipped**, 2 warnings, 90.05 seconds |
| Two selected parent/director PostgreSQL tests below | **0 passed, 0 failed, 2 skipped**, 1 warning, 1.98 seconds |
| `node --test tests/test_guest_boundary.js tests/test_sensitive_logging.js` | **22 passed, 0 failed, 0 skipped** |
| Supplemental inline SQLite/TestClient/source-IP verification | **15 scenarios passed, 0 failed** |
| Inline follow-up checks of observed Python failure causes | **4 scenarios passed, 0 failed** |

Existing Python selection (passed to `pytest.main()` with `-q -p no:cacheprovider --basetemp=/tmp/ww-sec002-20260925.TCmxbR/pytest`):

```text
tests/test_session_hardening.py
tests/test_session_revocation.py
tests/test_login_limits.py
tests/test_site_admin_csrf.py
tests/test_sensitive_logging.py
tests/test_account_deletion.py
tests/test_guest_boundary.py
tests/test_analytics.py::test_unauthenticated_adult_deleted_and_stale_sessions_are_not_observed
tests/test_analytics.py::test_unsigned_cookie_cannot_manufacture_identity
tests/test_security_authorization.py::test_private_admin_reads_reject_forged_roles_before_data_access
tests/test_security_authorization.py::test_real_admin_diagnostics_and_no_hidden_analytics_api
```

Additional selected tests, run with the same isolated interpreter/environment and pytest cache disabled:

```text
tests/test_private_practice.py::test_pin_only_wrong_adults_old_links_and_revocation
tests/test_private_practice.py::test_parent_session_expiry_age_and_withdrawal_preserve_data
```

The five skips in the main selection are real-Redis integration cases requiring the explicit disposable local Redis fixture (atomic multiprocess expiration, both dimensions' TTLs, two IP-poisoning cases, concurrent boundary reservation). No backend was created or borrowed. The two family cases require `WW_AGE_TEST_POSTGRES_URL`; none was supplied. Their PostgreSQL locking/end-to-end guarantees remain unexecuted here. The warnings concern anyio's deprecated portal alias and pytest assertion rewriting after bootstrap imports.

All five failures are retained as failures, not relabeled as passes:

| Failed test | Actual result / diagnosis |
| --- | --- |
| `test_session_hardening.py::test_production_limiter_off_allows_https_login_without_backend` | Login and secure-cookie checks reach the later assertion expecting `/account/state` 200. Its synthetic profile has no eligible age record; current code returns age-screen 403. Inline follow-up confirmed successful login followed by this 403, then an eligible profile's login/state/logout sequence with runtime production-mode limiter off. |
| `test_login_limits.py::test_invitation_acceptance_cannot_bypass_verifier_login_limit` | Expected invalid-PIN 400, received eligibility 403 because the fixture student has no age record. Eligible synthetic follow-up confirmed shared quota: nine failed verifier logins, invitation failure 400, next acceptance 429. |
| `test_login_limits.py::test_contest_valid_session_header_only_route_and_site_boundary` | Mock `contest_results_payload=lambda *args` rejects current `_include_private=True` keyword with TypeError after successful credential checking. Follow-up with a signature-compatible in-memory stub confirmed header required/valid-header acceptance; no real finalization was run. |
| `test_guest_boundary.py::test_guest_flags_or_stale_page_identity_cannot_authorize_writes[None-post-/arcade/plays-payload2]` | Request omits required `request_id`; schema validation returns 422 before the expected authorization 401. No authentication success or write was observed. No score-security investigation followed. |
| Same test with `[B-post-/arcade/plays-payload2]` | Same obsolete payload/422 ordering. Separate account-header mismatch scenario returned 401. |

These were evidence of fixture/mock drift, not demonstrated auth bypasses. Tests were not edited in the initial verification. The later authorized repair above fixes the three limiter/session fixture issues; the original run remains recorded as not green.

The 15 supplemental cases verified: student logout expiry header/replay; student A-to-B nonce rotation/replay; logout/re-login independence; same-identity login nonce preservation/page-generation rotation; verifier logout/replay; verifier identity switch; dedicated site-admin logout/replay; contest-admin global logout/replay; session-version invalidation; deleted-status invalidation; account/header mismatch; 20-versus-21 forwarded hops; malformed/all-trusted chains and malformed peers; IPv4-mapped addresses; IPv6 universal/malformed network rejection. Contest page rendering/status were stubbed locally to isolate authentication. No score/domain actions were exercised.

Four further inline scenarios established the age-fixture failure cause, runtime production-mode off/false behavior with an eligible synthetic profile, invitation/shared limiter behavior with eligible data, and the current header-only contest authentication boundary with signature-compatible result stubs. The runtime mode check reused an already-imported app; it is not a second secure-cookie startup test. Startup import tests and the live cookie observation provide that separate evidence.

# Blockers

**Confirmed launch blocker: production login throttling is disabled (`off`/`false`).** The new operator evidence meets the SEC-002 disabled-required-protection standard. A saved Redis URL alone provides no enforcement. No claim of an observed production account compromise is made.

Remaining SEC-002 launch gates:

1. **Production activation pending.** Merge and deployment of this repair must precede approved Redis/required enablement. No action was taken in Render.
2. **Original Render compatibility defect repaired locally.** Original code rejects Render's normal forwarding setting and would return 503 on enabling Redis. The new platform-header path removes that mismatch without accepting XFF as identity. Validate the deployed ingress/header contract before opening beta; no trusted ingress CIDRs are required for the Render path.
3. **Deployed session/revocation and secret-policy state unknown.** Unsafe/default secret, insecure cookies, missing effective revocation, old workers that ignore retirement, or fail-open behavior would be blockers if observed. Public cookie flags are positive but insufficient to attest those conditions.

Redis health, deployed ingress and session state remain verification gates. Partial CSRF coverage, cleanup scheduling, missing capability-redemption throttle and remaining Guest fixture drift are documented separately; no P0/P1 exploit was established for them here.

# Nonblocking Hardening

- Repair the two remaining Guest payload fixture cases and add focused parent/director limiter/logout and concurrency coverage in an authorized test change. The three limiter/session fixture cases are fixed in this repair. Keep real Redis/PostgreSQL tests in disposable environments.
- Extend explicit CSRF protection/origin validation consistently to cookie-authenticated forms, particularly login and contest-admin forms; review same-site sibling-origin exposure.
- Give private director credential entry a suitable per-identity bucket rather than a shared `private-director` identifier; consider explicit abuse limits on capability redemption/registration.
- Schedule and monitor revocation cleanup with a reviewed owner/retention policy; do not run it as part of this verification.
- Consider explicit nonce rotation on same-identity reauthentication and a verifier session-version/reset policy. Current ordinary identity changes and logout are protected.
- Consider a dedicated visible contest-admin logout action and stronger non-Render deployment validation against contrary CLI proxy rewriting. Render limiter identity is now independent of that rewrite.
- Keep Redis timeout/TLS options controlled and dependencies pinned/reproducible. Review missing HSTS on the observed responses under a separate transport-hardening decision.

# Required Human/Render Checks

Perform inspection first. **No configuration change, secret rotation, service creation, production database mutation, or deployment is authorized by this report.** If a change is needed, prepare the exact reviewed change and obtain explicit human approval. Do not run failure/outage/threshold tests against production or real accounts.

| Exact setting/status to inspect | Expected safe state / evidence to retain | Secret VALUE must remain hidden? |
| --- | --- | --- |
| Render deployed revision / `RENDER_GIT_COMMIT`, instance rollout and dependency versions | Reviewed code or a documented equivalent; all old web workers retired; known middleware/dependency versions. | Commit/version status is nonsecret. |
| Effective `RENDER` and `APP_ENV` | On Render, verify runtime `RENDER=true` to select both platform identity and production policy; `APP_ENV=production` alone retains the non-Render CIDR path. | No. |
| `SESSION_SECRET` presence/policy | Record present=yes, stripped length satisfies >=32=yes, not development sentinel=yes; confirm random generation/confidential storage privately. Do not rotate. | **Yes; no value, prefix, hash, or exact length needed.** |
| `SESSION_COOKIE_SECURE` and production startup behavior | Omitted under verified production marker or accepted true value; bad config rejected by deployed code. Record policy without injecting bad config into production. | No. |
| Canonical host/public HTTPS cookie after authorized test login | Secure, HttpOnly, SameSite=Lax, Path=/, expected lifetime, no unintended Domain; anonymous observed attributes already recorded. | **Cookie values/CSRF values hidden.** |
| `LOGIN_RATE_LIMIT_MODE` | Currently confirmed `off`; change to `redis` only after merged repair is deployed and activation approved. | No. |
| `LOGIN_RATE_LIMIT_REQUIRED` | Currently confirmed `false`; change to `true` together with Redis mode. No silent disablement on outage. | No. |
| `LOGIN_RATE_LIMIT_REDIS_URL` | Present=yes; correct shared intended backend; compatible scheme/TLS/network and no unexpected timeout/TLS query overrides. | **Yes; URL, host credentials and query values hidden.** |
| One authorized `GET /admin/security/rate-limit` | `mode=redis`, `required=true`, `configured=true`, `state=backend_operational`. Keep `production_verified=false` interpreted as the helper's fixed non-attestation field. This writes one two-second diagnostic key. | Endpoint JSON is designed to be nonsecret; admin token/cookie hidden. |
| Redis service health/ACL/expiry and all-worker sharing | Same approved backend across workers; EVAL/INCR/EXPIRE/PTTL permitted; stable service/appropriate eviction policy; current operational evidence. Do not infer this from an old saved URL or historical service plan. | Connection details/credentials hidden; status only. |
| Effective `FORWARDED_ALLOW_IPS` | On Render, leave dashboard override absent and retain platform default `*`; repaired limiter does not use rewritten peer. Empty remains required for active non-Render production. | No. |
| Actual Render start command and wrappers | Retain `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; no new proxy flags required. Confirm no alternate untrusted ingress bypasses the public header contract. | Command may be recorded only after stripping any credentials. |
| `LOGIN_TRUSTED_PROXY_CIDRS` and platform header boundary | Leave unset on Render; do not invent CIDRs. Confirm public/custom hosts traverse trusted Render ingress and deliver one valid overwritten CF header. Private/direct paths must not admit untrusted callers. | No secret value required; record boundary/status only. |
| Existing or approved isolated staging evidence | Distinct-client buckets, forged-XFF and forged-CF-header overwrite resistance, shared worker counters, expiry/recovery, all auth classes and controlled Redis outage 503. Retain normal HTTPS/CSRF round trips. No production brute force, lockout or outage simulation. | All synthetic/real credential and cookie values hidden. |
| Revocation migration/schema and application DB access | Migration `v2r3s4t5u6v7` or later compatible schema; table/index exist; lookup/retirement permissions; old workers drained. Inspect migration/status read-only. | DB URL/password and row token hashes hidden. |
| Authorized dedicated test-account login/logout/replay | Normal test-only login/logout; session cleared/replaced, old cookie cannot restore protected access, independent browser unaffected. Do not use a real student's credentials or disclose cookies. | **Yes.** |
| Account version/status enforcement | Deployed source matches reviewed checks; use isolated synthetic tests for deletion/status/version mutations, not production edits. | Account identifiers/cookies remain private. |
| `PUBLIC_BASE_URL`, `RENDER_EXTERNAL_URL`, canonical/custom host and sibling origins | Legitimate admin/membership/family forms work; foreign origins denied where checks exist; residual CSRF risk reviewed. | Public origins nonsecret; tokens hidden. |
| Render ingress/CDN/APM/log configuration | No raw auth bodies, Cookie/Authorization/admin headers, capability paths/query values, Redis URLs, provider/consent secrets or SQL parameters in logs. Inspect configuration/redacted evidence, not raw secret dumps. | **Yes for all sensitive payloads.** |
| Revocation cleanup owner/job/last success | Approved scheduled `purge_expired` plus transaction commit; expiry semantics preserved and failures monitored. No current job is attested. | Status/counts only; no row hashes/DB credentials. |
| Enabled parent/director access classes and their deployed gates | If enabled for beta, approved configuration and existing disposable PostgreSQL access/expiry/revocation tests must be reviewed; capability access is separate from ordinary verifier PIN login. | Parent/provider/link/session secrets hidden. |

## Exact human enablement sequence after merge

This is a future operator runbook, not authorization or a record of actions performed. Source is **READY TO STAGE** for review; nothing has been staged. Production is **not ready to declare verified**.

1. Review and merge the four-file repair through the normal human process. Record the resulting merge/deployment revision and the last approved rollback revision. Do not activate the limiter on the old source: its Render configuration check returns 503.
2. Before production activation, use an approved isolated **public Render** staging service/backend to validate the provider boundary: different legitimate clients yield distinct buckets; caller XFF prefixes cannot change a bucket; supplying a fake CF header is overwritten by ingress; absent/invalid identity and backend outages return 503; limits/expiry and shared-worker behavior hold. Record booleans/counts, not credentials or raw header/cookie/Redis values. No staging infrastructure is created by this task. Existing approved equivalent evidence may satisfy this step.
3. Obtain deployment approval and deploy the merged repair to production **with current `off`/`false` settings unchanged**. Keep the existing start command, unset CIDRs, inherited forwarding default, saved Redis URL and signing key. Confirm all web instances have the patched revision and the normal HTTPS/session/CSRF flows still work. This step alone leaves the SEC-002 blocker in place.
4. Privately confirm Redis service readiness, correct URL/ACL/options/shared backend, production marker/secret policy and revocation schema. Confirm public/custom host ingress is the documented Render path and no untrusted direct/private route bypasses it. Keep the beta gate closed; do not infer readiness merely from the saved URL.
5. Obtain explicit activation approval. Change **only these two environment values together** and apply through the approved Render rollout:

   ```text
   LOGIN_RATE_LIMIT_MODE=redis
   LOGIN_RATE_LIMIT_REQUIRED=true
   ```

   Keep `LOGIN_RATE_LIMIT_REDIS_URL` at its existing verified secret value. Leave `LOGIN_TRUSTED_PROXY_CIDRS` unset. Leave `FORWARDED_ALLOW_IPS` without a dashboard override (runtime platform default `*`). Keep the exact existing start command. No new client-IP flag, secret rotation, database migration, Redis creation or proxy CIDR setting is part of this repair.
6. Confirm the effective values/revision on all workers, then have an authorized site admin run one `/admin/security/rate-limit` diagnostic. Expected: `mode=redis`, `required=true`, `configured=true`, `state=backend_operational`; `production_verified=false` is the diagnostic's fixed non-attestation field. This uses one separate two-second probe key. Perform only low-volume normal dedicated test-account login/logout for the intended auth classes; no threshold exhaustion or outage simulation in production. Check no unexpected 503/429 spike and no credential-bearing logs.
7. Record production evidence and remaining session/logging/cleanup/CSRF dispositions. Clear SEC-002 only when all original exit criteria are met, not just because configuration accepts `*` or a backend probe succeeds.

## Exact rollback plan

- **Before activation:** with off/false still in force, an explicitly approved code rollback may deploy the recorded prior revision with the same environment/start command. This returns to the known disabled-limiter state; beta remains blocked. No schema rollback is needed.
- **After activation, preferred response:** keep `LOGIN_RATE_LIMIT_MODE=redis` and `LOGIN_RATE_LIMIT_REQUIRED=true` while repairing/forward-fixing the backend or platform-header problem. Logins fail closed (503) during the outage; do not fabricate CF headers or fall back to XFF. Existing sessions are not deliberately invalidated by this patch.
- **If a human explicitly approves emergency restoration of the former unthrottled behavior:** set **both** `LOGIN_RATE_LIMIT_MODE=off` and `LOGIN_RATE_LIMIT_REQUIRED=false` in one approved configuration rollout; confirm that state before reverting source. Keep beta closed and mark SEC-002 BLOCKED. This disables protection and is an explicit security exception, not an automatic safe fallback. The original code with Redis enabled and Render's wildcard default will reject logins, so do not roll back source first expecting successful login.
- If source rollback is needed after that explicit exception, deploy the recorded prior approved revision; preserve the existing secret URL/key, unset CIDRs and unchanged start command. Do not rotate secrets, flush Redis, delete counters, alter the database or run schema downgrades. Counters expire normally. To re-enable, repeat the repaired-source activation/verification sequence under approval.

# Master Control Update

| Item | Status | Blocker | Evidence | Next Action | Exit Criterion | Last Updated |
| --- | --- | --- | --- | --- | --- | --- |
| SEC-002 | BLOCKED — local Render repair READY TO STAGE | Operator confirms production limiter off/false. Repair unmerged/undeployed; backend operation and deployed header/session checks remain open. | This report; baseline `b1279cff21d16ca81528cf3dfa9768b84680f42e`; Render/Uvicorn primary sources; follow-up focused tests 131 passed/0 failed/5 skipped; historical evidence retained above. | Human review/merge, approved staging boundary checks, approved code-only deployment, then approved paired Redis/required enablement and low-volume production verification. | All intended auth classes protected; deployed required settings, shared Redis operation and trustworthy client attribution verified; no P0/P1 blocker remains. | 2026-09-25 |

# Recommendation

**Local source is READY TO STAGE for review; keep full intended October beta blocked on SEC-002.** The Render incompatibility is repaired and focused tests pass, but production is confirmed unthrottled. Merge/deploy/enable only through explicit human approvals, using the sequence above. Redis operation, service-specific header delivery, deployed session behavior and the other original verification gates still require evidence. No staging, commit, push, merge, deployment or Render change was performed by this agent.
