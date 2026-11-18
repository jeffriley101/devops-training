"""Credential-free case validation and file custody shared by the operator and service."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from .security import validate_email


CONFIG_DIR = Path("/etc/woodshed/parent-deletion-support")
ADULT_CONFIG_DIR = Path("/etc/woodshed/adult-deletion-support")
AUTHORITY_WINDOW = timedelta(hours=24)
CONFIRMATION_WINDOW = timedelta(minutes=30)
ACTION = "DELETE THIS ACCOUNT"
ADULT_ACTION = "DELETE MY ADULT ACCOUNT"
PRIOR_ACTION = "CLEAN UP PREVIOUS STUDENT DELETION"
PRIOR_ADULT_ACTION = "CLEAN UP PREVIOUS ADULT DELETION"
PRIOR_CASE_FIELDS = {
    "case", "status", "action", "profile_id", "woodchuck_id", "profile_created_at", "deleted_at",
    "deletion_evidence_ref", "evidence_kind", "verified_by", "verified_at",
}
PRIOR_ADULT_FIELDS = (PRIOR_CASE_FIELDS - {"profile_id", "profile_created_at", "woodchuck_id"}) | {
    "verifier_id", "verifier_created_at",
}
REFERENCE = re.compile(r"[A-Za-z0-9_-]{8,64}")
CASE_FIELDS = {
    "case", "status", "profile_id", "woodchuck_id", "profile_created_at",
    "requester_ref", "identity_ref", "authority_ref", "verified_by", "verified_at",
    "confirmation", "disputed", "reviewed_by",
}
ADULT_CASE_FIELDS = ((CASE_FIELDS - {"profile_id", "woodchuck_id", "profile_created_at"})
                     | {"verifier_id", "email", "verifier_created_at"})


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


def _check_parent_directories(path, owner):
    if not path.is_absolute():
        raise SupportDenied("support_private_path_required")
    for directory in path.parents:
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, owner}
                or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))):
            raise SupportDenied("support_private_path_required")


@contextmanager
def _protected_file(path, owner):
    """Read a regular file whose custody is controlled by the configured owner."""
    _check_parent_directories(path, owner)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or info.st_mode & 0o027 or info.st_nlink != 1):
            raise SupportDenied("support_private_file_required")
        yield stream


def _write_new(path, value, *, mode=0o600):
    """Publish a complete record without overwriting earlier evidence."""
    import tempfile

    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".support-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_encode(value))
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        os.unlink(temporary)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _authority(case, reference, now, *, adult, prior=False):
    subject_id = "verifier_id" if adult else "profile_id"
    if prior:
        # The independent custodian verifies the original deletion record and its date.
        if (type(case) is not dict or set(case) != (PRIOR_ADULT_FIELDS if adult else PRIOR_CASE_FIELDS)
                or case["case"] != reference
                or case["status"] != "approved" or case["action"] != (PRIOR_ADULT_ACTION if adult else PRIOR_ACTION)
                or type(case[subject_id]) is not int or case[subject_id] <= 0
                or (not adult and (not isinstance(case["woodchuck_id"], str)
                    or re.fullmatch(r"WC-[A-Z0-9-]{1,29}", case["woodchuck_id"]) is None))
                or (adult and case["evidence_kind"] != "support_receipt")
                or case["evidence_kind"] not in ("support_receipt", "verified_deletion_record")
                or not _reference(case["deletion_evidence_ref"]) or not _reference(case["verified_by"])
                or case["deletion_evidence_ref"] in (reference, case["verified_by"])):
            raise SupportDenied("support_prior_case_invalid")
        created_at, deleted_at, verified_at = (_time(case[key]) for key in (
            "verifier_created_at" if adult else "profile_created_at", "deleted_at", "verified_at",
        ))
        if not created_at <= deleted_at <= verified_at <= now or now - verified_at >= AUTHORITY_WINDOW:
            raise SupportDenied("support_prior_case_dates_invalid")
        return created_at
    if (type(case) is not dict or set(case) != (ADULT_CASE_FIELDS if adult else CASE_FIELDS)
            or case["case"] != reference
            or case["status"] != "approved"
            or type(case[subject_id]) is not int or case[subject_id] <= 0
            or type(case["disputed"]) is not bool):
        raise SupportDenied("support_case_invalid")
    if adult:
        if not isinstance(case["email"], str):
            raise SupportDenied("support_case_invalid")
        try:
            email = validate_email(case["email"])
        except ValueError:
            raise SupportDenied("support_case_invalid") from None
        if email != case["email"]:
            raise SupportDenied("support_case_invalid")
    elif (not isinstance(case["woodchuck_id"], str)
          or re.fullmatch(r"WC-[A-Z0-9-]{1,29}", case["woodchuck_id"]) is None):
        raise SupportDenied("support_case_invalid")
    confirmation = case["confirmation"]
    if (type(confirmation) is not dict
            or set(confirmation) != {"case", subject_id, "requester_ref", "action", "reference", "at"}
            or confirmation["case"] != reference or type(confirmation[subject_id]) is not int
            or confirmation[subject_id] != case[subject_id]
            or confirmation["action"] != (ADULT_ACTION if adult else ACTION)
            or confirmation["requester_ref"] != case["requester_ref"]):
        raise SupportDenied("support_confirmation_mismatch")
    references = [case[key] for key in ("requester_ref", "identity_ref", "authority_ref")] + [confirmation["reference"]]
    if not all(_reference(value) for value in references) or len(set(references)) != len(references):
        raise SupportDenied("support_distinct_evidence_required")
    if (not _reference(case["verified_by"])
            or case["requester_ref"] in (case["verified_by"], case["reviewed_by"])
            or case["reviewed_by"] is not None and (
                not _reference(case["reviewed_by"]) or case["reviewed_by"] == case["verified_by"])
            or case["disputed"] and case["reviewed_by"] is None):
        raise SupportDenied("support_independent_review_required")
    created_at = _time(case["verifier_created_at"] if adult else case["profile_created_at"])
    verified_at = _time(case["verified_at"])
    confirmed_at = _time(confirmation["at"])
    if (not created_at <= verified_at <= confirmed_at <= now
            or now - verified_at >= AUTHORITY_WINDOW
            or now - confirmed_at >= CONFIRMATION_WINDOW):
        raise SupportDenied("support_case_expired")
    return created_at
