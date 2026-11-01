# Classroom Enrollment V2 — local service foundation

Base: `c5e496ddc2e95a212f9cc2814d1a5e6639fd025a` / `p31pta001`.
This is the first local completion task. It adds no HTTP routes, screens, email
delivery, production activation or comprehensive reporting reader.

## Entry and identity

The existing `classroom_s2.join_class` code/entry-intent service remains the public
service for student-initiated enrollment. Manual and QR-derived code text keep the
same existing validation. Both that adapter and the new invitation acceptance
adapter call the private S2 `_enroll_student` transition after authenticating the
student and validating their method's entry authority. The transition requires its
Program-first transaction; it is not an externally callable authentication path.

`classroom_enrollment` additionally requires `CLASSROOM_ENROLLMENT_V2_ENABLED=1`
and the existing S1/S2 gates. The new gate defaults OFF. The existing code path
does not depend on the new gate or query the new tables.

`issue_invitation(actor, program_id, class_id, woodchuck_id)` requires current,
transaction-bound adult credentials and an explicit active teaching assignment to
that exact Class. Owner/Head Director/Admin/Billing/code-manager roles alone are
insufficient. The target is an exact existing WC# using existing account-ID
normalization, not a search, email address or newly created identity. Issuance
requires an eligible active account, active/open Class, valid entitlement, capacity,
and no administrative/legacy held membership. The same account and source checks
are repeated after lock waits. No student name, email, PIN or token is logged.

Issuance does not enroll. The named student must authenticate with their existing
account and explicitly respond. Account/parent authorization is the existing S2
eligibility contract: an already authorized child may enroll, while missing or
withdrawn account authorization denies acceptance. This does not approve child
Class reporting. The separate S3 under-13/consent-linked denial is unchanged.
Unregistered or authorization-pending accounts must complete the existing account
flow before issuance/acceptance; this task creates no new parent/school authority.

## Persistence: additive `c32enroll001`

The migration follows P31 and creates only:

* `classroom_enrollment_invitations`: exact Program/Class/student/teaching assignment,
  issuer, fixed `classroom-enrollment-v2` purpose, issue/deadline instants, lifecycle
  state, resolution instant, and accepted membership-period reference.
* `classroom_enrollment_audit_events`: scoped invitation references, typed action,
  exactly the appropriate verifier or target-student actor, accepted-period
  reference where applicable, and server time. No free text, code, credential,
  email, practice record, parent payload or reporting scope is retained.

Restrictive foreign keys retain identities, assignments, periods and history.
Composite foreign keys reject invitation assignment/Class/Program substitution and
audit scope substitution. CHECKs enforce the fixed purpose, state, finite deadline,
resolution shape, acceptance-period evidence, actor and response-time rules.
Only one unresolved offer per exact teaching assignment/student is permitted.
Repeated issuance by that teacher returns the same offer. Different assigned
directors may have distinct offers; all still converge on one Class/student anchor.

The accepted-period FK retains a real period. Services also validate its exact
Class/student anchor and active, unheld state; the FK alone is not authorization.
One event per invitation/action makes repeats idempotent. A member created by
either path has the unchanged S2 `student_joined` audit. An invitation acceptance
also retains its invitation-specific audit in the same savepoint and transaction.

No previous table, migration, student identity, authorization, P-Chart invitation,
PTA record, function or trigger is rewritten or backfilled. Downgrade refuses if
either new table contains any evidence. Only empty local schemas may round-trip.

## Invitation lifecycle

The initial response window is seven days, matching the existing finite invitation
window without changing sharing invitations. UTC instants and a half-open deadline
apply: acceptance at the deadline refuses enrollment.

| State/action | Effect |
|---|---|
| Pending | Offer only; no membership or privileges |
| Not Now (`defer`) | Deferred offer; no membership; original deadline unchanged; repeating it adds no event |
| Decline | Terminal declined offer; no membership; later acceptance of that offer cannot enroll |
| Accept | Recheck exact current sources and account; create/reuse the authoritative membership and active period atomically |
| Expired | Effective by server time immediately; retained on the next response, revocation or replacement issue; no scheduler required |
| Revoke | Issuer may revoke an unresolved offer, including after their teaching assignment ends; no membership mutation |

Terminal declined/expired/revoked offers cannot enroll. A fresh authorized issue
creates new evidence. Accept/Decline/Not Now are only available to the named
authenticated account. Declining, deferring, viewing one's offer and issuer cleanup
remain possible after learning eligibility/entitlement is lost, without granting
access. A deleted account or stale/foreign credential proof still refuses.

Closed enrollment blocks new code joins, new offers and acceptance of outstanding
offers. Holds, removed memberships, corrupt/held period histories, expired access,
capacity excess, archived Classes and ended/replaced teaching assignments cannot
be bypassed through an invitation.

Acceptance retries reuse only the exact originally accepted period while current
entry/account/source conditions and that active unheld period remain valid. The
response deadline does not expire an already accepted membership. A retained offer
cannot rejoin after leave, undo a hold, substitute a later period, or change any
other Class. Accepted offers cannot be revoked as a membership-removal shortcut;
the existing Owner/Admin membership lifecycle remains authoritative.

## Transactions and disclosure boundaries

Use PostgreSQL READ COMMITTED and Program -> Class -> scoped invitation/account/
membership locking, retaining existing consent-before-profile ordering. No Team,
PTA or season locks are acquired. SQLite requires foreign-key enforcement and is
sequential evidence only. Callers own the outer commit/rollback. Failed audits and
outer rollbacks retain no partial enrollment, response or false success event.

Enrollment does not create a sharing preference, S3 reporting period, Stage 3B
progress authorization, verifier connection, Team membership, consumer subscription,
personal Full Access, XP or reward. It never exposes private practice history.
Existing reporting source-revision checks continue to observe the real S2
membership-period/audit transitions. Enrollment invitation states are not consent.

Internal ORM objects and `EnrollmentResponse` are service results, not public data
contracts. The future UI must explicitly project only approved self/invitation
fields; no raw ORM serialization or WC# lookup directory is authorized here.

## Operator and recovery compatibility

Compatible operators validate exactly one installed `c32enroll001` revision, all
prior P31 guards and the exact two-table contract, independent of feature gates.
New storage under an older/future stamp refuses. Operator preservation snapshots
include both new tables. Native PostgreSQL CHECK spellings and the five exact
dump/restore spellings have finite table-and-constraint-scoped matching; general
SQL normalization and C5's audited mappings are unchanged.

Original trusted P31 activation receipts remain verifiable after the additive
migration when their protected evidence is unchanged. Their bytes, hashes, stored
activation revision and issuance guards remain untouched. This is not an ongoing
health check or authorization to fabricate historical issuance.

**Remaining operator boundary:** new PTA ACTIVATE still requires installed P31,
as the original immutable issuance contract specifies. c32 does not relax it or
pretend a new activation occurred on P31. The local tests prove safe refusal.
Any future production proposal that needs new PTA issuance on a later schema must
resolve that compatibility explicitly; it must not rewrite old issuance evidence.

The task qualifies disposable SQLite copies and PostgreSQL 18 native schema/data
archive restoration with the new application and validator. It does not qualify
restoration of real production backups, a production rollout or the full product.

## Next task: Enrollment V2 screens and HTTP integration

After review/authorization, build Join and Share, equivalent manual/QR entry, the
assigned-director WC# issue/revoke view, and the student's own pending/deferred
inbox with Accept/Decline/Not Now and clear terminal states. Add strict same-origin,
CSRF, session/PIN freshness, rate limits, bounded pagination, generic unavailable
responses and no-store/no-referrer handling. GET must never accept, enroll or
change an offer. Do not expose private names/emails through target lookup.

Use one Program per service transaction. Integrate existing account/parent
authorization without creating a new identity or claiming reporting consent.
Any sharing action in Join and Share must be separately explicit and retain the
existing sharing service; enrollment alone must not preselect/report private data.
Keep the original P-Chart invitation routes and tokens unchanged. Exercise both
complete flows in desktop/mobile browser tests before proposing activation.
