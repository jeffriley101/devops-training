"""Independent-review regression: overlapping Programs serialize exact chart subjects."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, local
from queue import Queue
import time

import pytest
from sqlalchemy import select, text

from app import classroom as s1, classroom_s2 as s2, classroom_chart_consent as consent
from app import classroom_chart_sharing as sharing
from app.age_models import AccountPrivacy
from app.models import PracticeChart, WoodchuckProfile
from test_classroom_guardian_authorization import guardian_data, migrated_db, captured, adult_pin_hash, actor
from test_classroom_school_chart_privacy import proposed_chart_data, teacher
from test_classroom_s2 import institutional, create, code


@pytest.mark.parametrize("migrated_db", ["postgresql"], indirect=True)
def test_postgres_cross_program_chart_reader_subject_lock_order(proposed_chart_data, monkeypatch):
    refs = proposed_chart_data
    def proof(session, label):
        return s2.authenticate_student(session, woodchuck_id="WC-AGE-"+label, pin="2468")
    with refs.factory() as session:
        b=session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.woodchuck_id=="WC-AGE-B"))
        session.add(AccountPrivacy(profile_id=b.id, age_band="13to17", declared_at=refs.clock[0], public_from=refs.clock[0]))
        session.commit()
        s2.join_class(session, student=proof(session,"B"), program_id=1, code="BAND2026"); session.commit()
        institutional(session, program_id=2, starts_at=refs.clock[0], ends_at=refs.clock[0]+timedelta(days=365)); session.commit()
        room=create(session, program_id=2, name="Other exact Program Class"); session.commit()
        code(session, room.id, program_id=2); session.commit()
        assignment=s2.assign_teacher(session, actor=actor(session), program_id=2, class_id=room.id, verifier_id=refs.private_director_id); session.commit()
        for label in ("A","B"):
            s2.join_class(session, student=proof(session,label), program_id=2, code="BAND2026"); session.commit()
        for label,program_id,target in [("A",1,refs.private_assignment_id),("B",1,refs.private_assignment_id),("B",2,assignment.id),("A",2,assignment.id)]:
            profile=session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.woodchuck_id=="WC-AGE-"+label))
            chart=PracticeChart(profile_id=profile.id,practice_date=refs.clock[0].date(),created_at=refs.clock[0],minutes=5,instrument="Flute")
            session.add(chart); session.commit()
            sharing.set_sharing(session,student=proof(session,label),chart_id=chart.id,assignment_ids=[target]); session.commit()
        class2=room.id
    ready, release = Event(), Event()
    pids=Queue()
    state=local()
    original=s2._eligible_profile
    def gated(session,profile_id,**kwargs):
        result=original(session,profile_id,**kwargs)
        if getattr(state,"reader",None)=="first" and not getattr(state,"first_locked",False):
            state.first_locked=True
            ready.set()
            assert release.wait(10), "observer did not release first account lock"
        return result
    monkeypatch.setattr(s2,"_eligible_profile",gated)
    def observe(label,program_id,class_id):
        state.reader=label
        with refs.factory() as session:
            pids.put((label,session.scalar(text("SELECT pg_backend_pid()"))))
            session.execute(text("SET LOCAL statement_timeout='15s'"))
            try:
                result=sharing.shared_charts(session,actor=teacher(session),program_id=program_id,class_id=class_id)
                session.commit()
                return "ok",len(result)
            except Exception as error:
                session.rollback()
                return type(error).__name__,str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(observe,"first",1,refs.class_id)
        assert ready.wait(10), "first reader did not reach real account lock"
        second=pool.submit(observe,"second",2,class2)
        observed=dict(pids.get(timeout=10) for _ in range(2))
        try:
            with refs.factory.control_engine.connect() as watcher:
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    blockers=watcher.scalar(text("SELECT pg_blocking_pids(:pid)"),{"pid":observed["second"]})
                    if observed["first"] in blockers: break
                    time.sleep(0.01)
                else: pytest.fail("second reader did not encounter the actual account lock")
        finally:
            release.set()
        results=(first.result(timeout=30),second.result(timeout=30))
    assert results == (("ok",2),("ok",2)),results


@pytest.mark.parametrize("guardian_data", [{"under13":True},{"under13":False}], indirect=True)
def test_environment_alone_cannot_approve_protected_chart_scope(guardian_data,monkeypatch):
    refs=guardian_data
    monkeypatch.setenv("CLASSROOM_GUARDIAN_PCHART_SHARING_ENABLED","1")
    assert consent.LEGALLY_APPROVED_POLICIES == frozenset()
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied):
            consent.account_rule(session,refs.profile_id,lock=True)
