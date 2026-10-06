"""Disabled-by-default S1 foundation. Internal services; no request/route adapter.

Call inside one caller-owned transaction, authenticate in that transaction, and
commit only after success. Each mutation uses a savepoint so audit failure cannot
leave a committable partial authority mutation. PostgreSQL is the authority lock
backend; SQLite is for sequential synthetic rehearsals only.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import os
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Organization, TrustedVerifier
from .classroom_models import (
    ClassroomProgram, ClassroomRoleGrant, ClassroomClass,
    ClassroomTeachingAssignment, ClassroomOwnershipTransfer, ClassroomAuditEvent,
)
from .security import hash_pin, is_valid_pin
from .verifiers import authenticate_trusted_verifier, validate_email


ROLES = frozenset({"head_director", "admin", "code_manager", "billing"})
_SEAL = object()


class ClassroomDenied(ValueError):
    pass


class IdentityProofUnavailable(ClassroomDenied):
    pass


@dataclass(frozen=True, repr=False)
class AdultAuthentication:
    """Opaque evidence from existing credentials, never a client actor/role ID."""
    _session: Session = field(repr=False)
    _transaction: object = field(repr=False)
    _verifier_id: int = field(repr=False)
    _credential_hash: str = field(repr=False)
    _seal: object = field(repr=False)


def enabled() -> bool:
    return os.getenv("CLASSROOM_S1_ENABLED") == "1"


def _require_enabled():
    if not enabled():
        raise ClassroomDenied("Classroom S1 is disabled.")


def authenticate_adult(session: Session, *, email: str, pin: str) -> AdultAuthentication:
    # Avoid an identity-map credential value surviving a previous read.
    session.execute(select(TrustedVerifier).where(
        TrustedVerifier.email == validate_email(email)
    ).execution_options(populate_existing=True)).all()
    verifier = authenticate_trusted_verifier(session, email=email, pin=pin)
    if verifier is None:
        raise ClassroomDenied("Adult credentials are required.")
    return AdultAuthentication(session, session.get_transaction(), verifier.id, verifier.pin_hash, _SEAL)


def _actor(session, actor) -> int:
    if (type(actor) is not AdultAuthentication or actor._seal is not _SEAL
            or actor._session is not session
            or actor._transaction is not session.get_transaction()
            or actor._transaction is None or not actor._transaction.is_active):
        raise ClassroomDenied("Current adult credential authentication is required.")
    current = session.scalar(select(TrustedVerifier.pin_hash).where(
        TrustedVerifier.id == actor._verifier_id))
    if current != actor._credential_hash:
        raise ClassroomDenied("Adult credentials changed; authenticate again.")
    return actor._verifier_id


def _synthetic_provisioner(session, actor):
    """No live provisioning exists in S1. Both credentials and operator config required.

    These checks are a local containment boundary, NOT proof of email ownership.
    A future real onboarding path needs independently approved identity proof.
    """
    actor_id = _actor(session, actor)
    allowlist = os.getenv("CLASSROOM_S1_PROVISIONER_IDS", "").split(",")
    url = session.get_bind().url
    sqlite_local = False
    if url.get_backend_name() == "sqlite" and url.database:
        path = Path(url.database).resolve()
        sqlite_local = (str(path).startswith("/tmp/classroom-s1-")
                        and path.parent != Path("/tmp"))
    pg_local = (url.get_backend_name() == "postgresql"
                and url.host in {"127.0.0.1", "::1"}
                and (url.database or "").startswith("classroom_s1_")
                and not (set(url.query) - {"options"}))
    if (os.getenv("CLASSROOM_S1_LOCAL_PROVISIONING") != "1"
            or str(actor_id) not in allowlist or not (sqlite_local or pg_local)):
        raise IdentityProofUnavailable(
            "Direct email identity proof is unavailable; only explicitly authorized synthetic local provisioning exists.")
    _synthetic_email(session.scalar(select(TrustedVerifier.email).where(
        TrustedVerifier.id == actor_id)))
    return actor_id


def _synthetic_email(email):
    normalized = validate_email(email)
    if not normalized.rpartition("@")[2].endswith((".test", ".invalid")):
        raise IdentityProofUnavailable("Local provisioning requires a synthetic .test or .invalid address.")
    return normalized


def _mutation(function):
    @wraps(function)
    def run(session, *args, **kwargs):
        _require_enabled()
        connection = session.connection()
        if connection.dialect.name not in {"sqlite", "postgresql"}:
            raise ClassroomDenied("Unsupported Classroom database backend.")
        if (connection.dialect.name == "postgresql"
                and connection.get_isolation_level() != "READ COMMITTED"):
            # A repeatable-read snapshot could retain a revoked role even after
            # waiting for the Program lock. Require fresh statement snapshots.
            raise ClassroomDenied("Classroom writes require READ COMMITTED isolation.")
        if (connection.dialect.name == "sqlite"
                and connection.exec_driver_sql("PRAGMA foreign_keys").scalar() != 1):
            raise ClassroomDenied("Classroom writes require SQLite foreign key enforcement.")
        if (connection.dialect.name == "sqlite"
                and not connection.connection.driver_connection.in_transaction):
            # Python's legacy sqlite transaction mode does not BEGIN for SELECT.
            # Without this, releasing our SAVEPOINT can commit the outer work.
            connection.exec_driver_sql("BEGIN")
        # begin_nested flushes preceding work; callers must not pre-stage authority
        # changes. All service work and its success audit share this savepoint.
        with session.begin_nested():
            result = function(session, *args, **kwargs)
            session.flush()
            return result
    return run


def _fresh(session, model, *criteria):
    return session.scalar(select(model).where(*criteria).with_for_update()
                          .execution_options(populate_existing=True))


def _lock_program(session, program_id):
    if type(program_id) is not int:
        raise ClassroomDenied("An exact Program ID is required.")
    # A transaction may mutate only one Program, avoiding cross-Program lock
    # inversion. Program -> scoped Class/relationship/proposal.
    previous = session.info.get("classroom_write_program")
    transaction = session.get_transaction()
    if previous and previous[0] is transaction and previous[1] != program_id:
        raise ClassroomDenied("Use a separate transaction for each Program.")
    session.info["classroom_write_program"] = (transaction, program_id)
    program = _fresh(session, ClassroomProgram, ClassroomProgram.organization_id == program_id)
    if program is None:
        raise ClassroomDenied("Program unavailable.")
    return program


def _require_adults(session, *verifier_ids):
    # No adult row update locks: these operations never mutate credentials, and
    # shared adults must not introduce cross-Program lock inversions. Foreign
    # keys retain identity evidence; _actor rereads credentials after lock waits.
    for verifier_id in sorted(set(verifier_ids)):
        if type(verifier_id) is not int or session.scalar(select(TrustedVerifier.id).where(
                TrustedVerifier.id == verifier_id)) is None:
            raise ClassroomDenied("Adult unavailable.")


def _roles(session, program_id, verifier_id):
    now = datetime.now(timezone.utc)
    return set(session.scalars(select(ClassroomRoleGrant.role).where(
        ClassroomRoleGrant.program_id == program_id,
        ClassroomRoleGrant.verifier_id == verifier_id,
        ClassroomRoleGrant.starts_at <= now,
        ClassroomRoleGrant.ended_at.is_(None))))


def _authority(session, program, actor, *, admin=False):
    actor_id = _actor(session, actor)
    if program.owner_verifier_id != actor_id and not (
            admin and "admin" in _roles(session, program.organization_id, actor_id)):
        raise ClassroomDenied("Program authority is required.")
    return actor_id


def _audit(session, program_id, actor_id, action, **references):
    session.add(ClassroomAuditEvent(program_id=program_id, actor_verifier_id=actor_id,
                                   action=action, **references))


def _name(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ClassroomDenied("A valid display name is required.")
    return value.strip()


@_mutation
def onboard_adult(session, *, actor, email, display_name, pin):
    """Synthetic-only new identity, no login or relationship side effects.

    Existing addresses always require separate authentication; an operator cannot
    reset/claim them. Unique email arbitrates concurrent attempts at insertion.
    """
    _synthetic_provisioner(session, actor)
    normalized = _synthetic_email(email)
    name = _name(display_name, 100)
    if not isinstance(pin, str) or not is_valid_pin(pin):
        raise ClassroomDenied("PIN must contain exactly four digits.")
    if session.scalar(select(TrustedVerifier.id).where(TrustedVerifier.email == normalized)):
        raise ClassroomDenied("Existing account requires its own credentials.")
    adult = TrustedVerifier(email=normalized, display_name=name, pin_hash=hash_pin(pin))
    try:
        with session.begin_nested():
            session.add(adult)
            session.flush()
    except IntegrityError as error:
        raise ClassroomDenied("Account creation conflicted; use existing credentials.") from error
    return adult.id


@_mutation
def provision_program(session, *, actor, organization_id, owner):
    """Explicit synthetic operator + exact credential-authenticated founding adult."""
    actor_id = _synthetic_provisioner(session, actor)
    owner_id = _actor(session, owner)
    for adult_id in {actor_id, owner_id}:
        _synthetic_email(session.scalar(select(TrustedVerifier.email).where(
            TrustedVerifier.id == adult_id)))
    organization = _fresh(session, Organization, Organization.id == organization_id)
    if organization is None:
        raise ClassroomDenied("Organization unavailable.")
    _require_adults(session, actor_id, owner_id)
    _actor(session, actor)
    _actor(session, owner)
    if session.get(ClassroomProgram, organization_id) is not None:
        raise ClassroomDenied("Program already provisioned.")
    program = ClassroomProgram(organization_id=organization_id, owner_verifier_id=owner_id)
    session.add(program)
    session.flush()
    grant = ClassroomRoleGrant(program_id=organization_id, verifier_id=owner_id,
                               role="head_director", granted_by_verifier_id=actor_id)
    session.add(grant)
    session.flush()
    _audit(session, organization_id, actor_id, "program_provisioned",
           target_verifier_id=owner_id, new_owner_id=owner_id)
    _audit(session, organization_id, actor_id, "role_granted",
           target_verifier_id=owner_id, role_grant_id=grant.id)
    return program


@_mutation
def grant_role(session, *, actor, program_id, verifier_id, role):
    if role not in ROLES:
        raise ClassroomDenied("Choose an explicit Program role; ownership uses transfer.")
    program = _lock_program(session, program_id)
    _require_adults(session, _actor(session, actor), verifier_id)
    actor_id = _authority(session, program, actor)
    if role in _roles(session, program_id, verifier_id):
        raise ClassroomDenied("Role already granted.")
    row = ClassroomRoleGrant(program_id=program_id, verifier_id=verifier_id,
                             role=role, granted_by_verifier_id=actor_id)
    session.add(row)
    session.flush()
    _audit(session, program_id, actor_id, "role_granted", target_verifier_id=verifier_id,
           role_grant_id=row.id)
    return row


@_mutation
def revoke_role(session, *, actor, program_id, grant_id):
    program = _lock_program(session, program_id)
    actor_id = _authority(session, program, actor)
    row = _fresh(session, ClassroomRoleGrant, ClassroomRoleGrant.id == grant_id,
                 ClassroomRoleGrant.program_id == program_id)
    if row is None or row.ended_at is not None:
        raise ClassroomDenied("Active scoped role unavailable.")
    row.ended_at = datetime.now(timezone.utc)
    row.ended_by_verifier_id = actor_id
    row.ended_reason = "revoked"
    _audit(session, program_id, actor_id, "role_revoked", target_verifier_id=row.verifier_id,
           role_grant_id=row.id)
    return row


@_mutation
def create_class(session, *, actor, program_id, display_name):
    program = _lock_program(session, program_id)
    actor_id = _authority(session, program, actor, admin=True)
    row = ClassroomClass(program_id=program_id, display_name=_name(display_name, 150))
    session.add(row)
    session.flush()
    _audit(session, program_id, actor_id, "class_created", class_id=row.id)
    return row


def _class(session, program_id, class_id):
    row = _fresh(session, ClassroomClass, ClassroomClass.id == class_id,
                 ClassroomClass.program_id == program_id)
    if row is None:
        raise ClassroomDenied("Class unavailable in this Program.")
    return row


@_mutation
def assign_teacher(session, *, actor, program_id, class_id, verifier_id):
    program = _lock_program(session, program_id)
    _require_adults(session, _actor(session, actor), verifier_id)
    actor_id = _authority(session, program, actor, admin=True)
    _class(session, program_id, class_id)
    if session.scalar(select(ClassroomTeachingAssignment.id).where(
            ClassroomTeachingAssignment.class_id == class_id,
            ClassroomTeachingAssignment.verifier_id == verifier_id,
            ClassroomTeachingAssignment.ended_at.is_(None))):
        raise ClassroomDenied("Teaching assignment already exists.")
    row = ClassroomTeachingAssignment(program_id=program_id, class_id=class_id,
                                     verifier_id=verifier_id, granted_by_verifier_id=actor_id)
    session.add(row)
    session.flush()
    _audit(session, program_id, actor_id, "teacher_assigned", class_id=class_id,
           target_verifier_id=verifier_id, assignment_id=row.id)
    return row


@_mutation
def end_teaching(session, *, actor, program_id, assignment_id, reason="revoked"):
    if reason not in {"revoked", "departed"}:
        raise ClassroomDenied("Choose an explicit assignment end reason.")
    program = _lock_program(session, program_id)
    actor_id = _actor(session, actor)
    row = _fresh(session, ClassroomTeachingAssignment,
                 ClassroomTeachingAssignment.id == assignment_id,
                 ClassroomTeachingAssignment.program_id == program_id)
    if row is None or row.ended_at is not None:
        raise ClassroomDenied("Active scoped teaching assignment unavailable.")
    if not (reason == "departed" and actor_id == row.verifier_id):
        _authority(session, program, actor, admin=True)
    row.ended_at = datetime.now(timezone.utc)
    row.ended_by_verifier_id = actor_id
    row.ended_reason = reason
    _audit(session, program_id, actor_id, "teacher_" + reason, class_id=row.class_id,
           target_verifier_id=row.verifier_id, assignment_id=row.id)
    return row


@_mutation
def propose_transfer(session, *, actor, program_id, recipient_id):
    program = _lock_program(session, program_id)
    _require_adults(session, _actor(session, actor), recipient_id)
    actor_id = _authority(session, program, actor)
    if recipient_id == actor_id:
        raise ClassroomDenied("Choose a different adult recipient.")
    old = _fresh(session, ClassroomOwnershipTransfer,
                 ClassroomOwnershipTransfer.program_id == program_id,
                 ClassroomOwnershipTransfer.status == "pending")
    if old is not None:
        old.status = "superseded"
        old.resolved_at = datetime.now(timezone.utc)
        _audit(session, program_id, actor_id, "transfer_superseded",
               target_verifier_id=old.recipient_verifier_id, transfer_id=old.id)
        session.flush()
    row = ClassroomOwnershipTransfer(program_id=program_id,
        initiator_verifier_id=actor_id, recipient_verifier_id=recipient_id,
        owner_version=program.owner_version)
    session.add(row)
    session.flush()
    _audit(session, program_id, actor_id, "transfer_proposed",
           target_verifier_id=recipient_id, transfer_id=row.id)
    return row


def _proposal(session, program, transfer_id):
    row = _fresh(session, ClassroomOwnershipTransfer,
                 ClassroomOwnershipTransfer.id == transfer_id,
                 ClassroomOwnershipTransfer.program_id == program.organization_id)
    if (row is None or row.status != "pending"
            or row.initiator_verifier_id != program.owner_verifier_id
            or row.owner_version != program.owner_version):
        raise ClassroomDenied("Current pending ownership proposal required.")
    return row


@_mutation
def cancel_transfer(session, *, actor, program_id, transfer_id):
    program = _lock_program(session, program_id)
    actor_id = _authority(session, program, actor)
    row = _proposal(session, program, transfer_id)
    row.status = "cancelled"
    row.resolved_at = datetime.now(timezone.utc)
    _audit(session, program_id, actor_id, "transfer_cancelled",
           target_verifier_id=row.recipient_verifier_id, transfer_id=row.id)
    return row


@_mutation
def accept_transfer(session, *, actor, program_id, transfer_id):
    program = _lock_program(session, program_id)
    actor_id = _actor(session, actor)
    _require_adults(session, actor_id, program.owner_verifier_id)
    actor_id = _actor(session, actor)
    row = _proposal(session, program, transfer_id)
    if row.recipient_verifier_id != actor_id:
        raise ClassroomDenied("The exact intended adult must authenticate and accept.")
    old_owner = program.owner_verifier_id
    program.owner_verifier_id = actor_id
    program.owner_version += 1
    row.status = "accepted"
    row.resolved_at = datetime.now(timezone.utc)
    _audit(session, program_id, actor_id, "ownership_transferred", transfer_id=row.id,
           target_verifier_id=actor_id, old_owner_id=old_owner, new_owner_id=actor_id)
    return row


def _metadata(session, actor_id, *, program_id=None, class_id=None):
    roles = list(session.scalars(select(ClassroomRoleGrant).where(
        ClassroomRoleGrant.verifier_id == actor_id,
        ClassroomRoleGrant.starts_at <= datetime.now(timezone.utc),
        ClassroomRoleGrant.ended_at.is_(None))))
    assignments = list(session.scalars(select(ClassroomTeachingAssignment).where(
        ClassroomTeachingAssignment.verifier_id == actor_id,
        ClassroomTeachingAssignment.starts_at <= datetime.now(timezone.utc),
        ClassroomTeachingAssignment.ended_at.is_(None))))
    role_programs = {r.program_id for r in roles}
    assignment_programs = {a.program_id for a in assignments}
    programs = session.execute(select(ClassroomProgram, Organization.name).join(
        Organization, Organization.id == ClassroomProgram.organization_id).where(or_(
            ClassroomProgram.owner_verifier_id == actor_id,
            ClassroomProgram.organization_id.in_(role_programs | assignment_programs)))
        .order_by(ClassroomProgram.organization_id)
        .execution_options(populate_existing=True)).all()
    output = []
    for program, name in programs:
        pid = program.organization_id
        if program_id is not None and pid != program_id:
            continue
        explicit_roles = sorted(r.role for r in roles if r.program_id == pid)
        owner = program.owner_verifier_id == actor_id
        classes = select(ClassroomClass).where(ClassroomClass.program_id == pid)
        if not owner and "admin" not in explicit_roles:
            classes = classes.where(ClassroomClass.id.in_(
                [a.class_id for a in assignments if a.program_id == pid]))
        if class_id is not None:
            classes = classes.where(ClassroomClass.id == class_id)
        resolved = [{"id": c.id, "display_name": c.display_name,
                     "teaching": any(a.class_id == c.id for a in assignments)}
                    for c in session.scalars(classes.order_by(ClassroomClass.id))]
        if class_id is not None and not resolved:
            continue
        output.append({"program_id": pid, "display_name": name, "owner": owner,
                       "roles": explicit_roles, "classes": resolved})
    if (program_id is not None or class_id is not None) and not output:
        raise ClassroomDenied("Program/Class section unavailable.")
    return output


def my_classes(session, *, actor, program_id=None, class_id=None):
    # No Classroom table introspection/query in the disabled branch. Missing
    # schema while enabled propagates as an error; never an empty roster.
    actor_id = _actor(session, actor)
    if not enabled():
        return {"status": "disabled", "paid_capabilities": "not_implemented",
                "student_reporting": "not_implemented"}
    if class_id is not None and program_id is None:
        raise ClassroomDenied("Class selection requires its exact Program.")
    return {"status": "foundation_only",
            "programs": _metadata(session, actor_id, program_id=program_id, class_id=class_id),
            "paid_capabilities": "not_implemented", "student_reporting": "not_implemented"}


def director_sections(session, *, actor, selected_week=None, today=None):
    from .band_director_dashboard import dashboard_metrics
    actor_id = _actor(session, actor)
    return {"connected_students": dashboard_metrics(session, verifier_id=actor_id,
            selected_week=selected_week, today=today),
            "my_classes": my_classes(session, actor=actor)}


def require_student_reporting(session, *, actor, program_id, class_id):
    _require_enabled()
    my_classes(session, actor=actor, program_id=program_id, class_id=class_id)
    raise ClassroomDenied("Classroom student reporting is not implemented (S3).")
