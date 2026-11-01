"""Focused c32 compatibility; no repeated P31 issuance security audit."""
from copy import deepcopy

from alembic import command
import pytest

from app import pta_receipt_schema as issuance
from tests.test_c22_operational_compatibility import all_rows, calendar, config, cutover, set_clock
from tests.test_persistent_team_cutover import NOW, activate, plan, stage
from tests.test_weekly2_operator_compatibility import install, migrated_db


def test_original_p31_receipt_and_calendar_survive_only_the_new_additive_schema(migrated_db, monkeypatch):
    db = migrated_db
    install(db, 'w25weekly005')
    command.upgrade(config(), 'p31pta001')
    set_clock(monkeypatch, NOW)
    approved = plan(db)
    stage(db, approved)
    receipt = activate(db, approved)
    assert cutover.verify_cutover(db[0], approved, approved['plan_sha256'], receipt)['passed']
    frozen = deepcopy((approved, receipt, all_rows(db[1])))
    command.upgrade(config(), 'c32enroll001')
    after = all_rows(db[1])
    assert {name: values for name, values in after.items() if not name.startswith('classroom_enrollment_')} == frozen[2]
    assert after[issuance.NAME] == frozen[2][issuance.NAME]
    assert cutover.verify_cutover(db[0], approved, approved['plan_sha256'], receipt)['passed']
    calendar.provision(db[0], seasons=['halloween-2026'])
    assert all_rows(db[1]) == after and (approved, receipt) == frozen[:2]
    forged = deepcopy(receipt)
    forged['issuance']['receipt_id'] = '0' * 32
    with pytest.raises(cutover.CutoverError, match='activation_receipt_issuance_mismatch'):
        cutover.verify_cutover(db[0], approved, approved['plan_sha256'], forged)


def test_c32_does_not_relax_the_p31_only_new_issuance_contract(migrated_db, monkeypatch):
    db = migrated_db
    install(db, 'w25weekly005')
    command.upgrade(config(), 'p31pta001')
    set_clock(monkeypatch, NOW)
    approved = plan(db)
    stage(db, approved)
    command.upgrade(config(), 'c32enroll001')
    before = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match='receipt_issuance_schema_required'):
        activate(db, approved)
    assert all_rows(db[1]) == before and before[issuance.NAME] == []
