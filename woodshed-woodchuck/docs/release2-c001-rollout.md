# Director #1 / C001 rollout

The official student entry is https://woodshed-woodchuck.onrender.com/prebeta/C001.
Display or print `/prebeta/C001/display` using the browser's Print command. Its
locally generated SVG QR contains only that public URL. Displaying the page does
not claim registration or create an account. Test a printed copy with a phone
before Director #1 distributes it.

## Manual registration control

Set `C001_REGISTRATION_DISABLED=true` in the application environment and restart
all application instances to stop new C001 claims. Unset it or set it to `false`
to reopen. The default is open; unrecognized nonempty values close entry.
This is an operator configuration step, not a public toggle. There is no cap.

Closed entry returns an HTML message with HTTP 503 and a Guest tools link.
Previously established signed claims and pending parent requests are honored,
including cross-device activation after successful verification. Existing tester
enrollments and lifetime access remain subject to the same age/privacy gates.
Guest tools remain accountless and local-only. Reopening uses the same URL/QR.

Sign in to Site Admin and open `/admin/analytics?cohort=C001` to see the switch
status and existing **Enrolled active testers** count. That count uses the existing
active-account report semantics, rather than counting pending parent claims or
deleted accounts. No enrollment count or student identity is added publicly.

## Remaining rollout gates

- Verify the deployed switch state in Site Admin and scan the official QR.
- Complete the real-guardian Production KWS canary before inviting students who
  need parent authorization. This remains a manual rollout gate.
- Review parent-permission email copy through the separate product/legal process.
- Confirm Director #1 is ready to show/print the entry page and operators know how
  to close and reopen claims. Any desired PILOT-D1 backfill uses the existing
  reviewed operator workflow; this delta does not run it.

No schema migration, billing setup, enrollment cap, or classroom administration
is required by this delta. Production notice v3, hashes, signatures, callbacks,
retention, and guardian attestation are unchanged.

## Local validation (2026-09-23)

Latest results per test: 278 Python tests passed, zero assertion failures, zero
skips, and six setup errors from the missing separate recovery checkout
(`recovery_authorization.py` / `age_recovery.py`). These six tests in
`test_private_practice_followup.py` remain unverified; restore that checkout and
rerun them before claiming complete recovery-flow coverage. Another 19 Guest
JavaScript checks passed with zero failures/skips.

The six recovery setup errors reproduced identically on untouched base commit
`87078957c80ec3e881a620262aa8c95a808c88b8`, confirming a pre-existing missing
external-checkout dependency rather than a Release 2 regression.

The Python coverage includes tester enrollment and migration, age screening,
private practice/family, KWS Production/verification/retention/Test configuration,
cohort analytics and migration, Guest boundaries, session revocation, and a real
Chromium Guest journey. Final focused rollout/migration/analytics rerun: 44 passed.
Two existing test fixtures were corrected: the browser's synthetic accounts now
declare age, and analytics restores its logger after Alembic logging setup.

Subsequent Guest/session wording validation: 34 passed, 0 failed, 0 skipped;
`git diff --check` passed.

Tests used a clean environment, disposable SQLite files, and a disposable UTF-8
PostgreSQL database on a local Unix socket, with captured emails/KWS requests.
The temporary PostgreSQL server was stopped
after validation. No production database, real delivery, or Render configuration
was used.
