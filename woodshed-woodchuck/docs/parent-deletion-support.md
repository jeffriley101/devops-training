# Parent and guardian deletion requests

Parents and legal guardians can email `support@woodshedwoodchuck.com` with the subject **Account Deletion Request**. They can start without signing in, reinstalling the app, or knowing the child's Woodchuck ID. Never ask for the child's PIN.

## Verify the request

1. Open an access-restricted Support case. Record when it arrived and assign an opaque case reference. Keep identity and relationship evidence in the approved restricted channel and retain references in the case. Never place identity documents, passwords, tokens, or sensitive messages in the executor record.
2. Verify the adult and their current authority over the exact child.
   - For example, if the child has a current, verified parental consent record, check that the activated record belongs to that child's profile, identifies the same guardian, and has no withdrawal or conflicting authority. Then independently verify the adult through a fresh outbound callback or confirmation using a contact channel already verified for that adult. Use the trusted record for the contact details. A matching inbox address, Woodchuck ID, payment record, or adult-verification result alone cannot establish authority.
   - If the record is missing, withdrawn, or inconsistent, or the guardian no longer controls the previously verified contact channel, refer the case to an authorized reviewer. Use an already approved secure method to verify the adult's identity and authority over this child. Request only evidence needed to resolve the gap, through the restricted evidence channel. Keep the decision and restricted references in the case. If the necessary evidence or an approved method is unavailable, leave the case open as pending verification.
   - Bind the verified authority to the child's profile number, current Woodchuck ID, and original account creation timestamp. Keep the identity and relationship references separate.
3. Obtain the verified adult's explicit deletion confirmation for the identified child and case through the verified channel. Record its original timestamp and separate reference. The workflow requires authority verified within 24 hours and confirmation within 30 minutes of execution. Renew expired authority separately before obtaining a new confirmation.
4. Check the latest case record immediately before release to the executor. If the requester withdraws the request, their authority is revoked, or a competing claim or appeal is pending, record it. The approver takes the case lock, publishes a non-approved status, and holds execution. Leave unresolved cases open. A distinct authorized reviewer must record the decision before a disputed case can return to execution. Require fresh confirmation when authority or the approved case details change. If execution has already started, hold further action and use `status` to establish what committed.

## Execute and verify

The offline executor reuses the student deletion operation on one verified profile. It has no web route and does not require the child's account credentials. The authorized approver publishes the restricted case record. A separate named Support executor runs:

```bash
python -m app.parent_deletion_support execute <opaque-case-reference>
python -m app.parent_deletion_support status <opaque-case-reference>
```

Record the executor result against the Support case. `deletion_applied` means a fresh read-only database snapshot matched the expected tombstone and passed the shared ordinary-cleanup checks. Repeat execution checks the recorded outcome without applying deletion again. After interruption, use `status` to reconcile the intent. Hold any discrepancy or residual data for authorized investigation and preserve the original intent and result.

A separate reviewer checks lost access, invalidated sessions and links, the ordinary data the operation cleared, and any residual provider copies or retained records. Notify the verified adult with the observed result and any unresolved requests or approved retained exceptions. A receipt covers the shared deletion operation, while a broader completion notice requires verification of the separately approved obligations.

## Students who cannot sign in

If the student still knows their Woodchuck ID and PIN, they can sign in on the website at `/login` and complete deletion in Account & Privacy without reinstalling the app. Accept email requests through the same Support inbox. The current candidate has no PIN reset or verified executor for an email-only student self-request when the student lacks working credentials. Record the case as pending and escalate for an approved account-ownership and fulfillment procedure. The guardian executor requires independently verified guardian authority.

## Approval handoff and trusted execution

The trusted case-verification account owns the non-writable approvals directory, separate from the named executor. Publish the case to `<reference>.json` through that workflow only. Include these exact fields:

- `case`, `status` (`approved` to execute), `profile_id`, `woodchuck_id`, `profile_created_at`.
- `requester_ref`, `identity_ref`, `authority_ref`, `verified_by`, `verified_at`, `disputed`, `reviewed_by` (null or the distinct reviewer ID).
- `confirmation`, containing `case`, `profile_id`, `requester_ref`, `action` (`DELETE THIS ACCOUNT`), `reference`, and `at`.

Use timezone-aware timestamps and distinct opaque references. Give the executor read-only access to the approved files and stable `<reference>.lock` file. The verifier takes an exclusive file lock on that stable file while publishing, replacing, or revoking an approval. Hold the same file in place for the life of the case. The executor holds a shared lock and rechecks the approval and expiration through its transaction.

After operational authorization, a trusted host administrator provisions a root-owned `/etc/woodshed/parent-deletion-support/<executor UID>.json` file readable only by root and that executor's dedicated group. Configure the approved `database`, `database_oid`, individual database `role` and `database_url`, the trusted `approval_uid` and `approval_directory`, and the executor-private `register` path. The application secret custodian supplies the SHA-256 of the exact UTF-8 bytes of the active application `SESSION_SECRET` for the `session_secret_sha256` setting. The protected executor environment receives that same secret. The executor checks the digest before connecting, so a different secret cannot generate a different retired-ID reservation. Keep both the secret and its digest in restricted custody. Use the pinned `c36choice001` PostgreSQL target with a private local socket or verified TLS.

Qualify the individual nonowner role for the shared deletion statements, row locks, readback and existing security logging before use. PostgreSQL needs a column-level update privilege for each relation locked with `FOR UPDATE`. Limit lock-only grants to the required ID column, including protected consent. Grant updates to other columns only where the shared operation changes them. A recent C001 creation can cause one existing `quick_deletion` security event. Include its required insert columns and sequence usage when qualifying the role.

Keep the intent and result outside the database. Give the independent reviewer restricted access to the case evidence and observed results. Operational host identities, database grants and custody still require provisioning and qualification.

## Cleanup of a previously deleted student account

A named executor can finish ordinary cleanup for one existing deletion using the same approval directory, target database, application secret, restricted database role and private register. Use a **new** case reference. Preserve the original request or deletion clock, any older receipt and any missed deadline. The approver checks an original, attributable deletion record against the subject’s original Woodchuck ID, profile number, creation time and actual deletion time. Never derive a date from a backup, retention deadline, last update or current time. Leave the case open if the original record or its authority cannot be verified.

The independent approver publishes a fresh case under the same file lock. Include exactly `case`, `status` (`approved`), `action` (`CLEAN UP PREVIOUS STUDENT DELETION`), `profile_id`, `woodchuck_id` (original), `profile_created_at`, `deleted_at` (original), `deletion_evidence_ref`, `evidence_kind`, `verified_by` and `verified_at`. Use `support_receipt` and the original case reference for a prior deletion receipt held in this register. The executor checks the receipt, matching intent, recorded target and restriction digest. For an original deletion documented separately, use `verified_deletion_record` only after the custodian has inspected the original source and its authority in the restricted evidence channel. A date or reference supplied by the requester alone is insufficient. Verification of the source must be current within 24 hours of cleanup.

```bash
python -m app.parent_deletion_support cleanup-prior <new-case-reference>
python -m app.parent_deletion_support status-prior <new-case-reference>
```

The database must also match the original, keyed ID reservation, tombstone, unusable credential, session restriction, creation time and deletion time. `prior_ordinary_cleanup_verified` records the original deletion time and this later cleanup check after a fresh database read. It covers only the implemented ordinary cleanup. Use `status-prior` after an interruption. A mismatch, incomplete result or unavailable source remains open for review. Apply the same restricted case custody and retention schedule as other Support deletion cases.
