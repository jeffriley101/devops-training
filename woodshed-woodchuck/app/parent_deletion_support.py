"""Offline fulfillment of a verified Support case through account deletion."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
from hmac import compare_digest
import json
import os
from pathlib import Path
import re
import stat
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from .account_deletion import (
    DeletionDataConflict,
    anonymize_woodchuck_account,
    lock_account_for_deletion,
    ordinary_deletion_complete,
)
from .models import WoodchuckProfile
from .session_config import DEVELOPMENT_SECRET, session_secret


CONFIG_DIR = Path("/etc/woodshed/parent-deletion-support")
REVISION = "c36choice001"
AUTHORITY_WINDOW = timedelta(hours=24)
CONFIRMATION_WINDOW = timedelta(minutes=30)
ACTION = "DELETE THIS ACCOUNT"
REFERENCE = re.compile(r"[A-Za-z0-9_-]{8,64}")
CASE_FIELDS = {
    "case", "status", "profile_id", "woodchuck_id", "profile_created_at",
    "requester_ref", "identity_ref", "authority_ref", "verified_by", "verified_at",
    "confirmation", "disputed", "reviewed_by",
}


class SupportDenied(ValueError):
    """Stable diagnostic without case evidence, credentials or SQL."""


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _time(value):
    if not isinstance(value, str):
        raise SupportDenied("support_case_invalid")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise SupportDenied("support_case_invalid") from None
    if result.tzinfo is None:
        raise SupportDenied("support_case_invalid")
    return _utc(result)


def _reference(value):
    return isinstance(value, str) and REFERENCE.fullmatch(value) is not None


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value):
    return sha256(_encode(value)).hexdigest()


def _require_application_secret(expected_digest):
    secret = session_secret()
    if secret.strip() == DEVELOPMENT_SECRET or len(secret.strip()) < 32:
        raise SupportDenied("support_application_session_secret_required")
    if (not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
            or not compare_digest(sha256(secret.encode("utf-8")).hexdigest(), expected_digest)):
        raise SupportDenied("support_application_session_secret_mismatch")


def _read_json(stream):
    def unique_keys(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise SupportDenied("support_record_invalid")
        return result

    raw = stream.read(16385)
    if len(raw) > 16384:
        raise SupportDenied("support_record_invalid")
    try:
        return json.loads(raw, object_pairs_hook=unique_keys)
    except (ValueError, UnicodeError):
        raise SupportDenied("support_record_invalid") from None


@contextmanager
def _protected_file(path, owner):
    """Read a regular file whose custody is controlled by the configured owner."""
    if not path.is_absolute():
        raise SupportDenied("support_private_path_required")
    for directory in path.parents:
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, owner}
                or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))):
            raise SupportDenied("support_private_path_required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or info.st_mode & 0o027 or info.st_nlink != 1):
            raise SupportDenied("support_private_file_required")
        yield stream


def _write_new(path, value):
    """Publish a complete record without overwriting earlier evidence."""
    import tempfile

    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".support-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_encode(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        os.unlink(temporary)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


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
            "AND pg_has_role(oid, 'MEMBER')) OR EXISTS (SELECT FROM pg_catalog.pg_database "
            "WHERE datname = current_database() AND pg_has_role(datdba, 'MEMBER')) OR EXISTS "
            "(SELECT FROM pg_catalog.pg_class WHERE oid = 'public.woodchuck_profiles'::regclass "
            "AND pg_has_role(relowner, 'MEMBER'))"
        ))
        if administrative:
            raise SupportDenied("support_nonadministrative_role_required")
        revisions = session.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
        if revisions != [REVISION]:
            raise SupportDenied("support_revision_mismatch")
        session.execute(text("SET LOCAL search_path TO public, pg_catalog, pg_temp"))


def _authority(case, reference, now):
    if (type(case) is not dict or set(case) != CASE_FIELDS or case["case"] != reference
            or case["status"] != "approved"
            or type(case["profile_id"]) is not int or case["profile_id"] <= 0
            or type(case["disputed"]) is not bool
            or not isinstance(case["woodchuck_id"], str)
            or re.fullmatch(r"WC-[A-Z0-9-]{1,29}", case["woodchuck_id"]) is None):
        raise SupportDenied("support_case_invalid")
    confirmation = case["confirmation"]
    if (type(confirmation) is not dict
            or set(confirmation) != {"case", "profile_id", "requester_ref", "action", "reference", "at"}
            or confirmation["case"] != reference or type(confirmation["profile_id"]) is not int
            or confirmation["profile_id"] != case["profile_id"] or confirmation["action"] != ACTION
            or confirmation["requester_ref"] != case["requester_ref"]):
        raise SupportDenied("support_confirmation_mismatch")
    references = [case[key] for key in ("requester_ref", "identity_ref", "authority_ref")] + [confirmation["reference"]]
    if not all(_reference(value) for value in references) or len(set(references)) != len(references):
        raise SupportDenied("support_distinct_evidence_required")
    if (not _reference(case["verified_by"])
            or case["reviewed_by"] is not None and (
                not _reference(case["reviewed_by"]) or case["reviewed_by"] == case["verified_by"])
            or case["disputed"] and case["reviewed_by"] is None):
        raise SupportDenied("support_independent_review_required")
    created_at = _time(case["profile_created_at"])
    verified_at = _time(case["verified_at"])
    confirmed_at = _time(confirmation["at"])
    if (not created_at <= verified_at <= confirmed_at <= now
            or now - verified_at >= AUTHORITY_WINDOW
            or now - confirmed_at >= CONFIRMATION_WINDOW):
        raise SupportDenied("support_case_expired")
    return created_at


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


def _check_register(directory):
    if not directory.is_absolute():
        raise SupportDenied("support_private_register_required")
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise SupportDenied("support_private_register_required")


def fulfill(factory, *, target, approval_directory, approval_uid, register, reference,
            status_only=False, clock=lambda: datetime.now(timezone.utc)):
    """Execute only the exact authorized account; reconcile a known case without mutating it."""
    if not _reference(reference):
        raise SupportDenied("support_case_invalid")
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
            return _reconcile(factory, target, reference, intent, result_path)
        with factory() as session:
            target.require(session)
            if status_only:
                return {"case": reference, "status": "no_recorded_execution"}
            with _protected_file(approval_directory / (reference + ".lock"), approval_uid) as approval_lock:
                fcntl.flock(approval_lock, fcntl.LOCK_SH)
                approval_path = approval_directory / (reference + ".json")
                with _protected_file(approval_path, approval_uid) as stream:
                    case = _read_json(stream)
                _authority(case, reference, clock())
                profile, _ = lock_account_for_deletion(session, case["profile_id"])
                moment = clock()
                created_at = _authority(case, reference, moment)
                if (profile is None or profile.status != "active" or profile.woodchuck_id != case["woodchuck_id"]
                        or _utc(profile.created_at) != created_at):
                    raise SupportDenied("support_child_mismatch_or_unavailable")
                previous_version = profile.session_version
                anonymize_woodchuck_account(session, profile=profile, now=moment)
                restriction = _restriction_digest(profile)
                if (restriction is None or _utc(profile.deleted_at) != _utc(moment)
                        or profile.session_version != previous_version + 1):
                    raise SupportDenied("support_deletion_postcondition_failed")
                intent = {
                    "case": reference, "profile_id": profile.id, "role": target.role,
                    "database": target.database, "database_oid": target.database_oid,
                    "approval_sha256": _digest(case), "restriction_sha256": restriction,
                    "deleted_at": _utc(profile.deleted_at).isoformat(),
                }
                _write_new(intent_path, intent)
                with _protected_file(approval_path, approval_uid) as stream:
                    if _digest(_read_json(stream)) != intent["approval_sha256"]:
                        raise SupportDenied("support_approval_changed")
                _authority(case, reference, clock())
                session.commit()
        return _reconcile(factory, target, reference, intent, result_path)


def _reconcile(factory, target, reference, intent, result_path):
    fields = {
        "case", "profile_id", "role", "database", "database_oid",
        "approval_sha256", "restriction_sha256", "deleted_at",
    }
    if (type(intent) is not dict or set(intent) != fields or intent["case"] != reference
            or (intent["database"], intent["database_oid"], intent["role"])
            != (target.database, target.database_oid, target.role)
            or type(intent["profile_id"]) is not int
            or any(not isinstance(intent[key], str) or re.fullmatch(r"[0-9a-f]{64}", intent[key]) is None
                   for key in ("approval_sha256", "restriction_sha256"))):
        raise SupportDenied("support_record_mismatch")
    with factory() as session:
        # Read both the tombstone and ordinary cleanup from one committed snapshot.
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        target.require(session)
        profile = session.get(WoodchuckProfile, intent["profile_id"])
        if (_restriction_digest(profile) != intent["restriction_sha256"]
                or _utc(profile.deleted_at) != _time(intent["deleted_at"])):
            raise SupportDenied("support_execution_unreconciled")
        if not ordinary_deletion_complete(session, profile):
            raise SupportDenied("support_ordinary_cleanup_incomplete")
    result = {"case": reference, "status": "deletion_applied", "deleted_at": intent["deleted_at"]}
    receipt = {**result, "intent_sha256": _digest(intent)}
    if result_path.exists():
        with _protected_file(result_path, os.geteuid()) as stream:
            if _read_json(stream) != receipt:
                raise SupportDenied("support_record_mismatch")
    else:
        _write_new(result_path, receipt)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("execute", "status"))
    parser.add_argument("case", help="Opaque Support case reference")
    args = parser.parse_args(argv)
    engine = None
    try:
        if os.geteuid() == 0:
            raise SupportDenied("support_named_executor_required")
        with _protected_file(CONFIG_DIR / (str(os.geteuid()) + ".json"), 0) as stream:
            config = _read_json(stream)
        _require_application_secret(config.get("session_secret_sha256"))
        approval_uid = config["approval_uid"]
        if approval_uid == os.geteuid() or type(approval_uid) is not int:
            raise SupportDenied("support_independent_approval_custody_required")
        target = SupportTarget(config["database"], config["database_oid"], config["role"])
        url = make_url(config["database_url"])
        if (url.drivername != "postgresql+psycopg" or url.database != target.database
                or url.username != target.role or any(name.startswith("PG") for name in os.environ)
                or set(url.query) - {"host", "port", "sslmode", "sslrootcert"}
                or (url.host and (url.query.get("sslmode") != "verify-full"
                                 or not Path(url.query.get("sslrootcert", "")).is_absolute()))
                or (not url.host and not Path(url.query.get("host", "")).is_absolute())
                or (url.host and "host" in url.query)):
            raise SupportDenied("support_connection_unqualified")
        engine = create_engine(url, hide_parameters=True)
        result = fulfill(sessionmaker(engine, autoflush=False), target=target,
                         approval_directory=Path(config["approval_directory"]), approval_uid=approval_uid,
                         register=Path(config["register"]), reference=args.case,
                         status_only=args.action == "status")
        print(json.dumps(result, sort_keys=True))
        return 0
    except SupportDenied as error:
        print(str(error), file=sys.stderr)
        return 1
    except DeletionDataConflict:
        print("support_account_data_conflict", file=sys.stderr)
        return 1
    except Exception:
        print("support_operation_unavailable_or_uncertain", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
