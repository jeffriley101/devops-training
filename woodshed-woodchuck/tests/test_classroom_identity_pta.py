"""C33's additive storage preserves P31 receipts, never changes new issuance."""
from copy import deepcopy

from alembic import command
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import pta_receipt_schema as issuance
from app.classroom_identity_models import ClassroomRosterIdentity as Identity, ClassroomRosterIdentityAudit as Audit
from app.classroom_models import ClassroomClass, ClassroomStudentMembership, ClassroomProgram
from tests.test_c22_operational_compatibility import all_rows, calendar, config, cutover, set_clock
from tests.test_persistent_team_cutover import NOW, activate, plan, stage
from tests.test_weekly2_operator_compatibility import install, migrated_db


def test_original_p31_issuance_survives_private_identity_storage(migrated_db, monkeypatch):
    db = migrated_db
    install(db, 'w25weekly005')
    command.upgrade(config(), 'p31pta001')
    set_clock(monkeypatch, NOW)
    approved = plan(db); stage(db, approved); receipt = activate(db, approved)
    frozen = deepcopy((approved, receipt, all_rows(db[1])))
    command.upgrade(config(), 'c33identity001')
    with Session(db[1]) as session:
        anchor, room = session.execute(select(ClassroomStudentMembership, ClassroomClass).join(
            ClassroomClass, ClassroomClass.id == ClassroomStudentMembership.class_id)).first()
        program = session.get(ClassroomProgram, room.program_id)
        identity = Identity(program_id=room.program_id, profile_id=anchor.profile_id,
            school_name='Synthetic Retained School Name', version=1, updated_at=NOW)
        session.add(identity); session.flush()
        session.add(Audit(identity_id=identity.id, program_id=room.program_id, profile_id=anchor.profile_id,
            class_id=room.id, actor_verifier_id=program.owner_verifier_id, version=1, action='created', occurred_at=NOW))
        session.commit()
    after = all_rows(db[1])
    assert {name: values for name, values in after.items() if name not in {
        'classroom_roster_identities', 'classroom_roster_identity_audits',
        'classroom_enrollment_invitations', 'classroom_enrollment_audit_events'}} == frozen[2]
    assert after[issuance.NAME] == frozen[2][issuance.NAME]
    assert cutover.verify_cutover(db[0], approved, approved['plan_sha256'], receipt)['passed']
    calendar.provision(db[0], seasons=['halloween-2026'])
    assert all_rows(db[1]) == after and (approved, receipt) == frozen[:2]


def test_c33_keeps_new_pta_activation_p31_only(migrated_db, monkeypatch):
    db = migrated_db
    install(db, 'w25weekly005'); command.upgrade(config(), 'p31pta001')
    set_clock(monkeypatch, NOW)
    approved = plan(db); stage(db, approved)
    command.upgrade(config(), 'c33identity001')
    before = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match='receipt_issuance_schema_required'):
        activate(db, approved)
    assert all_rows(db[1]) == before and before[issuance.NAME] == []
