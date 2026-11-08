"""Focused feature integration with the accepted C35 authorization boundaries."""
from datetime import timedelta
import json
import os

from alembic import command
import pytest
from sqlalchemy import select

from app import classroom as s1, classroom_school_authorization as school
from app import classroom_guardian_authorization as guardian, classroom_chart_consent as chart_consent
from app import classroom_enrollment_views as enrollment, classroom_directors_choice as choice
from app.classroom_school_models import ClassroomSchoolAuthorization
from app.models import PracticeChart
from app.operator_schema_compatibility import installed_revision
from app.operator_classroom_choice_contract import C36
from test_classroom_directors_choice import (
    choice_data, guardian_data, migrated_db, captured, adult_pin_hash, actor,
    saved, student_read, director, CUSTOM, PATH,
)
from test_classroom_student_home import student_home, issue, start, page
from test_classroom_guardian_authorization import fully_authorize, read, parent_proof
from school_approval_fixture import approve_programs
from tests.test_c22_operational_compatibility import all_rows, config
from tests.test_c36_operator_compatibility import sequences


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_owned_homework_and_class_assignments_do_not_bypass_school_revocation(
        choice_data, student_home, monkeypatch):
    refs = choice_data
    assert refs is student_home
    approvals = approve_programs(refs.factory, monkeypatch, reviewer_id=1, approver_id=2)
    fully_authorize(refs)
    issue(refs); start(refs); saved(refs)
    with refs.factory() as session:
        # Positive disclosure before revocation, not a negative masked by absent
        # guardian/student permission or an unapproved legal test fixture.
        assert read(session, refs)['values']['fields']
        session.commit()
        approval = session.get(ClassroomSchoolAuthorization, approvals[1])
        refs.clock[0] += timedelta(seconds=2)
        school.revoke(session, actor=actor(session, 2), program_id=1,
            record_id=approval.id, previous=school.revision(approval), reason='authority_revoked')
        session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        with pytest.raises(s1.ClassroomDenied):
            enrollment.director_class(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id)
        assert student_read(session, refs)['assignments'][0]['content']['title'] == CUSTOM['title']
        assigned = choice.director_page(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id)
        encoded = json.dumps(assigned)
        for secret in (refs.woodchuck_id, 'WC-AGE-B', 'parent-a@example.test', 'saved A', 'saved B'):
            assert secret not in encoded
        assert set(assigned) == {'program_id', 'program_name', 'class_id', 'class_name', 'week', 'through',
                                 'can_create', 'assignments', 'next_before'}
    before = all_rows(refs.factory.engine)
    assert CUSTOM['title'] in page(refs) and 'data-homework-slot="1"' in page(refs)
    assert all_rows(refs.factory.engine) == before


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_populated_c35_upgrade_preserves_school_and_guardian_withdrawals(
        choice_data, student_home, monkeypatch, tmp_path):
    refs = choice_data
    approve_programs(refs.factory, monkeypatch, reviewer_id=1, approver_id=2)
    fully_authorize(refs)
    issue(refs); start(refs)
    with refs.factory() as session:
        session.add(PracticeChart(profile_id=refs.profile_id, practice_date=refs.clock[0].date(),
            created_at=refs.clock[0], instrument='Flute', source='p-book', minutes=15,
            note='synthetic retained private practice'))
        refs.clock[0] += timedelta(seconds=2)
        guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
            class_id=refs.class_id, purpose='reporting')
        approval = session.scalar(select(ClassroomSchoolAuthorization).where(
            ClassroomSchoolAuthorization.program_id == 1))
        school.revoke(session, actor=actor(session, 2), program_id=1, record_id=approval.id,
            previous=school.revision(approval), reason='authority_revoked')
        session.commit()
    # New assignment tables are still empty; build the exact populated C35
    # source with the migration's authorized disposable empty-only round trip.
    command.downgrade(config(), 'c35school001')
    before, counters = all_rows(refs.factory.engine), sequences(refs.factory.engine)
    with refs.factory.engine.connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'c35school001'
    command.upgrade(config(), 'c36choice001')
    after, after_counters = all_rows(refs.factory.engine), sequences(refs.factory.engine)
    assert {name: after[name] for name in before} == before
    assert all(after[name] == [] for name in C36)
    assert {name: after_counters[name] for name in counters} == counters
    with refs.factory.engine.connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'c36choice001'
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
    (tmp_path / 'authorization-preservation.json').write_text(json.dumps({
        'original_tables': len(before), 'original_records': sum(map(len, before.values())),
        'original_sequences': len(counters), 'all_preserved': True,
        'school_revocation_preserved': True, 'guardian_withdrawal_preserved': True,
        'new_assignment_tables_empty': True,
    }, indent=2) + '\n')


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_all_classroom_gates_off_block_saved_work_without_writes(choice_data, student_home, monkeypatch):
    refs = choice_data
    issue(refs); start(refs); saved(refs)
    adult = director(refs)
    assert not school.LEGALLY_APPROVED_AGREEMENTS and not chart_consent.LEGALLY_APPROVED_POLICIES
    for name in tuple(os.environ):
        if name.startswith('CLASSROOM_'):
            monkeypatch.delenv(name)
    before = all_rows(refs.factory.engine)
    for client in (refs.child, adult):
        for url in ('/classroom', PATH, PATH + f'/1/classes/{refs.class_id}',
                    PATH + f'/student/1/classes/{refs.class_id}'):
            response = client.get(url)
            assert response.status_code == 404 and CUSTOM['title'] not in response.text
        assert client.post(PATH, data={}).status_code == 404
        assert client.post(PATH + f'/1/classes/{refs.class_id}', data={}).status_code == 404
    assert not choice.enabled() and all_rows(refs.factory.engine) == before
