"""Actual p21 upgrades preserve populated release data and free request paths."""
from datetime import datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from sqlalchemy import MetaData, Table, event, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app import classroom, models as m
from app.band_director_dashboard import dashboard_metrics
from app.db import Base
from app.security import hash_pin
from tests.test_persistent_team_cutover import BOUNDARY_AT, NOW, activate, apply, plan, seed, stage
from tests.test_persistent_team_migration import disposable_sqlite_configuration
from tests.test_team_families import disposable_url


def config():
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def c22_metadata():
    """Keep this explicit historical-head test independent of additive S2/S3."""
    s2_tables = {
        "classroom_entitlements", "classroom_class_states", "classroom_entry_codes",
        "classroom_membership_holds", "classroom_s2_audit_events",
        "classroom_reporting_periods", "classroom_s3_audit_events",
    }
    expected = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in s2_tables:
            table.to_metadata(expected)
    return expected


def legacy_snapshot(connection):
    """Compare every old table, ID, value and evidence row, not selected counts."""
    from app.persistent_team_cutover import normalized
    metadata = MetaData()
    result = {}
    for name in sorted(inspect(connection).get_table_names()):
        if name == "alembic_version" or name.startswith("classroom_"):
            continue
        table = Table(name, metadata, autoload_with=connection, resolve_fks=False)
        result[name] = normalized([
            dict(row) for row in connection.execute(
                select(table).order_by(*table.primary_key.columns)
            ).mappings()
        ])
    return result


def add_identity_and_personal_history(engine):
    with Session(engine) as session:
        adult = m.TrustedVerifier(email="free-director@example.invalid", display_name="Free Director",
                                  pin_hash=hash_pin("2468"))
        recipient = m.TrustedVerifier(email="recipient@example.invalid", display_name="Future Owner",
                                      pin_hash=hash_pin("1357"))
        session.add_all([adult, recipient])
        session.flush()
        organization = m.Organization(name="Historical Program Name", organization_type="school",
                                       created_by_verifier_id=adult.id)
        session.add(organization)
        session.flush()
        session.add_all([
            m.StudentVerifierConnection(profile_id=24, verifier_id=adult.id, role="band_director",
                                        status="accepted", accepted_at=NOW - timedelta(days=30)),
            m.StudentOrganizationMembership(organization_id=organization.id, profile_id=24),
            m.VerifierOrganizationMembership(organization_id=organization.id, verifier_id=adult.id,
                                             role="band_director"),
            m.ProfileCapability(profile_id=1, capability="band_director"),
            m.AccountPrivacy(profile_id=2, age_band="under13", declared_at=NOW),
            m.TesterEnrollment(profile_id=3, cohort_key="PILOT-D1", joined_at=NOW),
            m.TesterEnrollment(profile_id=4, cohort_key="C001", joined_at=NOW),
            m.WoodchuckState(profile_id=24, state_json={"synthetic": "preserve"}, revision=7),
        ])
        session.add_all([
            m.AccountPrivacy(profile_id=pid, age_band="adult", declared_at=NOW - timedelta(days=40),
                             public_from=NOW - timedelta(days=40))
            for pid in (1, 3, 4, 24)
        ])
        billing = m.BillingAccount(profile_id=1)
        session.add(billing)
        session.flush()
        membership = m.Membership(billing_account_id=billing.id, source="manual", starts_at=NOW)
        session.add(membership)
        session.flush()
        session.add(m.MembershipSeat(membership_id=membership.id, profile_id=1, slot_number=1))
        session.commit()
        return adult.id, organization.id, recipient.id


def free_paths_without_classroom_queries(engine, monkeypatch, *, staged=False):
    from app import main, session_revocations, team_authority, verifier_routes
    from app.memberships import student_has_full_access
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(verifier_routes, "SessionLocal", factory)
    monkeypatch.setattr(session_revocations, "SessionLocal", factory)
    monkeypatch.delenv("CLASSROOM_S1_ENABLED", raising=False)
    statements = []
    instant = NOW
    original_datetime = team_authority.datetime

    class FixtureDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    if staged:
        # Exercise both real sides of the already-approved staging fence.
        # The wall clock is explicit; the authority gate itself is unchanged.
        monkeypatch.setattr(team_authority, "datetime", FixtureDatetime)

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record)
    try:
        with factory() as session:
            assert [student_has_full_access(session, pid, at=NOW) for pid in (1, 2, 3, 4)] == [True, False, True, True]
            adult = classroom.authenticate_adult(session, email="free-director@example.invalid", pin="2468")
            expected = dashboard_metrics(session, verifier_id=adult._verifier_id, today=NOW.date())
            resolved = classroom.director_sections(session, actor=adult, today=NOW.date())
            assert resolved["connected_students"] == expected
            assert len(expected["students"]) == 1
            assert resolved["my_classes"] == {
                "status": "disabled", "paid_capabilities": "not_implemented",
                "student_reporting": "not_implemented",
            }
        client = TestClient(main.app)
        try:
            assert client.post("/trusted-verifiers/login", data={
                "email": "free-director@example.invalid", "pin": "2468",
            }).status_code == 200
            for path in ("/band-director/dashboard", "/trusted-verifiers/dashboard"):
                response = client.get(path)
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
            if staged:
                instant = BOUNDARY_AT
                with factory() as session:
                    adult = classroom.authenticate_adult(session, email="free-director@example.invalid", pin="2468")
                    with pytest.raises(HTTPException) as denied:
                        classroom.director_sections(session, actor=adult, today=NOW.date())
                    assert denied.value.status_code == 503
                    assert "Team transition is awaiting activation" in denied.value.detail
                response = client.get("/band-director/dashboard")
                assert response.status_code == 503
                assert "Team transition is awaiting activation" in response.text
                # A band_director connection is not a verifier connection. The
                # existing empty verifier page has no Team context to resolve.
                response = client.get("/trusted-verifiers/dashboard")
                assert response.status_code == 200
                assert response.context["connections"] == []
                assert response.context["student"] is None
        finally:
            client.close()
        assert not any("classroom_" in statement for statement in statements)
        return expected
    finally:
        event.remove(engine, "before_cursor_execute", record)
        if staged:
            monkeypatch.setattr(team_authority, "datetime", original_datetime)


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("pta_state", ["dormant", "staged", "active"])
def test_populated_upgrade_preserves_free_paths_and_pta_operator_compatibility(tmp_path, monkeypatch, backend, pta_state):
    from app.persistent_team_cutover import CutoverError, verify_cutover
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(config(), "p21team001")
    engine = seed(url, migrated=True)
    try:
        adult_id, organization_id, recipient_id = add_identity_and_personal_history(engine)
        db = (url, engine)
        approved = plan(db)
        if pta_state == "staged":
            stage(db, approved)
        elif pta_state == "active":
            receipt = apply(db, approved)
        with engine.connect() as connection:
            assert not any(name.startswith("classroom_") for name in inspect(connection).get_table_names())
            before = legacy_snapshot(connection)
            assert len(before["teams"]) == 10
            assert len(before["team_memberships"]) == 40
            if pta_state == "active":
                assert any(row["team_roster_frozen_at"] for row in before["contest_weeks"])
        free_before = free_paths_without_classroom_queries(engine, monkeypatch, staged=pta_state == "staged")
        monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1")
        with Session(engine) as session:
            adult = classroom.authenticate_adult(session, email="free-director@example.invalid", pin="2468")
            with pytest.raises(DBAPIError):
                classroom.my_classes(session, actor=adult)
        monkeypatch.delenv("CLASSROOM_S1_ENABLED")

        command.upgrade(config(), "c22class001")
        with engine.connect() as connection:
            assert legacy_snapshot(connection) == before
            classroom_tables = [name for name in inspect(connection).get_table_names() if name.startswith("classroom_")]
            assert len(classroom_tables) == 8
            for name in classroom_tables:
                assert connection.scalar(text(f"SELECT count(*) FROM {name}")) == 0
            assert compare_metadata(MigrationContext.configure(connection), c22_metadata()) == []
            if backend == "sqlite":
                assert list(connection.execute(text("PRAGMA foreign_key_check"))) == []
        # Installation approval is separate from the immutable plan contract.
        # New c22 plans describe c22; old unstaged plans need fresh review.
        if pta_state == "active":
            assert verify_cutover(url, approved, approved["plan_sha256"], receipt)["passed"]
        else:
            assert plan(db)["content"]["revision"] == "c22class001"
            with pytest.raises(CutoverError, match="plan_schema_revision_changed"):
                stage(db, approved)
        with engine.connect() as connection:
            assert legacy_snapshot(connection) == before
        assert free_paths_without_classroom_queries(engine, monkeypatch, staged=pta_state == "staged") == free_before

        # Empty-only downgrade changes no old data, even with live PTA history.
        command.downgrade(config(), "p21team001")
        with engine.connect() as connection:
            assert legacy_snapshot(connection) == before
        command.upgrade(config(), "c22class001")
        with Session(engine) as session:
            session.add(m.ClassroomProgram(organization_id=organization_id, owner_verifier_id=adult_id))
            session.commit()
        # Real management on populated p21 history has no Team, student, free
        # relationship, privacy, personal benefit, or cohort side effects.
        monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1")
        with Session(engine) as session:
            adult = classroom.authenticate_adult(session, email="free-director@example.invalid", pin="2468")
            room = classroom.create_class(session, actor=adult, program_id=organization_id, display_name="Synthetic Class")
            grant = classroom.grant_role(session, actor=adult, program_id=organization_id,
                                         verifier_id=adult_id, role="code_manager")
            assignment = classroom.assign_teacher(session, actor=adult, program_id=organization_id,
                                                   class_id=room.id, verifier_id=adult_id)
            classroom.end_teaching(session, actor=adult, program_id=organization_id,
                                   assignment_id=assignment.id, reason="departed")
            classroom.revoke_role(session, actor=adult, program_id=organization_id, grant_id=grant.id)
            proposal = classroom.propose_transfer(session, actor=adult, program_id=organization_id,
                                                   recipient_id=recipient_id)
            proposal_id = proposal.id
            session.commit()
        with Session(engine) as session:
            recipient = classroom.authenticate_adult(session, email="recipient@example.invalid", pin="1357")
            classroom.accept_transfer(session, actor=recipient, program_id=organization_id, transfer_id=proposal_id)
            session.commit()
        with engine.connect() as connection:
            assert legacy_snapshot(connection) == before
            assert connection.scalar(text("SELECT count(*) FROM classroom_audit_events")) == 7
        with pytest.raises(RuntimeError, match="classroom_authority_or_history_in_use"):
            command.downgrade(config(), "p21team001")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c22class001"
            assert connection.scalar(text("SELECT owner_verifier_id FROM classroom_programs")) == recipient_id
            assert legacy_snapshot(connection) == before
        if pta_state == "staged":
            # The original staged approval survives both additive migrations.
            receipt = activate(db, approved)
            assert verify_cutover(url, approved, approved["plan_sha256"], receipt)["passed"]
    finally:
        engine.dispose()


def test_only_additive_classroom_revision_follows_verified_release_chain():
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == ["c24class001"]
    assert script.get_revision("c23class001").down_revision == "c22class001"
    assert script.get_revision("c22class001").down_revision == "p21team001"
    assert script.get_revision("p21team001").down_revision == "p20team001"
    assert script.get_revision("p20team001").down_revision == "f19arcade001"
