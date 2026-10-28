"""Storage-only invitation evidence on real migrations and synthetic accounts."""
from datetime import timedelta

from alembic import command
import pytest
from sqlalchemy import inspect, select, text, MetaData, Table
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import classroom as s1, classroom_s2 as s2, classroom_reporting as reporting
from app import classroom_chart_sharing as sharing, classroom_student_invitations as invitations
from app import classroom_models as cm, models as m
from app.age_models import AccountPrivacy
from app.age_privacy import utc
from app.operator_schema_compatibility import installed_revision, classroom_snapshot, assert_classroom_unchanged
from app.security import hash_pin
from tests.test_weekly2_foundation_migration import db, config, revision, seed_history, profile, rows, schema_contract, NOW

OLD, NEW = 'c25class001', 'c26class001'
CURRENT = 'c28class001'
TABLE = 'classroom_student_invitations'


@pytest.fixture(scope='module')
def pin_hash():
    return hash_pin('2468')


@pytest.fixture
def stored(db, monkeypatch, pin_hash):
    command.upgrade(config(), CURRENT)
    seed_history(db)
    profile(db, 2)
    profile(db, 3)
    clock = [NOW + timedelta(hours=1)]
    monkeypatch.setattr(s2, '_now', lambda: clock[0])
    monkeypatch.setattr(reporting, '_now', lambda: clock[0])
    for name in ('CLASSROOM_S1_ENABLED', 'CLASSROOM_S2_ENABLED', 'CLASSROOM_DIRECTOR_ENABLED'):
        monkeypatch.setenv(name, '1')
    with Session(db) as s:
        s.get(m.TrustedVerifier, 1).pin_hash = pin_hash
        s.add(m.TrustedVerifier(id=2, email='other@example.test', display_name='Other adult', pin_hash=pin_hash))
        for pid in (2, 3):
            s.get(m.WoodchuckProfile, pid).pin_hash = pin_hash
            s.add(AccountPrivacy(profile_id=pid, age_band='13to17', declared_at=NOW, public_from=NOW))
        s.add(cm.ClassroomClass(id=2, program_id=1, display_name='Synthetic invitation Class'))
        s.flush()
        s.add(cm.ClassroomClassState(class_id=2, program_id=1, state='active', enrollment_open=False,
            changed_by_verifier_id=1, created_at=NOW, updated_at=NOW))
        s.add(cm.ClassroomTeachingAssignment(id=1, program_id=1, class_id=2, verifier_id=1,
            starts_at=NOW, granted_by_verifier_id=1))
        s.commit()
    return db, clock


def actor(s, other=False):
    return s1.authenticate_adult(s, email='other@example.test' if other else 'wl2@example.invalid', pin='2468')


def student(s, pid=2):
    return s2.authenticate_student(s, woodchuck_id=f'WL2-{pid}', pin='2468')


def issue(s):
    return invitations.issue(s, actor=actor(s), program_id=1, class_id=2,
        assignment_id=1, profile_id=2)


def test_migration_round_trip_preserves_old_rows_and_schema(db):
    command.upgrade(config(), OLD)
    seed_history(db)
    with Session(db) as s:
        s.add(m.PracticeChart(id=1, profile_id=1, practice_date=NOW.date(), minutes=17, instrument='Trumpet'))
        s.add(cm.ClassroomTeachingAssignment(id=1, program_id=1, class_id=1, verifier_id=1,
            starts_at=NOW, granted_by_verifier_id=1))
        s.flush()
        # Frozen old schema; current ORM also includes the later c27 source.
        shares = Table('classroom_chart_director_shares', MetaData(), autoload_with=s.connection())
        s.execute(shares.insert().values(chart_id=1, class_id=1, assignment_id=1,
            membership_period_id=1, entitlement_id=1, account_declared_at=NOW,
            class_activation_at=NOW, selected_at=NOW))
        s.commit()
    before, shape = rows(db), schema_contract(db)
    command.upgrade(config(), NEW)
    with db.connect() as c:
        assert installed_revision(c, RuntimeError) == NEW
        assert classroom_snapshot(c, NEW)[TABLE] == []
    after = rows(db)
    assert after.pop(TABLE) == []
    assert after == before
    command.downgrade(config(), OLD)
    assert revision(db) == OLD and TABLE not in inspect(db).get_table_names()
    assert rows(db) == before and schema_contract(db) == shape
    command.upgrade(config(), NEW)
    assert revision(db) == NEW


def test_authenticated_lifecycle_is_durable_and_grants_nothing(stored):
    db, clock = stored
    before = rows(db)
    with Session(db) as s:
        row = issue(s)
        ident = row.id
        assert row.issued_by_verifier_id == 1 and row.assignment_id == 1
        assert utc(row.expires_at) - utc(row.issued_at) == invitations.LIFETIME
        assert row.accepted_at is None and row.revoked_at is None
        s.commit()
    with Session(db) as s:
        for proof in (2, student(s, 3)):
            with pytest.raises(s1.ClassroomDenied):
                invitations.accept(s, student=proof, invitation_id=ident)
        accepted = invitations.accept(s, student=student(s), invitation_id=ident)
        first = utc(accepted.accepted_at)
        clock[0] += timedelta(seconds=1)
        with pytest.raises(s1.ClassroomDenied):
            invitations.accept(s, student=student(s), invitation_id=ident)
        assert utc(s.get(cm.ClassroomStudentInvitation, ident).accepted_at) == first
        # Acceptance now offers the individual sharing option, but still creates
        # no shares, enrollment, reporting grants, or disclosure by itself.
        assert [r['assignment_id'] for r in sharing.choices(s, student(s))] == [1]
        assert sharing.lock_recipient(s, student(s), 1).invitation_id == ident
        s.commit()
    with Session(db) as s:
        with pytest.raises(s1.ClassroomDenied):
            invitations.revoke(s, actor=actor(s, other=True), invitation_id=ident)
        invitations.revoke(s, actor=actor(s), invitation_id=ident)
        s.commit()
    with Session(db) as s:
        row = s.get(cm.ClassroomStudentInvitation, ident)
        assert row.accepted_by_profile_id == 2 and row.revoked_by_verifier_id == 1
        assert row.accepted_at is not None and row.revoked_at is not None
        with pytest.raises(s1.ClassroomDenied):
            invitations.accept(s, student=student(s), invitation_id=ident)
    after = rows(db)
    before.pop(TABLE)
    assert len(after.pop(TABLE)) == 1
    assert after == before  # enrollment, reporting, charts, economy and history untouched
    with pytest.raises(RuntimeError, match='in_use'):
        command.downgrade(config(), OLD)
    assert revision(db) == CURRENT
    with pytest.raises(RuntimeError, match='unexpected_classroom_row_change'), db.begin() as c:
        before = classroom_snapshot(c, CURRENT)
        c.execute(text(f'DELETE FROM {TABLE}'))
        assert_classroom_unchanged(c, CURRENT, before, RuntimeError)
    with db.connect() as c:
        assert len(classroom_snapshot(c, CURRENT)[TABLE]) == 1


def test_issue_scope_credentials_and_acceptance_boundaries(stored, monkeypatch):
    db, clock = stored
    with Session(db) as s:
        for values in ({'actor': actor(s, True)}, {'actor': 1}, {'class_id': 1}, {'profile_id': 99999}):
            args = dict(actor=actor(s), program_id=1, class_id=2, assignment_id=1, profile_id=2)
            args.update(values)
            with pytest.raises(s1.ClassroomDenied):
                invitations.issue(s, **args)
        ident = issue(s).id
        s.commit()
    with Session(db) as s:
        stale = student(s)
        s.rollback()
        with pytest.raises(s1.ClassroomDenied):
            invitations.accept(s, student=stale, invitation_id=ident)
    with Session(db) as s:
        clock[0] += invitations.LIFETIME
        with pytest.raises(s1.ClassroomDenied):
            invitations.accept(s, student=student(s), invitation_id=ident)
        clock[0] -= invitations.LIFETIME
        for band in ('under13', 'unknown'):
            with s.begin_nested():
                s.get(AccountPrivacy, 2).age_band = band
                s.flush()
                with pytest.raises(s1.ClassroomDenied):
                    invitations.accept(s, student=student(s), invitation_id=ident)
        s.rollback()
    # Valid ordinary child consent must not authorize this separate Classroom path.
    from app import child_authorization
    from app.child_models import ConsentEvidence
    with monkeypatch.context() as child:
        child.setattr(child_authorization, 'under13_available', lambda: True)
        child.setattr(child_authorization, 'notice_policy', lambda: ('synthetic', '', 'a' * 64))
        with Session(db) as s:
            evidence = ConsentEvidence(profile_id=2, parent_email='parent@example.test',
                notice_version='synthetic', notice_sha256='a' * 64, approved_at=NOW, confirmed_at=NOW)
            s.add(evidence)
            s.flush()
            rule = s.get(AccountPrivacy, 2)
            rule.consent_id = evidence.id
            for band in ('under13', '13to17'):
                rule.age_band = band
                s.flush()
                assert child_authorization.consent_active(s, rule)
                with pytest.raises(s1.ClassroomDenied):
                    invitations.accept(s, student=student(s), invitation_id=ident)
                with pytest.raises(s1.ClassroomDenied):
                    issue(s)
            s.rollback()
    with Session(db) as s:
        for flag in ('CLASSROOM_DIRECTOR_ENABLED', 'CLASSROOM_S1_ENABLED', 'CLASSROOM_S2_ENABLED'):
            with monkeypatch.context() as off:
                off.delenv(flag)
                with pytest.raises(s1.ClassroomDenied):
                    issue(s)
                with pytest.raises(s1.ClassroomDenied):
                    invitations.accept(s, student=student(s), invitation_id=ident)
                with pytest.raises(s1.ClassroomDenied):
                    invitations.revoke(s, actor=actor(s), invitation_id=ident)
        s.get(cm.ClassroomEntitlement, 1).ends_at = clock[0]
        s.flush()
        with pytest.raises(s1.ClassroomDenied):
            invitations.accept(s, student=student(s), invitation_id=ident)
        # Removing authority never prevents the original issuer revoking evidence.
        assert invitations.revoke(s, actor=actor(s), invitation_id=ident).revoked_at is not None
        s.rollback()
    with Session(db) as s:
        assignment = s.get(cm.ClassroomTeachingAssignment, 1)
        assignment.ended_at, assignment.ended_reason, assignment.ended_by_verifier_id = clock[0], 'revoked', 1
        s.add(cm.ClassroomTeachingAssignment(id=2, program_id=1, class_id=2, verifier_id=2,
            starts_at=clock[0], granted_by_verifier_id=1))
        s.flush()
        with pytest.raises(s1.ClassroomDenied):
            invitations.accept(s, student=student(s), invitation_id=ident)
        with pytest.raises(s1.ClassroomDenied):
            invitations.revoke(s, actor=actor(s, True), invitation_id=ident)


def test_schema_rejects_wrong_scope_acceptor_and_invalid_timestamps(stored):
    db, _ = stored
    values = dict(program_id=1, class_id=2, assignment_id=1, profile_id=2,
                  issued_by_verifier_id=1, issued_at=NOW, expires_at=NOW + timedelta(days=7))
    for invalid in ({'class_id': 1}, {'profile_id': 99999}, {'expires_at': NOW},
                    {'accepted_at': NOW}, {'accepted_by_profile_id': 2},
                    {'accepted_at': NOW, 'accepted_by_profile_id': 3},
                    {'accepted_at': NOW + timedelta(days=7), 'accepted_by_profile_id': 2},
                    {'revoked_at': NOW}, {'revoked_at': NOW, 'revoked_by_verifier_id': 2}):
        with pytest.raises(IntegrityError), Session(db) as s:
            s.add(cm.ClassroomStudentInvitation(**(values | invalid)))
            s.commit()


@pytest.mark.parametrize('damage', ['column', 'index'])
def test_operator_rejects_changed_invitation_contract(db, damage):
    command.upgrade(config(), NEW)
    with db.begin() as c:
        c.execute(text(f'ALTER TABLE {TABLE} ADD COLUMN unexpected INTEGER' if damage == 'column'
                       else 'DROP INDEX ix_classroom_student_invitation_recipient'))
    with db.connect() as c, pytest.raises(RuntimeError, match='classroom_student_invitations:'):
        installed_revision(c, RuntimeError)
