"""Apply or verify one documented Support deletion inside the application environment."""
import argparse
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import sys
import warnings

from .adult_account_deletion import AdultDeletionHold
from .db import SessionLocal
from .deletion_disposal import DeletionDataConflict
from .parent_deletion_support import (
    ApplicationDeletionTarget, OwnerShellApproval, _check_register, _require_application_secret,
    _require_connection, fulfill,
)
from .support_case_files import (
    ADULT_CONFIG_DIR, CONFIG_DIR, SupportDenied, _digest, _protected_file, _read_json, _reference,
)
from .support_deletion_request import request_path


@dataclass(frozen=True)
class OperatorRequest:
    root: Path

    def verify(self, case, reference, *, adult):
        uid = case["executor_uid"]
        path = request_path(self.root, uid, reference, adult=adult)
        try:
            with _protected_file(path, uid) as stream:
                request = _read_json(stream)
        except FileNotFoundError:
            raise SupportDenied("support_operator_request_missing") from None
        expected = {"action": "execute-adult" if adult else "execute", "case": reference,
                    "approval_sha256": _digest(case)}
        if request != expected:
            raise SupportDenied("support_request_mismatch")
        return uid


def _application_target(config):
    _require_application_secret(config["session_secret_sha256"])
    target = ApplicationDeletionTarget(config["database"], config["database_oid"], config["role"])
    _require_connection(SessionLocal.kw["bind"].url, target)
    return target


def _owner_case(action, reference, directory):
    if not sys.stdin.isatty():
        raise SupportDenied("support_interactive_owner_shell_required")
    # Never fall back to reading an echoed password or take the token from argv.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            review = OwnerShellApproval(getpass.getpass("Woodshed site-admin credential: "))
        except (EOFError, OSError, getpass.GetPassWarning):
            raise SupportDenied("support_interactive_owner_shell_required") from None
    review.require()
    _check_register(directory)
    uid = os.geteuid()
    with _protected_file(directory / ".target.json", uid) as stream:
        config = _read_json(stream)
    if (not isinstance(config, dict) or set(config) != {"database", "database_oid", "role", "session_secret_sha256"}
            or type(config["database_oid"]) is not int or config["database_oid"] <= 0):
        raise SupportDenied("support_application_configuration_invalid")
    target = _application_target(config)
    register = directory / "receipts"
    register.mkdir(mode=0o700, exist_ok=True)
    # Keep the existing stable approval lock across case edits and repeat calls.
    flags = os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    os.close(os.open(directory / (reference + ".lock"), flags, 0o600))
    return fulfill(SessionLocal, target=target, approval_directory=directory, approval_uid=uid, register=register,
                   reference=reference, adult=action.endswith("-adult"), status_only=action.startswith("status"),
                   operator_request=review)


def execute_case(action, reference, *, owner_dir=None):
    if action not in ("execute", "execute-adult", "status", "status-adult") or not _reference(reference):
        raise SupportDenied("support_case_invalid")
    service_uid = os.geteuid()
    if service_uid == 0 or os.getuid() != service_uid:
        raise SupportDenied("support_application_identity_required")
    if owner_dir is not None:
        return _owner_case(action, reference, owner_dir)
    adult = action.endswith("-adult")
    directory = ADULT_CONFIG_DIR if adult else CONFIG_DIR
    with _protected_file(directory / "application.json", 0) as stream:
        config = _read_json(stream)
    fields = {"service_uid", "database", "database_oid", "role", "session_secret_sha256",
              "request_root", "approval_directory", "approval_uid", "register"}
    if (type(config) is not dict or set(config) != fields or type(config["service_uid"]) is not int
            or config["service_uid"] != service_uid or type(config["approval_uid"]) is not int
            or config["approval_uid"] < 0 or config["approval_uid"] == service_uid
            or type(config["database_oid"]) is not int or config["database_oid"] <= 0):
        raise SupportDenied("support_application_configuration_invalid")
    target = _application_target(config)
    return fulfill(SessionLocal, target=target, approval_directory=Path(config["approval_directory"]),
                   approval_uid=config["approval_uid"], register=Path(config["register"]), reference=reference,
                   adult=adult, status_only=action.startswith("status"),
                   operator_request=OperatorRequest(Path(config["request_root"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("execute", "execute-adult", "status", "status-adult"))
    parser.add_argument("case", help="Opaque Support case reference")
    parser.add_argument("--owner-dir", type=Path, help="Private directory for an owner-verified case in Render Shell")
    args = parser.parse_args(argv)
    try:
        result = execute_case(args.action, args.case, owner_dir=args.owner_dir)
    except (SupportDenied, AdultDeletionHold) as error:
        print(str(error), file=sys.stderr)
    except DeletionDataConflict:
        print("support_account_data_conflict", file=sys.stderr)
    except Exception:
        print("support_operation_unavailable_or_uncertain", file=sys.stderr)
    else:
        print(json.dumps(result, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
