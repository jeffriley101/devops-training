"""Command boundaries that can be checked without database privileges or a running service."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import support_deletion_request as operator
from app import support_deletion_service as service
from app.support_case_files import ACTION, SupportDenied, _protected_file


REF = "request-one"


def test_operator_starts_without_secrets_or_third_party_packages():
    check = """
import sys
from app import support_deletion_request
assert 'app.db' not in sys.modules and 'app.session_config' not in sys.modules
assert not any(name.startswith('sqlalchemy') for name in sys.modules)
support_deletion_request.main(['--help'])
"""
    result = subprocess.run([sys.executable, "-B", "-E", "-S", "-c", check], env={},
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "{execute,execute-adult}" in result.stdout


@pytest.mark.parametrize("argv", [
    ["status", REF], ["cleanup-prior", REF], ["execute", REF, "--profile-id", "24"],
    ["execute-adult", REF, "--sql", "SELECT 1"],
])
def test_operator_accepts_only_the_deletion_action_and_one_case(argv):
    with pytest.raises(SystemExit) as exc:
        operator.main(argv)
    assert exc.value.code == 2


@pytest.mark.parametrize("field", ["database_url", "session_secret"])
def test_operator_refuses_credentials_in_root_config(monkeypatch, capsys, field):
    uid = 1234
    monkeypatch.setattr(operator, "_operator_uid", lambda: uid)
    config = {"approval_directory": "/approvals", "approval_uid": 1235, "request_root": "/requests",
              field: "synthetic-sensitive-value"}

    def config_file(path, owner):
        assert path == operator.ADULT_CONFIG_DIR / f"request-{uid}.json" and owner == 0
        return BytesIO(json.dumps(config).encode())

    monkeypatch.setattr(operator, "_protected_file", config_file)
    monkeypatch.setattr(operator, "record_request", lambda *a, **kw: pytest.fail("Invalid config accepted"))
    assert operator.main(["execute-adult", REF]) == 1
    assert capsys.readouterr().err.strip() == "support_request_configuration_invalid"


def test_different_approved_case_contents_cannot_replace_an_operator_request(tmp_path, monkeypatch):
    # Map the two file custodians onto the temporary test directory. Exercise
    # actual exclusive file publication and real approval locks on each attempt.
    owner = os.getuid()
    verifier = owner + 1
    approvals = tmp_path / "approvals"
    approvals.mkdir(mode=0o700)
    destination = tmp_path / "requests" / (REF + ".json")
    destination.parent.mkdir(mode=0o750)
    destination.parent.chmod(0o750)
    monkeypatch.setattr(operator, "_operator_uid", lambda: owner)
    monkeypatch.setattr(operator, "request_path", lambda *args, **kwargs: destination)
    monkeypatch.setattr(operator, "_protected_file", lambda path, expected: _protected_file(path, owner))
    now = datetime.now(timezone.utc)
    case = dict(case=REF, status="approved", profile_id=42, woodchuck_id="WC-SYNTHETIC",
                profile_created_at=(now-timedelta(days=1)).isoformat(), requester_ref="requester-ref",
                identity_ref="identity-proof", authority_ref="authority-ref", verified_by="verifier-a",
                verified_at=now.isoformat(), confirmation=dict(case=REF, profile_id=42,
                    requester_ref="requester-ref", action=ACTION, reference="confirm-one", at=now.isoformat()),
                disputed=False, reviewed_by=None, executor_uid=owner)
    lock = approvals / (REF + ".lock")
    lock.touch(mode=0o600)
    approval = approvals / (REF + ".json")
    approval.touch(mode=0o600)
    approval.write_text(json.dumps(case))

    def record():
        return operator.record_request(approvals, verifier, destination.parent, REF, adult=False, clock=lambda: now)

    assert record() == {"case": REF, "status": "execution_requested"}
    initial = (destination.stat().st_ino, destination.read_bytes())
    assert destination.stat().st_mode & 0o777 == 0o640
    assert record()["status"] == "execution_requested"
    case["confirmation"]["reference"] = "confirm-two"
    approval.write_text(json.dumps(case))
    with pytest.raises(SupportDenied, match="^support_request_mismatch$"):
        record()
    assert (destination.stat().st_ino, destination.read_bytes()) == initial


@pytest.mark.parametrize(("uid", "url", "expected"), [
    (77, "sqlite://", "support_application_configuration_invalid"),
    (78, "sqlite://", "support_connection_unqualified"),
    (78, "postgresql+psycopg://different@/deletion_test?host=/tmp", "support_connection_unqualified"),
    (78, "postgresql+psycopg://application@/different?host=/tmp", "support_connection_unqualified"),
])
def test_service_rejects_wrong_operator_or_connection_before_execution(monkeypatch, capsys, uid, url, expected):
    config = {"service_uid": 78, "database": "deletion_test", "database_oid": 54321, "role": "application",
              "session_secret_sha256": sha256(b"synthetic-isolated-application-secret").hexdigest(),
              "approval_directory": "/approvals", "approval_uid": 79, "request_root": "/requests",
              "register": "/register"}
    monkeypatch.setattr(service.os, "getuid", lambda: uid)
    monkeypatch.setattr(service.os, "geteuid", lambda: uid)
    monkeypatch.setenv("SESSION_SECRET", "synthetic-isolated-application-secret")
    monkeypatch.setattr(service, "_protected_file", lambda path, owner: BytesIO(json.dumps(config).encode()))
    monkeypatch.setattr(service, "fulfill", lambda *a, **kw: pytest.fail("Unqualified service reached execution"))
    engine = create_engine(url)
    try:
        monkeypatch.setattr(service, "SessionLocal", sessionmaker(bind=engine))
        assert service.main(["execute", REF]) == 1
        assert capsys.readouterr().err.strip() == expected
    finally:
        engine.dispose()
