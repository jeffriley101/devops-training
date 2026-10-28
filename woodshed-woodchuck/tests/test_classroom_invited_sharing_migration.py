"""Actual SQLite/PostgreSQL c27 migrations; old rows and source history survive."""
from datetime import timedelta
from alembic import command
import pytest
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import classroom_models as cm, models as m, classroom_student_invitations as invitations
from app.operator_schema_compatibility import installed_revision, classroom_snapshot, assert_classroom_unchanged
from tests.test_weekly2_foundation_migration import db, config, revision, seed_history, rows, schema_contract, NOW
from test_classroom_student_invitations import stored, pin_hash, actor, student, issue, CURRENT

OLD, NEW = 'c26class001', 'c27class001'
TABLE = 'classroom_chart_director_shares'


def test_roundtrip_preserves_enrollment_shares_and_private_charts(db):
    command.upgrade(config(), OLD)
    seed_history(db)
    with Session(db) as s:
        s.add(m.PracticeChart(id=1, profile_id=1, practice_date=NOW.date(), minutes=17, instrument='Trumpet'))
        s.add(m.PracticeChart(id=2, profile_id=1, practice_date=NOW.date(), minutes=11, instrument='Trumpet'))
        s.add(cm.ClassroomTeachingAssignment(id=1, program_id=1, class_id=1, verifier_id=1,
            starts_at=NOW, granted_by_verifier_id=1))
        s.flush()
        table = Table(TABLE, MetaData(), autoload_with=s.connection())
        s.execute(table.insert().values(chart_id=1, class_id=1, assignment_id=1,
            membership_period_id=1, entitlement_id=1, account_declared_at=NOW,
            class_activation_at=NOW, selected_at=NOW))
        s.commit()
    before, shape = rows(db), schema_contract(db)
    command.upgrade(config(), NEW)
    with db.connect() as c:
        assert installed_revision(c, RuntimeError) == NEW
    after = rows(db)
    assert after[TABLE][0].pop('invitation_id') is None
    assert after == before
    command.downgrade(config(), OLD)
    with db.connect() as c:
        assert installed_revision(c, RuntimeError) == OLD
    assert rows(db) == before
    # Reflection order of foreign keys may change after SQLite table rebuild.
    after_shape = schema_contract(db)
    for value in (shape, after_shape):
        for table in value[0].values():
            table['foreign_keys'].sort(key=lambda fk: str(fk['constrained_columns']))
    assert after_shape == shape
    command.upgrade(config(), NEW)
    with db.connect() as c:
        assert installed_revision(c, RuntimeError) == NEW


def test_invitation_source_constraints_downgrade_and_operator_history(stored):
    db, clock = stored
    with Session(db) as s:
        invite = issue(s)
        invitations.accept(s, student=student(s), invitation_id=invite.id)
        ident = invite.id
        s.add(m.PracticeChart(id=1, profile_id=2, practice_date=NOW.date(), minutes=17, instrument='Trumpet'))
        s.commit()
    values = dict(chart_id=1, class_id=2, assignment_id=1, entitlement_id=1,
        account_declared_at=NOW, class_activation_at=NOW, selected_at=clock[0])
    for invalid in ({}, {'invitation_id': ident, 'membership_period_id': 1}, {'invitation_id': 99999}):
        with pytest.raises(IntegrityError), Session(db) as s:
            s.add(cm.ClassroomChartDirectorShare(**(values | invalid)))
            s.commit()
    with Session(db) as s:
        s.add(cm.ClassroomChartDirectorShare(**values, invitation_id=ident, withdrawn_at=clock[0]))
        s.commit()
    with db.connect() as c:
        assert installed_revision(c, RuntimeError) == CURRENT
    with pytest.raises(RuntimeError, match='in_use'):
        command.downgrade(config(), OLD)
    assert revision(db) == CURRENT
    with pytest.raises(RuntimeError, match='unexpected_classroom_row_change'), db.begin() as c:
        before = classroom_snapshot(c, CURRENT)
        c.execute(text(f'DELETE FROM {TABLE}'))
        assert_classroom_unchanged(c, CURRENT, before, RuntimeError)
    with db.connect() as c:
        assert len(classroom_snapshot(c, CURRENT)[TABLE]) == 1


@pytest.mark.parametrize('damage', ['column', 'index'])
def test_operator_rejects_tampered_invited_share_contract(db, damage):
    command.upgrade(config(), NEW)
    with db.begin() as c:
        c.execute(text(f'ALTER TABLE {TABLE} ADD COLUMN unexpected INTEGER' if damage == 'column'
                       else 'DROP INDEX uq_classroom_chart_director_share_open'))
    with db.connect() as c, pytest.raises(RuntimeError, match=TABLE + ':'):
        installed_revision(c, RuntimeError)


@pytest.mark.parametrize('revoke_first', [True, False])
def test_migrated_invited_share_read_and_revocation(stored, revoke_first):
    from sqlalchemy.orm import sessionmaker
    from app import classroom_chart_sharing as sharing
    from test_classroom_s1_postgres import ordered_wait
    db, clock = stored
    with Session(db) as s:
        invite = issue(s)
        invitations.accept(s, student=student(s), invitation_id=invite.id)
        ident = invite.id
        s.add_all([m.PracticeChart(id=1, profile_id=2, practice_date=NOW.date(), minutes=17, instrument='Trumpet'),
                   m.PracticeChart(id=2, profile_id=2, practice_date=NOW.date(), minutes=11, instrument='Trumpet')])
        s.flush()
        assert sharing.shared_charts(s, actor=actor(s), program_id=1, class_id=2) == []
        sharing.set_sharing(s, student=student(s), chart_id=1, assignment_ids=[1])
        s.commit()
    factory = sessionmaker(db)
    factory.control_engine = db
    clock[0] += timedelta(days=8)  # Accepted access persists past the acceptance deadline.
    def read(s):
        return len(sharing.shared_charts(s, actor=actor(s), program_id=1, class_id=2))
    def revoke(s):
        invitations.revoke(s, actor=actor(s), invitation_id=ident)
    if db.dialect.name == 'postgresql':
        first, second = (revoke, read) if revoke_first else (read, revoke)
        result = ordered_wait(factory, first, second)
        assert [r[0] for r in result] == ['ok', 'ok']
        assert (result[1][1] if revoke_first else result[0][1]) == (0 if revoke_first else 1)
    else:
        with factory() as s:
            assert read(s) == 1
            revoke(s)
            s.commit()
    with factory() as s:
        assert read(s) == 0
        assert s.scalar(select(cm.ClassroomStudentMembership.id).where(
            cm.ClassroomStudentMembership.profile_id == 2)) is None
