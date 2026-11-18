"""Record a named operator's request to execute one independently approved deletion."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import sys

from .support_case_files import (
    ADULT_CONFIG_DIR, CONFIG_DIR, SupportDenied, _authority, _check_parent_directories, _digest, _protected_file,
    _read_json, _reference, _write_new,
)


def _operator_uid():
    uid = os.getuid()
    if uid == 0 or uid != os.geteuid():
        raise SupportDenied("support_named_executor_required")
    return uid


def request_path(root, uid, reference, *, adult):
    if type(uid) is not int or uid <= 0 or not _reference(reference):
        raise SupportDenied("support_request_invalid")
    return Path(root) / str(uid) / ("adult" if adult else "parent") / (reference + ".json")


def record_request(approval_directory, approval_uid, request_root, reference, *, adult,
                   clock=lambda: datetime.now(timezone.utc)):
    uid = _operator_uid()
    if type(approval_uid) is not int or approval_uid < 0 or approval_uid == uid:
        raise SupportDenied("support_independent_approval_custody_required")
    destination = request_path(request_root, uid, reference, adult=adult)
    _check_parent_directories(destination, uid)
    directory = destination.parent.lstat()
    if (directory.st_uid != uid or directory.st_mode & 0o027
            or not directory.st_mode & stat.S_IXGRP
            or directory.st_gid != os.getegid() and not directory.st_mode & stat.S_ISGID):
        raise SupportDenied("support_private_request_directory_required")
    approvals = Path(approval_directory)
    with _protected_file(approvals / (reference + ".lock"), approval_uid) as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        with _protected_file(approvals / (reference + ".json"), approval_uid) as stream:
            case = _read_json(stream)
        if (type(case) is not dict or type(case.get("executor_uid")) is not int
                or case["executor_uid"] != uid):
            raise SupportDenied("support_operator_mismatch")
        _authority({key: value for key, value in case.items() if key != "executor_uid"},
                   reference, clock(), adult=adult)
        request = {"case": reference, "action": "execute-adult" if adult else "execute",
                   "approval_sha256": _digest(case)}
        try:
            _write_new(destination, request, mode=0o640)
        except FileExistsError:
            with _protected_file(destination, uid) as stream:
                if _read_json(stream) != request:
                    raise SupportDenied("support_request_mismatch") from None
    return {"case": reference, "status": "execution_requested"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("execute", "execute-adult"))
    parser.add_argument("case", help="Opaque Support case reference")
    args = parser.parse_args(argv)
    try:
        uid = _operator_uid()
        adult = args.action == "execute-adult"
        config_dir = ADULT_CONFIG_DIR if adult else CONFIG_DIR
        with _protected_file(config_dir / f"request-{uid}.json", 0) as stream:
            config = _read_json(stream)
        if type(config) is not dict or set(config) != {"approval_directory", "approval_uid", "request_root"}:
            raise SupportDenied("support_request_configuration_invalid")
        result = record_request(config["approval_directory"], config["approval_uid"],
                                config["request_root"], args.case, adult=adult)
        print(json.dumps(result, sort_keys=True))
        return 0
    except SupportDenied as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print("support_request_unavailable", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
