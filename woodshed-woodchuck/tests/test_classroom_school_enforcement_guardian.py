"""Retain actual C35/KWS guardian negatives behind a valid synthetic school.

The reused test functions keep their original assertions. These fixtures add
the new prerequisite, never replace the guardian/account decision.
"""
import pytest

from school_approval_fixture import approve_programs
from test_classroom_guardian_authorization import (
    guardian_data, migrated_db, captured, adult_pin_hash,
    test_native_evidence_is_distinct_from_each_class_permission as native_permissions,
    test_parent_reporting_withdrawal_preserves_membership_participation_and_private_director as report_withdrawal,
    test_exact_class_and_program_consents_are_not_substitutable as exact_scope,
    test_only_post_guardian_and_student_occurrence_and_submission_history_is_visible as history_bounds,
    test_regrant_requires_fresh_child_choice_and_never_backfills_history as regrant_bounds,
    test_pg_parent_withdrawal_and_actual_report_reader_observe_lock_wait as withdrawal_wait,
)
from test_classroom_school_chart_privacy import (
    proposed_chart_data,
    test_individual_chart_selection_and_exact_parent_permission_work_the_same_across_ages as exact_chart,
    test_under13_comprehensive_reporting_remains_denied_with_native_parent_evidence as under13_denial,
    test_chart_readers_fail_closed_when_any_exact_source_is_lost as chart_loss,
)


@pytest.fixture
def approved_guardian(guardian_data, monkeypatch):
    approve_programs(guardian_data.factory, monkeypatch, reviewer_id=1, approver_id=2)
    return guardian_data


@pytest.mark.parametrize('check', [native_permissions, report_withdrawal, exact_scope, history_bounds, regrant_bounds],
    ids=lambda f: f.__name__)
def test_existing_guardian_negatives_with_school_prerequisite(approved_guardian, check):
    check(approved_guardian)


@pytest.fixture
def approved_chart(proposed_chart_data, monkeypatch):
    approve_programs(proposed_chart_data.factory, monkeypatch, reviewer_id=1, approver_id=2)
    return proposed_chart_data


@pytest.mark.parametrize('guardian_data', [{'under13': True}, {'under13': False}], indirect=True)
def test_exact_guardian_chart_permission_with_approved_school(approved_chart):
    exact_chart(approved_chart)


@pytest.mark.parametrize('guardian_data', [{'under13': True}], indirect=True)
def test_under13_reporting_never_opened_by_approved_school(approved_chart):
    under13_denial(approved_chart)


@pytest.mark.parametrize('loss', ['legal', 'gate', 'parent_permission', 'reference', 'teacher', 'account'])
def test_existing_chart_permission_negatives_with_school_prerequisite(approved_chart, monkeypatch, loss):
    chart_loss(approved_chart, monkeypatch, loss)


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('rollback', [False, True])
def test_invitation_list_native_account_withdrawal_wait(approved_guardian, rollback):
    from app import classroom_enrollment as enrollment, classroom_enrollment_views as offers
    from app import parent_access, child_authorization
    from test_classroom_guardian_authorization import actor, parent_request
    from test_classroom_s1_postgres import ordered_wait
    refs = approved_guardian
    with refs.factory() as session:
        enrollment.issue_invitation(session, actor=actor(session, 2), program_id=1,
            class_id=refs.class_id, woodchuck_id=refs.woodchuck_id)
        session.commit()
    def read(session):
        result = offers.director_class(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id)
        return [row['woodchuck_id'] for row in result['offers']]
    with refs.factory() as session:
        assert read(session) == [refs.woodchuck_id]
    def withdraw(session):
        evidence, _, _ = parent_access.authenticated(session, parent_request(refs), lock=True)
        child_authorization.withdraw_evidence(session, evidence)
    result = ordered_wait(refs.factory, withdraw, read, rollback_first=rollback)
    assert [row[0] for row in result] == ['ok', 'ok']
    assert result[1][1] == ([refs.woodchuck_id] if rollback else [None])
