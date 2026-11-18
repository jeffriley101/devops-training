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

For a new, undisputed parent or adult request, the Woodshed owner can use the following procedure. Existing delegated cases and receipts keep their original execution procedure.

### Owner-operated Render shell

1. In the authenticated Support mailbox, personally complete the identity, exact-account authority and confirmation checks above or in the [adult procedure](adult-deletion-support.md). Check the latest conversation for a withdrawal or dispute immediately before execution. Keep the original evidence in the restricted case.
2. Stage a private working copy on the Woodshed web service. The commands below use `/tmp/woodshed-owner` (owner-only directory, mode `0700`). Transfer the files privately from the restricted case, with mode `0600`. Use the opaque case reference as the filename, for example `CASE-0001.json`. The working copy contains the following fields.

   | Fields | Required value |
   | --- | --- |
   | `case`, `status`, `verified_by`, `verified_at` | The same case reference as the command, `approved`, `woodshed-owner`, and the original timezone-aware verification time within 24 hours. |
   | `gmail_thread_id`, `verification_method` | Original Gmail thread ID (8–64 letters, digits, underscores or hyphens) and a brief description of the checks actually performed (up to 160 characters). |
   | `requester_ref`, `identity_ref`, `authority_ref` | Distinct opaque references to the requester, identity check and authority check in the restricted case. Keep message contents and identity documents in that case. |
   | Exact subject | For a guardian, `profile_id`, `woodchuck_id`, `profile_created_at`. For an adult, `verifier_id`, canonical `email`, `verifier_created_at`. Numeric IDs are positive integers. Use the original creation timestamp with timezone. |
   | `confirmation` | An object containing the same `case`, numeric `profile_id` or `verifier_id`, and `requester_ref`, plus a distinct `reference` to the explicit response, its timezone-aware `at` within 30 minutes and its `action`. Use `DELETE THIS ACCOUNT` for a child or `DELETE MY ADULT ACCOUNT` for an adult. |
   | `disputed`, `reviewed_by` | Use `false` and `null` for an undisputed case. Hold a dispute or withdrawal. Use the stable case lock described below when publishing an amendment. |

3. In the private `.target.json`, retain the qualified application's `database`, numeric `database_oid`, `role` and `session_secret_sha256` (SHA-256 of the active signing secret). Confirm that the web service and database share a workspace and both run in Oregon. Copy the exact short `dpg-…` hostname from the database's **Connect → Internal** tab into an additional field, `render_internal_host`. Use the existing application login and database in the CLI's private `DATABASE_URL`, with that exact direct host, port 5432 (explicit or default) and the sole query setting `sslmode=require`. Use Render's normal private DNS. Keep connection URLs and credentials out of the case and shell history. The command checks negotiated TLS and the connected host and port for both execution and readback. Targets without the internal pin use the existing private socket or `verify-full` TCP configuration with an absolute CA path. [Render internal connections](https://render.com/docs/postgresql-creating-connecting#internal-connections)

   To qualify the internal connection on Render after release, use `status` for a documented guardian case or `status-adult` for a documented adult case, with the case reference and `--owner-dir` below. This checks the target through the application login without deleting an account. A current approved case with no prior execution returns `no_recorded_execution`. For a previously executed case, use its original approval and intent to verify the committed result.
4. Open the owner's authenticated interactive Render shell for `woodshed-woodchuck`. From the application directory, run the applicable command with the actual case reference and enter the existing Woodshed site-admin credential at the hidden prompt.

   ```bash
   python -m app.support_deletion_service execute CASE-0001 --owner-dir /tmp/woodshed-owner
   python -m app.support_deletion_service execute-adult CASE-0001 --owner-dir /tmp/woodshed-owner
   ```

5. The command locks and rereads the case before commit. A fresh read-only PostgreSQL transaction checks the result before the command writes `receipts/CASE-0001.result.json`. Transfer the original receipt and matching `.intent.json` through the owner's authenticated channel into the restricted Support case alongside its Gmail thread reference. Confirm both copies match before the service's temporary directory is lost. After a restart, restore the original case and those files to their private locations and use `status` or `status-adult` to recheck committed state. If execution is uncertain and the original intent is unavailable, keep the case open. Reply through the verified channel with the observed result. A disposal marked `access_required` or `contact_review_required`, or an outstanding provider action, keeps the case open. Apply the Support case retention deadline.

### Existing direct executor and receipts

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

For a new execution, read the `disposal` fields in the result. The executor checks eligible private activity, private practice and Weekly plans, standalone age history, and delivery metadata. `verified` means the eligible records in that category passed readback. A protected record can still remain. `access_required` means that category could not be completed with the named role’s permissions and visibility. Its changes rolled back. Keep the original deadline and assign the remaining work to its authorized owner. A new prior-cleanup case can verify later disposal. Earlier receipts retain their original meaning. [Remaining records](play-deletion-release-readiness.md)

## Application-mediated fulfillment

1. The independent approver verifies the subject, current authority and explicit confirmation using the applicable parent or adult procedure. Add exactly one field to the approved case, `executor_uid`, the integer UID of the assigned named Support operator. Use an individual authenticated OS account separate from the approver and application service. Record the person's identity-to-UID assignment with the case and preserve it through the evidence retention period. The service accepts both the original approval and a request owned by that UID.
2. From that named OS account, the operator runs `python -m app.support_deletion_request execute <case>` for a parent or `python -m app.support_deletion_request execute-adult <case>` for an adult. It records the case, action and digest of the entire approval. The response `execution_requested` means the request was recorded. The operator needs neither a database connection nor the application's signing secret.
3. From the existing trusted application environment, its custodian invokes `python -m app.support_deletion_service execute <case>` or `execute-adult <case>`. This is a one-case offline command. Keep service invocation, environment, database access and register under service custody. A named operator's shell must never run as the service identity. The service uses its existing application database connection and preserves the existing subject checks, approval lock, 24-hour verification window and 30-minute confirmation window. Immediately before commit it checks the approved case, request ownership, exact approval digest, authority and confirmation again.
4. To withdraw or supersede a request, the approver takes the stable approval lock exclusively and records the changed case status. Use `status` or `status-adult` from the service environment to reconcile an interrupted execution. A receipt requires fresh database readback and records the executor UID. A missing or changed request, an unreconciled intent or an incomplete disposal category keeps the case open. Preserve the original evidence and timestamps. Only a new independently approved case can authorize a new attempt when the original approval has changed.

**Host configuration.** For each kind, use the existing root-controlled `/etc/woodshed/parent-deletion-support/` or `/etc/woodshed/adult-deletion-support/` directory. The root-owned `request-<uid>.json` contains only `approval_directory`, `approval_uid` and `request_root`. The separate root-owned `application.json`, readable by the service, pins `service_uid`, `database`, `database_oid`, `role`, `session_secret_sha256`, `approval_uid`, `approval_directory`, `request_root` and `register`. The service uses its own verified-TLS connection or private local socket. The service login may own application data. Cluster-administrative and row-security-bypass roles are rejected. The direct human executor keeps its original nonowner restriction.

The approver owns the approval and stable lock. Give only the assigned operator and service the case read access they need. Under a protected request root, provision `<uid>/parent/` or `<uid>/adult/` owned by that operator, mode `2750`, with a group the service can read. The operator publishes a mode-`0640` request. Use a separate service-owned mode-`0700` register. Verify that the operator cannot read the service credential, signing secret or register, write approvals, replace installed service code or invoke arbitrary commands as the service. Check actual credential custody before enabling a previously provisioned direct executor. The mediated commands cover fresh confirmed deletions. Previously deleted accounts and legacy receipts continue through their original evidence-bound procedures.
