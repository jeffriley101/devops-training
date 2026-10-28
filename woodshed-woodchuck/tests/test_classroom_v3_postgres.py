"""V3 aggregates obey the same PostgreSQL withdrawal serialization as charts."""
import pytest

from app import classroom_dashboard, classroom_chart_sharing as sharing
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2 import actor, student
from test_classroom_s2_postgres import s2_pg
from test_classroom_s3_postgres import s3_pg, setup_reporting
from test_classroom_s3_reporting import chart


@pytest.mark.parametrize('withdraw_first', [False, True])
def test_v3_aggregate_read_and_withdraw_are_serialized(s3_pg, monkeypatch, withdraw_first):
    monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED', '1')
    refs = setup_reporting(s3_pg, granted=False)
    with s3_pg() as session:
        chart_id = chart(session, s3_pg.reporting_clock[0].date(), s3_pg.reporting_clock[0])
        chart(session, s3_pg.reporting_clock[0].date(), s3_pg.reporting_clock[0])  # private
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[refs.assignment_id])
        session.commit()

    def revoke(session):
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[])

    def read(session):
        result = classroom_dashboard.dashboard(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id)
        rows = result['students']
        if rows:
            assert len(rows) == 1 and rows[0]['lifetime']['charts'] == 1
            assert rows[0]['lifetime']['total_seconds'] == rows[0]['lifetime']['pristine_seconds'] == 1020
            assert rows[0]['lifetime']['verified_seconds'] == 0
            assert rows[0]['history'][-1]['cumulative_seconds'] == 1020
        return len(rows)

    first, second = (revoke, read) if withdraw_first else (read, revoke)
    results = ordered_wait(s3_pg, first, second)
    assert [result[0] for result in results] == ['ok', 'ok']
    assert (results[1][1] if withdraw_first else results[0][1]) == (0 if withdraw_first else 1)
    with s3_pg() as session:
        assert read(session) == 0
