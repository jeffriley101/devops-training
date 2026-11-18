"""Offline fulfillment of a verified Support case through account deletion."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
from hmac import compare_digest
import json
import os
from pathlib import Path
import re
import stat
import sys

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from .account_deletion import (
    DELETED_PUBLIC_NAME,
    DeletionDataConflict,
    clear_student_ordinary_data,
    anonymize_woodchuck_account,
    lock_account_for_deletion,
    ordinary_deletion_complete,
)
from .accounts import retired_identifier_hash
from .deletion_disposal import associated_disposal, valid_disposal_record
from .adult_account_deletion import (
    AdultDeletionHold,
    adult_deletion_complete,
    adult_invitation_cutoff_complete,
    adult_spent_invitations_complete,
    clear_adult_spent_invitations,
    delete_adult_account,
    invitation_key,
)
from .models import TrustedVerifier, WoodchuckProfile
from .session_config import DEVELOPMENT_SECRET, session_secret
from .support_case_files import (
    ACTION, ADULT_ACTION, PRIOR_ACTION, PRIOR_ADULT_ACTION, CONFIG_DIR, ADULT_CONFIG_DIR,
    SupportDenied, _authority, _check_parent_directories, _digest, _protected_file,
    _read_json, _reference, _time, _utc, _write_new,
)
from .verifiers import normalize_email


REVISION = "c36choice001"
INVITATION_SCOPE = "ordinary-invitation-disposal-v2"
RETENTION_SCOPE = "associated-data-disposal-v3"


def _require_application_secret(expected_digest):
    secret = session_secret()
    if secret.strip() == DEVELOPMENT_SECRET or len(secret.strip()) < 32:
        raise SupportDenied("support_application_session_secret_required")
    if (not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
            or not compare_digest(sha256(secret.encode("utf-8")).hexdigest(), expected_digest)):
        raise SupportDenied("support_application_session_secret_mismatch")


@dataclass(frozen=True)
class SupportTarget:
    database: str
    database_oid: int
    role: str

    def require(self, session):
        if session.get_bind().dialect.name != "postgresql":
            raise SupportDenied("support_postgresql_required")
        session.execute(text("SET LOCAL search_path TO pg_catalog, public, pg_temp"))
        found = session.execute(text(
            "SELECT datname::text, oid::bigint, session_user::text, current_user::text "
            "FROM pg_catalog.pg_database WHERE datname = current_database()"
        )).one()
        if tuple(found) != (self.database, self.database_oid, self.role, self.role):
            raise SupportDenied("support_target_mismatch")
        administrative = session.scalar(text(
            "SELECT EXISTS (SELECT FROM pg_catalog.pg_roles "
            "WHERE (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls) "
            "AND pg_has_role(oid, 'MEMBER')) OR (:human_login AND (EXISTS (SELECT FROM pg_catalog.pg_database "
            "WHERE datname = current_database() AND pg_has_role(datdba, 'MEMBER')) OR EXISTS "
            "(SELECT FROM pg_catalog.pg_class WHERE oid IN "
            "('public.woodchuck_profiles'::regclass, 'public.trusted_verifiers'::regclass) "
            "AND pg_has_role(relowner, 'MEMBER'))))"
        ), {"human_login": not isinstance(self, ApplicationDeletionTarget)})
        if administrative:
            raise SupportDenied("support_nonadministrative_role_required")
        revisions = session.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
        if revisions != [REVISION]:
            raise SupportDenied("support_revision_mismatch")
        session.execute(text("SET LOCAL search_path TO public, pg_catalog, pg_temp"))


class ApplicationDeletionTarget(SupportTarget):
    """The service-held application login may own its data. Operators receive no database login."""


@dataclass(frozen=True)
class RenderInternalDeletionTarget(ApplicationDeletionTarget):
    """One owner-qualified, direct Render Postgres hostname on the private network."""

    host: str

    def __post_init__(self):
        if (not isinstance(self.host, str) or len(self.host) > 63
                or not re.fullmatch(r"dpg-[a-z0-9]+(?:-[a-z0-9]+)*", self.host)):
            raise SupportDenied("support_application_configuration_invalid")

    def require(self, session):
        # Check the actual client transport for both execution and fresh readback.
        connection = session.connection().connection.driver_connection.pgconn
        if (not connection.ssl_in_use or connection.host != self.host.encode("ascii")
                or connection.port != b"5432"):
            raise SupportDenied("support_connection_unqualified")
        super().require(session)


OWNER_ACTOR = "woodshed-owner"


@dataclass(frozen=True, repr=False)
class OwnerShellApproval:
    """Render authenticates shell access. Recheck the owner's entered admin credential."""

    credential: str

    def require(self):
        if not sys.stdin.isatty():
            raise SupportDenied("support_interactive_owner_shell_required")
        configured = os.getenv("SITE_ADMIN_TOKEN", "")
        if (os.getenv("RENDER") != "true" or os.getenv("RENDER_SERVICE_TYPE") != "web"
                or os.getenv("RENDER_EXTERNAL_HOSTNAME") != "woodshed-woodchuck.onrender.com"
                or not configured.strip() or not compare_digest(self.credential.encode(), configured.encode())):
            raise SupportDenied("support_owner_admin_required")

    def case(self, approval):
        self.require()
        if (not isinstance(approval, dict) or approval.get("verified_by") != OWNER_ACTOR
                or not _reference(approval.get("gmail_thread_id"))
                or not isinstance(approval.get("verification_method"), str)
                or not 1 <= len(approval["verification_method"].strip()) <= 160):
            raise SupportDenied("support_owner_verification_required")
        if approval.get("disputed") is not False:
            raise SupportDenied("support_disputed_case")
        return {key: value for key, value in approval.items() if key not in ("gmail_thread_id", "verification_method")}


def _original_intent(register, case, target, *, adult):
    """Check the original Support receipt and target before certifying later cleanup."""
    source = case["deletion_evidence_ref"]
    intent_path = register / (source + ".intent.json")
    result_path = register / (source + ".result.json")
    if case["evidence_kind"] == "verified_deletion_record" and not (intent_path.exists() or result_path.exists()):
        # Signed-in self-deletion has no Support receipt. The custodian must
        # inspect an independent original record before approving this reference.
        return None
    try:
        with _protected_file(intent_path, os.geteuid()) as stream:
            intent = _read_json(stream)
        with _protected_file(result_path, os.geteuid()) as stream:
            receipt = _read_json(stream)
    except FileNotFoundError:
        raise SupportDenied("support_prior_receipt_unavailable") from None
    subject_id = "verifier_id" if adult else "profile_id"
    fields = {"case", subject_id, "role", "database", "database_oid", "approval_sha256", "restriction_sha256", "deleted_at"}
    hashes = {"approval_sha256", "restriction_sha256"}
    expected_receipt = {"case": source, "status": "adult_deletion_applied" if adult else "deletion_applied"}
    if type(intent) is dict and intent.get("retention_scope") == RETENTION_SCOPE:
        if not valid_disposal_record(intent.get("disposal"), adult=adult):
            raise SupportDenied("support_prior_receipt_mismatch")
        fields.add("disposal")
        expected_receipt["disposal"] = intent["disposal"]
    if adult:
        fields.add("invitation_key")
        hashes.add("invitation_key")
    if (type(intent) is not dict or set(intent) not in (fields, fields | {"retention_scope"})
            or ("retention_scope" in intent and intent["retention_scope"] not in (INVITATION_SCOPE, RETENTION_SCOPE))
            or any(not isinstance(intent[key], str) or re.fullmatch(r"[0-9a-f]{64}", intent[key]) is None for key in hashes)
            or (intent["case"], intent["database"], intent["database_oid"], intent["role"], intent[subject_id])
            != (source, target.database, target.database_oid, target.role, case[subject_id])
            or _time(intent["deleted_at"]) != _time(case["deleted_at"])
            or receipt != {**expected_receipt, "deleted_at": intent["deleted_at"], "intent_sha256": _digest(intent)}):
        raise SupportDenied("support_prior_receipt_mismatch")
    return intent


def _restriction_digest(profile):
    if (profile is None or profile.status != "deleted" or profile.deleted_at is None
            or not profile.woodchuck_id.startswith("WD-") or not profile.pin_hash.startswith("deleted$")
            or not profile.retired_woodchuck_id_hash):
        return None
    return _digest({
        "profile_id": profile.id,
        "created_at": _utc(profile.created_at).isoformat(),
        "deleted_at": _utc(profile.deleted_at).isoformat(),
        "tombstone": profile.woodchuck_id,
        "unusable_credential": profile.pin_hash,
        "retired_id": profile.retired_woodchuck_id_hash,
        "session_version": profile.session_version,
    })


def _adult_restriction_digest(verifier):
    if verifier is None:
        return None
    return _digest({
        "verifier_id": verifier.id, "created_at": _utc(verifier.created_at).isoformat(),
        "updated_at": _utc(verifier.updated_at).isoformat(), "email": verifier.email,
        "display_name": verifier.display_name, "pin_hash": verifier.pin_hash,
    })


def _apply_deletion(session, case, reference, clock, *, adult, prior, original):
    if adult:
        verifier = session.scalar(select(TrustedVerifier).where(
            TrustedVerifier.id == case["verifier_id"]
        ).with_for_update().execution_options(populate_existing=True))
        moment = clock()
        created_at = _authority(case, reference, moment, adult=True, prior=prior)
        if prior:
            restriction = _adult_restriction_digest(verifier)
            if (original is None or verifier is None or restriction != original["restriction_sha256"]
                    or _utc(verifier.created_at) != created_at or _utc(verifier.updated_at) != _time(case["deleted_at"])
                    or not adult_deletion_complete(session, verifier)
                    or not adult_invitation_cutoff_complete(session, original["invitation_key"], verifier.updated_at, moment)):
                raise SupportDenied("support_prior_deletion_mismatch_or_unavailable")
            clear_adult_spent_invitations(session, verifier.id)
            if not adult_spent_invitations_complete(session, verifier.id):
                raise SupportDenied("support_prior_cleanup_postcondition_failed")
            coverage = associated_disposal(session, verifier.id, adult_key=original["invitation_key"],
                                           deleted_at=verifier.updated_at)
            return _utc(verifier.updated_at), restriction, coverage
        if (verifier is None or normalize_email(verifier.email) != case["email"]
                or _utc(verifier.created_at) != created_at):
            raise SupportDenied("support_adult_mismatch_or_unavailable")
        delete_adult_account(session, verifier, now=moment)
        if (_utc(verifier.updated_at) != _utc(moment) or not adult_deletion_complete(session, verifier)
                or not adult_spent_invitations_complete(session, verifier.id)):
            raise SupportDenied("support_deletion_postcondition_failed")
        coverage = associated_disposal(session, verifier.id, adult_key=invitation_key(case["email"]), deleted_at=moment)
        return moment, _adult_restriction_digest(verifier), coverage

    profile, _ = lock_account_for_deletion(session, case["profile_id"])
    moment = clock()
    created_at = _authority(case, reference, moment, adult=False, prior=prior)
    if prior:
        restriction = _restriction_digest(profile)
        if (restriction is None or (original is not None and original["restriction_sha256"] != restriction)
                or _utc(profile.created_at) != created_at
                or _utc(profile.deleted_at) != _time(case["deleted_at"])
                or profile.session_version < 1 or profile.display_name != DELETED_PUBLIC_NAME
                or re.fullmatch(r"WD-[A-F0-9]{12}", profile.woodchuck_id) is None
                or re.fullmatch(r"deleted\$[a-f0-9]{64}", profile.pin_hash) is None
                or not compare_digest(profile.retired_woodchuck_id_hash, retired_identifier_hash(case["woodchuck_id"]))):
            raise SupportDenied("support_prior_deletion_mismatch_or_unavailable")
        clear_student_ordinary_data(session, profile, now=moment)
        session.flush()
        if not ordinary_deletion_complete(session, profile) or _restriction_digest(profile) != restriction:
            raise SupportDenied("support_prior_cleanup_postcondition_failed")
        return _utc(profile.deleted_at), restriction, associated_disposal(session, profile.id)
    if (profile is None or profile.status != "active" or profile.woodchuck_id != case["woodchuck_id"]
            or _utc(profile.created_at) != created_at):
        raise SupportDenied("support_child_mismatch_or_unavailable")
    previous_version = profile.session_version
    coverage = anonymize_woodchuck_account(session, profile=profile, now=moment)
    restriction = _restriction_digest(profile)
    if (restriction is None or _utc(profile.deleted_at) != _utc(moment)
            or profile.session_version != previous_version + 1):
        raise SupportDenied("support_deletion_postcondition_failed")
    return moment, restriction, coverage


def _check_register(directory):
    _check_parent_directories(directory, os.geteuid())
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise SupportDenied("support_private_register_required")


@contextmanager
def _locked_approval(directory, owner, reference):
    with _protected_file(directory / (reference + ".lock"), owner) as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        path = directory / (reference + ".json")
        with _protected_file(path, owner) as stream:
            approval = _read_json(stream)
        yield path, approval


def _case_for_operator(approval, reference, operator_request, *, adult, approval_uid):
    if operator_request is None:
        return approval, None
    if isinstance(operator_request, OwnerShellApproval):
        return operator_request.case(approval), OWNER_ACTOR
    if type(approval) is not dict:
        raise SupportDenied("support_case_invalid")
    assigned = approval.get("executor_uid")
    if type(assigned) is not int or assigned <= 0 or assigned in (approval_uid, os.geteuid()):
        raise SupportDenied("support_independent_executor_required")
    actor = operator_request.verify(approval, reference, adult=adult)
    if type(actor) is not int or actor != assigned:
        raise SupportDenied("support_operator_mismatch")
    return {key: value for key, value in approval.items() if key != "executor_uid"}, actor


def fulfill(factory, *, target, approval_directory, approval_uid, register, reference,
            status_only=False, adult=False, prior=False, clock=lambda: datetime.now(timezone.utc),
            operator_request=None):
    """Execute only the exact authorized account; reconcile a known case without mutating it."""
    if not _reference(reference):
        raise SupportDenied("support_case_invalid")
    mediated = isinstance(target, ApplicationDeletionTarget)
    if mediated != (operator_request is not None) or (mediated and prior):
        raise SupportDenied("support_execution_mode_mismatch")
    if isinstance(operator_request, OwnerShellApproval):
        operator_request.require()
        if approval_uid != os.geteuid():
            raise SupportDenied("support_owner_case_custody_required")
    elif mediated and approval_uid == os.geteuid():
        raise SupportDenied("support_independent_approval_custody_required")
    _check_register(register)
    intent_path = register / (reference + ".intent.json")
    result_path = register / (reference + ".result.json")
    # A private stable lock serializes the same case across CLI invocations.
    fd = os.open(register / (reference + ".lock"), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if intent_path.exists():
            with _protected_file(intent_path, os.geteuid()) as stream:
                intent = _read_json(stream)
            if mediated:
                with _locked_approval(approval_directory, approval_uid, reference) as (_, approval):
                    case, actor = _case_for_operator(approval, reference, operator_request, adult=adult,
                                                     approval_uid=approval_uid)
                    subject_id = "verifier_id" if adult else "profile_id"
                    if (not isinstance(intent, dict) or _digest(approval) != intent.get("approval_sha256")
                            or intent.get(subject_id) != case.get(subject_id)):
                        raise SupportDenied("support_approval_changed")
                    # An immutable, authenticated approval may be read after its execution window.
                    return _reconcile(factory, target, reference, intent, result_path, adult=adult,
                                      prior=False, clock=clock, actor=actor)
            return _reconcile(factory, target, reference, intent, result_path, adult=adult, prior=prior, clock=clock)
        if isinstance(operator_request, OwnerShellApproval) and result_path.exists():
            raise SupportDenied("support_execution_unreconciled")
        with factory() as session:
            target.require(session)
            if status_only and not mediated:
                return {"case": reference, "status": "no_recorded_execution"}
            with _locked_approval(approval_directory, approval_uid, reference) as (approval_path, approval):
                case, actor = _case_for_operator(approval, reference, operator_request, adult=adult,
                                                 approval_uid=approval_uid)
                _authority(case, reference, clock(), adult=adult, prior=prior)
                if status_only:
                    return {"case": reference, "status": "no_recorded_execution"}
                original = _original_intent(register, case, target, adult=adult) if prior else None
                moment, restriction, coverage = _apply_deletion(session, case, reference, clock, adult=adult,
                                                                prior=prior, original=original)
                subject_id = "verifier_id" if adult else "profile_id"
                intent = {
                    "case": reference, subject_id: case[subject_id], "role": target.role,
                    "database": target.database, "database_oid": target.database_oid,
                    "approval_sha256": _digest(approval), "restriction_sha256": restriction,
                    "deleted_at": _utc(moment).isoformat(), "retention_scope": RETENTION_SCOPE, "disposal": coverage,
                }
                if actor == OWNER_ACTOR:
                    intent["executed_by"] = OWNER_ACTOR
                elif mediated:
                    intent["executor_uid"] = actor
                if prior:
                    intent["cleanup_action"] = PRIOR_ADULT_ACTION if adult else PRIOR_ACTION
                    intent["cleanup_at"] = _utc(clock()).isoformat()
                if adult:
                    key = original["invitation_key"] if prior else invitation_key(case["email"])
                    if not adult_invitation_cutoff_complete(session, key, moment, clock()):
                        raise SupportDenied("support_deletion_postcondition_failed")
                    intent["invitation_key"] = key
                _write_new(intent_path, intent)
                with _protected_file(approval_path, approval_uid) as stream:
                    latest = _read_json(stream)
                    if _digest(latest) != intent["approval_sha256"]:
                        raise SupportDenied("support_approval_changed")
                case, current_actor = _case_for_operator(latest, reference, operator_request, adult=adult,
                                                         approval_uid=approval_uid)
                if current_actor != actor:
                    raise SupportDenied("support_operator_mismatch")
                _authority(case, reference, clock(), adult=adult, prior=prior)
                session.commit()
        return _reconcile(factory, target, reference, intent, result_path, adult=adult, prior=prior, clock=clock,
                          actor=actor)


def _reconcile(factory, target, reference, intent, result_path, *, adult, prior, clock, actor=None):
    subject_id = "verifier_id" if adult else "profile_id"
    fields = {
        "case", subject_id, "role", "database", "database_oid",
        "approval_sha256", "restriction_sha256", "deleted_at",
    }
    hashes = {"approval_sha256", "restriction_sha256"}
    actor_key = "executed_by" if actor == OWNER_ACTOR else "executor_uid"
    if actor is not None:
        fields.add(actor_key)
    if prior:
        fields.update(("cleanup_action", "cleanup_at"))
    if adult:
        fields.add("invitation_key")
        hashes.add("invitation_key")
    current_scope = isinstance(intent, dict) and "retention_scope" in intent
    if current_scope:
        fields.add("retention_scope")
    extended = current_scope and intent["retention_scope"] == RETENTION_SCOPE
    if extended:
        fields.add("disposal")
    if (type(intent) is not dict or set(intent) != fields or intent["case"] != reference
            or (prior and not current_scope)
            or (current_scope and intent["retention_scope"] not in (INVITATION_SCOPE, RETENTION_SCOPE))
            or (extended and not valid_disposal_record(intent["disposal"], adult=adult))
            or (actor is not None and (not extended or type(intent[actor_key]) is not type(actor)
                                        or intent[actor_key] != actor))
            or (intent["database"], intent["database_oid"], intent["role"])
            != (target.database, target.database_oid, target.role)
            or type(intent[subject_id]) is not int or intent[subject_id] <= 0
            or any(not isinstance(intent[key], str) or re.fullmatch(r"[0-9a-f]{64}", intent[key]) is None
                   for key in hashes)):
        raise SupportDenied("support_record_mismatch")
    if prior and (intent["cleanup_action"] != (PRIOR_ADULT_ACTION if adult else PRIOR_ACTION)
                  or not _time(intent["deleted_at"]) <= _time(intent["cleanup_at"]) <= clock()):
        raise SupportDenied("support_record_mismatch")
    with factory() as session:
        # Read both the tombstone and ordinary cleanup from one committed snapshot.
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        target.require(session)
        subject = session.get(TrustedVerifier if adult else WoodchuckProfile, intent[subject_id])
        digest = _adult_restriction_digest(subject) if adult else _restriction_digest(subject)
        if (digest != intent["restriction_sha256"]
                or _utc(subject.updated_at if adult else subject.deleted_at) != _time(intent["deleted_at"])):
            raise SupportDenied("support_execution_unreconciled")
        if adult:
            complete = adult_deletion_complete(session, subject) and (
                not current_scope or adult_spent_invitations_complete(session, subject.id))
        else:
            complete = ordinary_deletion_complete(session, subject, current_scope=current_scope)
        if not complete:
            raise SupportDenied("support_ordinary_cleanup_incomplete")
        if extended:
            try:
                associated_disposal(session, subject.id, adult_key=intent.get("invitation_key"),
                                    deleted_at=_time(intent["deleted_at"]), recorded=intent["disposal"])
            except DeletionDataConflict as error:
                raise SupportDenied("support_associated_cleanup_unverified") from error
        if adult and not adult_invitation_cutoff_complete(session, intent["invitation_key"],
                                                         subject.updated_at, clock()):
            raise SupportDenied("support_ordinary_cleanup_incomplete")
    result = {"case": reference, "status": "adult_deletion_applied" if adult else "deletion_applied",
              "deleted_at": intent["deleted_at"]}
    if actor is not None:
        result[actor_key] = actor
    if extended:
        result["disposal"] = intent["disposal"]
    if prior:
        result.update(status="prior_adult_spent_invitations_verified" if adult else "prior_ordinary_cleanup_verified",
                      cleanup_at=intent["cleanup_at"])
    receipt = {**result, "intent_sha256": _digest(intent)}
    if result_path.exists():
        with _protected_file(result_path, os.geteuid()) as stream:
            if _read_json(stream) != receipt:
                raise SupportDenied("support_record_mismatch")
    else:
        _write_new(result_path, receipt)
    return result


def _require_connection(url, target):
    if (url.drivername != "postgresql+psycopg" or url.database != target.database
            or url.username != target.role or any(name.startswith("PG") for name in os.environ)):
        raise SupportDenied("support_connection_unqualified")
    if isinstance(target, RenderInternalDeletionTarget):
        if (not url.database or not url.username or url.host != target.host or url.port not in (None, 5432)
                or url.query != {"sslmode": "require"} or "HOSTALIASES" in os.environ or "LOCALDOMAIN" in os.environ):
            raise SupportDenied("support_connection_unqualified")
        return
    socket_host = url.query.get("host", "")
    if (set(url.query) - {"host", "port", "sslmode", "sslrootcert"}
            or (url.host and (url.query.get("sslmode") != "verify-full"
                             or not Path(url.query.get("sslrootcert", "")).is_absolute()))
            or (not url.host and (not isinstance(socket_host, str)
                                  or not all(Path(host).is_absolute() for host in socket_host.split(","))))
            or (url.host and "host" in url.query)):
        raise SupportDenied("support_connection_unqualified")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("execute", "status", "execute-adult", "status-adult",
                                         "cleanup-prior", "status-prior", "cleanup-prior-adult", "status-prior-adult"))
    parser.add_argument("case", help="Opaque Support case reference")
    args = parser.parse_args(argv)
    engine = None
    try:
        if os.geteuid() == 0:
            raise SupportDenied("support_named_executor_required")
        adult = args.action.endswith("-adult")
        prior = args.action in {"cleanup-prior", "status-prior", "cleanup-prior-adult", "status-prior-adult"}
        config_directory = ADULT_CONFIG_DIR if adult else CONFIG_DIR
        with _protected_file(config_directory / (str(os.geteuid()) + ".json"), 0) as stream:
            config = _read_json(stream)
        _require_application_secret(config.get("session_secret_sha256"))
        approval_uid = config["approval_uid"]
        if approval_uid == os.geteuid() or type(approval_uid) is not int:
            raise SupportDenied("support_independent_approval_custody_required")
        target = SupportTarget(config["database"], config["database_oid"], config["role"])
        url = make_url(config["database_url"])
        _require_connection(url, target)
        engine = create_engine(url, hide_parameters=True)
        result = fulfill(sessionmaker(engine, autoflush=False), target=target,
                         approval_directory=Path(config["approval_directory"]), approval_uid=approval_uid,
                         register=Path(config["register"]), reference=args.case,
                         status_only=args.action.startswith("status"), adult=adult, prior=prior)
        print(json.dumps(result, sort_keys=True))
        return 0
    except SupportDenied as error:
        print(str(error), file=sys.stderr)
        return 1
    except DeletionDataConflict:
        print("support_account_data_conflict", file=sys.stderr)
        return 1
    except AdultDeletionHold as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("support_operation_unavailable_or_uncertain", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
