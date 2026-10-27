"""Synthetic tests for explicitly selected, individually scoped chart access."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import classroom, classroom_chart_sharing as sharing, classroom_s2 as s2
from app.classroom_models import ClassroomChartDirectorShare
from app.models import PracticeChart
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, student
from test_classroom_s3_reporting import report_db, setup, chart


@pytest.fixture
def configured(report_db, monkeypatch):
    monkeypatch.setenv("CLASSROOM_DIRECTOR_ENABLED", "1")
    return report_db


@pytest.mark.parametrize("replacement_verifier", [2, 3])
def test_grant_withdrawal_and_replacement_teacher(configured, replacement_verifier):
    factory, clock = configured
    with factory() as session:
        class_id, _, assignment_id = setup(session)
        chart_id = chart(session, clock[0].date(), clock[0])
        other_id = chart(session, clock[0].date(), clock[0])
        assert sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id) == []
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[assignment_id])
        rows = sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id)
        assert len(rows) == 1 and rows[0]["minutes"] == 17
        with pytest.raises(classroom.ClassroomDenied):
            sharing.shared_charts(session, actor=actor(session, 3), program_id=1, class_id=class_id)
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[])
        assert sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id) == []
        assert session.get(PracticeChart, chart_id).note == "PRIVATE NOTE MUST NOT BE READ"
        assert session.get(PracticeChart, other_id).minutes == 17
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[assignment_id])
        s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=assignment_id)
        replacement = s2.assign_teacher(session, actor=actor(session), program_id=1,
                                         class_id=class_id, verifier_id=replacement_verifier)
        assert replacement.id != assignment_id
        assert sharing.shared_charts(session, actor=actor(session, replacement_verifier), program_id=1, class_id=class_id) == []
        assert len(session.scalars(select(ClassroomChartDirectorShare)).all()) == 2


def test_forged_selection_and_expiry_are_denied(configured):
    factory, clock = configured
    with factory() as session:
        class_id, _, assignment_id = setup(session)
        chart_id = chart(session, clock[0].date(), clock[0])
        with pytest.raises(classroom.ClassroomDenied):
            sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[99999])
        with pytest.raises(classroom.ClassroomDenied):
            sharing.set_sharing(session, student=student(session), chart_id=99999, assignment_ids=[assignment_id])
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[assignment_id])
        clock[0] += timedelta(days=90)
        assert sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id) == []


def test_membership_interruption_does_not_restore_share(configured):
    factory, clock = configured
    with factory() as session:
        class_id, member_id, assignment_id = setup(session)
        chart_id = chart(session, clock[0].date(), clock[0])
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[assignment_id])
        share_id = session.scalar(select(ClassroomChartDirectorShare.id))
        clock[0] += timedelta(minutes=1)
        s2.suspend_member(session, actor=actor(session), program_id=1, class_id=class_id,
                          membership_id=member_id, reason="conduct")
        assert sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id) == []
        clock[0] += timedelta(minutes=1)
        s2.reinstate_member(session, actor=actor(session), program_id=1, class_id=class_id,
                            membership_id=member_id)
        assert sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id) == []
        assert not sharing.selection_current(session, student=student(session), share_id=share_id)
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[assignment_id])
        assert len(sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id)) == 1
