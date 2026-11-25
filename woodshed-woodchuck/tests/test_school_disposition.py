"""Portable regression checks on the normal authenticated two-Program PostgreSQL fixture."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from app import classroom_school_authorization as school, classroom_s2 as s2, school_disposition as disposition
from app.support_case_files import SupportDenied
from test_classroom_enrollment_acceptance_postgres import classroom_pg, adult_pin_hash
from test_classroom_s2_postgres import s2_pg
from test_classroom_s1 import actor


def test_ended_receipt_preview_binds_the_actual_authority_end(s2_pg):
    with s2_pg() as db:
        now = s2._now()
        original = school._latest(db, 1, school.school_year(now))
        case = {"operation": "retire_receipt", "program": 1, "record": original.id, "class": None,
                "authorization_end": now.isoformat()}
        with pytest.raises(SupportDenied, match="active_or_changed"):
            disposition.inspect_record(db, case, lambda: now, lock=False)
        school.revoke(db, actor=actor(db), program_id=1, record_id=original.id,
                      previous=school.revision(original), reason="authority_revoked")
        db.commit()
    with s2_pg() as db:
        row, fingerprint, expected_head = disposition.inspect_record(db, case, lambda: now, lock=False)
        assert row.id == case["record"] and fingerprint and expected_head[1] == school.revision(None)
        case["authorization_end"] = (now - timedelta(seconds=1)).isoformat()
        with pytest.raises(SupportDenied, match="active_or_changed"):
            disposition.inspect_record(db, case, lambda: now, lock=False)
        assert db.scalar(text("SELECT count(*) FROM classroom_school_authorizations")) == 2


def test_raw_hold_insert_requires_actor_and_independent_witness(s2_pg):
    from secrets import token_hex
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.pool import NullPool
    from app import classroom_hold_guard as guard
    from test_classroom_s2 import ready, join

    role = "c37_regression_" + token_hex(8)
    password = token_hex(32)
    with s2_pg.begin() as db:
        ready(db)
        member = join(db)
        member_id = member.id
        engine = db.get_bind()
        guard.validate(db.connection(), RuntimeError)
        db.connection().exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        db.execute(text(f"GRANT SELECT ON classroom_classes,classroom_student_memberships TO {role}"))
        db.execute(text("GRANT INSERT(membership_id,state,reason,held_at,held_by_verifier_id) "
                        f"ON classroom_membership_holds TO {role}"))
    writer = create_engine(engine.url.set(username=role,password=password), poolclass=NullPool)
    try:
        for staff_id in (None, 1):
            with pytest.raises(DBAPIError) as denial:
                with writer.begin() as connection:
                    connection.execute(text("INSERT INTO classroom_membership_holds "
                        "(membership_id,state,reason,held_at,held_by_verifier_id) "
                        "VALUES (:member,'removed','conduct',now(),:staff)"), {"member":member_id,"staff":staff_id})
            assert denial.value.orig.sqlstate == "42501"
        with s2_pg() as db:
            assert db.scalar(text("SELECT count(*) FROM classroom_membership_holds")) == 0
    finally:
        writer.dispose()
        with engine.begin() as con:
            con.exec_driver_sql(f"REVOKE ALL ON classroom_classes,classroom_student_memberships,classroom_membership_holds FROM {role}")
            con.exec_driver_sql(f"DROP ROLE {role}")


def test_restricted_route_does_not_use_shared_writer_without_credential(s2_pg, monkeypatch):
    from fastapi import HTTPException
    from app.classroom_writer_sessions import operation_session

    monkeypatch.delenv("C37_HOLD_WRITER_PASSWORD", raising=False)
    with pytest.raises(HTTPException) as denial:
        with operation_session("reinstate", s2_pg):
            pytest.fail("Unexpected write session")
    assert denial.value.status_code == 503
    with s2_pg() as fresh:
        assert fresh.scalar(text("SELECT count(*) FROM classroom_membership_holds")) == 0


def test_pilot_approval_pins_one_key_and_signs_the_exact_purpose(tmp_path):
    import hashlib
    import os
    import subprocess

    from app.support_case_files import _encode

    key = tmp_path / "ephemeral"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
    public = " ".join(key.with_suffix(".pub").read_text().split()[:2])
    pins = tmp_path / "signers"
    pins.write_text("administrator " + public + "\n")
    pins.chmod(0o600)
    config = dict(owner_uid=os.getuid(), register=str(tmp_path), signers=str(pins),
                  signers_sha256=hashlib.sha256(pins.read_bytes()).hexdigest())
    case = dict(case="SYNTHETIC0001", approval_contract=disposition.CASE_APPROVAL)
    payload = tmp_path / "manifest"
    payload.write_bytes(_encode(case))
    for namespace in (disposition.DESTINATION_APPROVAL, disposition.CASE_APPROVAL):
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", namespace, payload], check=True)
        payload.with_suffix(".sig").replace(tmp_path / "SYNTHETIC0001.administrator.sig")
        if namespace == disposition.DESTINATION_APPROVAL:
            with pytest.raises(SupportDenied, match="signature_required"):
                disposition.verify_approval(config, case, tmp_path, disposition.CASE_APPROVAL)
        else:
            disposition.verify_approval(config, case, tmp_path, disposition.CASE_APPROVAL)
    with pytest.raises(SupportDenied, match="signature_required"):
        disposition.verify_approval(config, dict(case, program=2), tmp_path, disposition.CASE_APPROVAL)
    for invalid in ("administrator ssh-ed25519 AAAA", "administrator ssh-ed25519 !", "d1 " + public,
                    "administrator " + public + "\nadministrator " + public):
        pins.write_text(invalid + "\n")
        config["signers_sha256"] = hashlib.sha256(pins.read_bytes()).hexdigest()
        with pytest.raises(SupportDenied, match="administrator_pin_invalid"):
            disposition.administrator_pin(config)
