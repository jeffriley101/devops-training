"""Actual additive migration and history-preserving acknowledgement downgrade."""
from datetime import timedelta

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.db import Base
from app.weekly_models import WeeklyPlan, WeeklyTaskClaim
from tests.test_weekly2_foundation_migration import db, config, revision, rows, seed_history, schema_contract
from tests.test_weekly2_plans import plan_values, NOW

OLD, NEW = 'w25weekly003', 'w25weekly004'


def test_claim_migration_populated_history_empty_roundtrip_and_parity(db):
    command.upgrade(config(), OLD)
    seed_history(db)
    before, structure = rows(db), schema_contract(db)
    command.upgrade(config(), NEW)
    assert revision(db) == NEW
    assert {k:v for k,v in rows(db).items() if k!='weekly_task_claims'} == before
    assert rows(db)['weekly_task_claims'] == []
    with db.connect() as c:
        assert compare_metadata(MigrationContext.configure(c, opts={'compare_type': True}), Base.metadata)==[]
        from app.operator_schema_compatibility import installed_revision
        with pytest.raises(RuntimeError,match='revision_not_approved'):installed_revision(c,RuntimeError)
    current,triggers=schema_contract(db)
    assert {k:v for k,v in current.items() if k!='weekly_task_claims'}==structure[0]
    assert triggers==structure[1]
    command.downgrade(config(), OLD)
    assert revision(db)==OLD and rows(db)==before and schema_contract(db)==structure
    command.upgrade(config(), NEW)
    assert revision(db)==NEW and {k:v for k,v in rows(db).items() if k!='weekly_task_claims'}==before
    script=ScriptDirectory.from_config(config())
    assert script.get_heads()==[NEW] and script.get_revision(NEW).down_revision==OLD
    assert len(list(script.walk_revisions()))==66


def test_claim_fresh_constraints_refuse_populated_downgrade_and_cascade(db):
    command.upgrade(config(),'head')
    seed_history(db)
    with db.begin() as c:c.execute(WeeklyPlan.__table__.insert().values(**plan_values()))
    before=rows(db)
    for changes in [{'plan_id':999},{'lane':'logging'},{'lane':'homework'},{'lane':'practice_minutes'},
                    {'lane':'Board'},{'lane':None},{'claimed_at':None},{'plan_id':None}]:
        with pytest.raises((IntegrityError, DataError)),db.begin() as c:
            c.execute(WeeklyTaskClaim.__table__.insert().values(**({'plan_id':1,'lane':'board','claimed_at':NOW}|changes)))
        assert rows(db)==before
    with db.begin() as c:
        for lane in ('board','thirds'):
            c.execute(WeeklyTaskClaim.__table__.insert().values(plan_id=1,lane=lane,claimed_at=NOW))
    used=rows(db)
    with pytest.raises(IntegrityError),db.begin() as c:
        c.execute(WeeklyTaskClaim.__table__.insert().values(plan_id=1,lane='board',claimed_at=NOW))
    with pytest.raises(RuntimeError,match='weekly_claims_in_use'):command.downgrade(config(),OLD)
    assert revision(db)==NEW and rows(db)==used
    grants=used['reward_grants']
    with db.begin() as c:c.execute(text('DELETE FROM weekly_plans WHERE id=1'))
    assert rows(db)['weekly_task_claims']==[] and rows(db)['reward_grants']==grants
    command.downgrade(config(),OLD)
    assert revision(db)==OLD and rows(db)['reward_grants']==grants
