"""Private records and shared scoring evidence on the migrated disposable database."""
from datetime import timedelta
from threading import Event, Thread
from time import monotonic, sleep

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from app.deletion_private_activity import (
    clear_private_activity, clear_private_school_activity, private_activity_complete, private_school_activity_complete,
)
from app.age_privacy import declare_age
from app.economy import qualified_camp_point_clause
from app.login_streaks import apply_daily_login
from app.main import app
from app.models import (
    ArcadeAttemptPack, ArcadeHighScore, ArcadePlaySession, ArcadeStartRequest, CampPointAward,
    CrownAward, DailyTriviaAttempt, LoginStreak, OwnedItemCopy, PlungePointAward, PracticeChart, PracticeChartVerification,
    QuestCompletion, RewardGrant, RewardInventoryPlacement, TravelingCupPlacement, WoodchuckProfile,
)
from app.store_catalog import MUM_SNACK_ITEM_KEYS
from app.store_inventory import OwnedItemAccessError, grant_owned_item
from app.weekly_models import WeeklyInstrumentPlan, WeeklyNewsletterPresentation, WeeklyPlan, WeeklyTaskClaim, WeeklyWheelPlay
from tests.test_account_deletion import NOW, profile, seed_history
from tests.test_weekly2_plans import plan_values, slot_values


def test_active_student_can_buy_and_place_before_deletion(play_pg, monkeypatch):
    from app import store_routes

    monkeypatch.setattr(store_routes, "SessionLocal", play_pg)
    with play_pg.begin() as session:
        student = profile(session, 1)
        declare_age(session, student.id, "13to17", at=NOW)
    credentials = {"woodchuck_id": student.woodchuck_id, "pin": "1234"}
    with TestClient(app) as client:
        assert client.post("/account/login", data=credentials).status_code == 200
        purchase = client.post("/store/purchases", json={"item_key": "candle"})
        assert purchase.status_code == 201
        copy_id = purchase.json()["item"]["id"]
        placed = client.put(f"/store/inventory/{copy_id}/placement", json={"x": 0.25, "y": 0.35})
        assert placed.status_code == 200 and placed.json()["item"]["placement_x"] == 0.25
        assert client.post("/account/delete", data={**credentials, "confirmation": "DELETE"},
                           follow_redirects=False).status_code == 303
        assert client.post("/store/purchases", json={"item_key": "candle"}).status_code == 401
    with play_pg() as session:
        assert session.get(OwnedItemCopy, copy_id) is None


def private_records(session, sid, label):
    pack = ArcadeAttemptPack(profile_id=sid, game_key="blue", cost=100, attempts_used=1)
    grant = RewardGrant(profile_id=sid, source_key="synthetic-trophy", reward_type="trophy", amount=1)
    session.add_all([pack, grant])
    session.flush()
    play = ArcadePlaySession(profile_id=sid, game_key="blue", play_token=label, started_at=NOW, pack_id=pack.id,
                             attempt_number=1, authoritative_score=5, completed_at=NOW)
    session.add(play)
    session.flush()
    rows = [
        pack, play, ArcadeStartRequest(profile_id=sid, play_id=play.id, request_id="one"),
        ArcadeHighScore(profile_id=sid, game_key="blue", best_score=5),
        PlungePointAward(profile_id=sid, event_key=label, event_type="carrot", points_scored=1, occurred_at=NOW),
        DailyTriviaAttempt(profile_id=sid, activity_date=NOW.date(), selected_answer="private", correct=False),
        QuestCompletion(profile_id=sid, activity_date=NOW.date(), quest_id="quest", logged_minutes=4,
                        reward_amount=5, completed_at=NOW),
        OwnedItemCopy(profile_id=sid, item_key="synthetic", acquisition_source="store", purchase_price=10),
        RewardInventoryPlacement(profile_id=sid, reward_grant_id=grant.id, reward_ordinal=1),
        TravelingCupPlacement(profile_id=sid, entitlement_key="coterie-cup"),
        WeeklyWheelPlay(profile_id=sid, id=label, request_key="one", funded_day=NOW.date(), funded_at=NOW,
                        daily_ordinal=1, outcomes=["xp"], completed_at=NOW),
        WeeklyNewsletterPresentation(profile_id=sid, week_start=NOW.date(), reserved_at=NOW),
    ]
    session.add_all(rows)
    session.flush()
    return rows


def test_private_records_removed_without_changing_qualified_points_or_another_student(play_pg):
    with play_pg() as session:
        target, _, other, team, *_ = seed_history(session)
    with play_pg.begin() as session:
        target, other = session.get(WoodchuckProfile, target.id), session.get(WoodchuckProfile, other.id)
        private = private_records(session, target.id, "first")
        retained = private_records(session, other.id, "second")
        for owner in (target, other):
            declare_age(session, owner.id, "13to17", at=NOW - timedelta(days=7))
            for age_in_days in range(6, -1, -1):
                login = apply_daily_login(session, profile_id=owner.id, now=NOW - timedelta(days=age_in_days))
                session.flush()
            assert login["crown_awarded"]
        target.plunge_best_score = other.plunge_best_score = 37
        source = "weekly-wheel:shared:1"
        wheel = WeeklyWheelPlay(profile_id=target.id, id="shared", request_key="two", funded_day=NOW.date(),
            funded_at=NOW, daily_ordinal=2, outcomes=["activity"], completed_at=NOW)
        award = CampPointAward(profile_id=target.id, activity_type="wheel-prize", points_awarded=14,
            team_id=team.id, occurred_at=NOW, created_at=NOW, duplicate_key=source)
        receipt = RewardGrant(profile_id=target.id, source_key=source, reward_type="activity", amount=14, created_at=NOW)
        session.add_all([wheel, award, receipt])
        session.flush()
        earned = select(CampPointAward.points_awarded).where(CampPointAward.id == award.id, qualified_camp_point_clause())
        assert session.scalar(earned) == 14
        assert not private_activity_complete(session, target.id)
        previous = target.updated_at
    with play_pg.begin() as session:
        clear_private_activity(session, target.id)
        assert private_activity_complete(session, target.id)
        clear_private_activity(session, target.id)
    with play_pg() as session:
        assert all(session.get(type(row), inspect(row).identity) is None for row in private)
        assert all(session.get(type(row), inspect(row).identity) is not None for row in retained)
        assert session.scalar(earned) == 14
        assert session.get(LoginStreak, target.id) is None
        assert session.get(LoginStreak, other.id).current_days == 7
        for owner in (target, other):
            crown = session.scalar(select(CrownAward).where(CrownAward.profile_id == owner.id))
            assert crown.category_key == "weekly-login-streak"
            assert session.scalar(select(RewardGrant.amount).where(
                RewardGrant.profile_id == owner.id, RewardGrant.source_key == crown.source_key)) == 1
        assert session.get(RewardGrant, receipt.id).source_key == source
        assert session.get(WeeklyWheelPlay, wheel.id).outcomes == ["activity"]
        assert session.get(WoodchuckProfile, other.id).plunge_best_score == 37
        subject = session.get(WoodchuckProfile, target.id)
        assert (subject.plunge_best_score, subject.updated_at) == (0, previous)


def test_mismatched_arcade_and_reward_owners_preserved(play_pg):
    with play_pg.begin() as session:
        target, other = profile(session, 1), profile(session, 2)
        pack = ArcadeAttemptPack(profile_id=target.id, game_key="blue", cost=100, attempts_used=1)
        receipt = RewardGrant(profile_id=other.id, source_key="other-award", reward_type="trophy", amount=1)
        session.add_all([pack, receipt])
        session.flush()
        linked = ArcadePlaySession(profile_id=other.id, play_token="other", game_key="blue",
                                   pack_id=pack.id, attempt_number=1)
        blocked = ArcadePlaySession(profile_id=target.id, play_token="target", game_key="thirds")
        session.add_all([linked, blocked])
        session.flush()
        request = ArcadeStartRequest(profile_id=other.id, request_id="other-slot", play_id=blocked.id)
        placement = RewardInventoryPlacement(profile_id=target.id, reward_grant_id=receipt.id, reward_ordinal=1)
        session.add_all([request, placement])
        session.flush()
        clear_private_activity(session, target.id)
        assert private_activity_complete(session, target.id)
    with play_pg() as session:
        for row in [pack, linked, blocked, request, placement, receipt]:
            assert session.get(type(row), inspect(row).identity) is not None


def test_only_unscored_unshared_private_charts_and_plans_removed(play_pg):
    with play_pg.begin() as session:
        sid = profile(session, 1).id
        other = profile(session, 2).id
        def chart(owner, **overrides):
            return PracticeChart(**(dict(profile_id=owner, practice_date=NOW.date(), instrument="Flute", minutes=8,
                                    include_contests=False, include_team_contests=False) | overrides))
        private = chart(sid)
        shared, team, reviewed, grant_source, sibling = (chart(sid, include_contests=True),
            chart(sid, include_team_contests=True), chart(sid), chart(sid), chart(other))
        plan = WeeklyPlan(**plan_values(id=None, profile_id=sid))
        sibling_plan = WeeklyPlan(**plan_values(id=None, profile_id=other))
        session.add_all([private, shared, team, reviewed, grant_source, sibling, plan, sibling_plan])
        session.flush()
        session.add_all([WeeklyInstrumentPlan(**slot_values(plan_id=plan.id)), WeeklyTaskClaim(plan_id=plan.id, lane="board", claimed_at=NOW),
                        PracticeChartVerification(practice_chart_id=reviewed.id, status="approved", responded_at=NOW),
                        RewardGrant(profile_id=other, source_key=f"practice-chart:{grant_source.id}", reward_type="dandelion", amount=1)])
        session.flush()
        assert not private_school_activity_complete(session, sid)
        clear_private_school_activity(session, sid)
        assert private_school_activity_complete(session, sid)
    with play_pg() as session:
        for row in [shared, team, reviewed, grant_source, sibling, sibling_plan]:
            assert session.get(type(row), inspect(row).identity) is not None
        assert session.get(PracticeChart, private.id) is None
        assert session.get(WeeklyPlan, plan.id) is None
        assert session.get(WeeklyTaskClaim, (plan.id, "board")) is None
        assert session.get(WeeklyInstrumentPlan, (plan.id, 1)) is None


def test_store_writer_waits_for_deletion_and_cannot_recreate_private_inventory(play_pg):
    with play_pg.begin() as session:
        sid = profile(session, 1).id
    started = Event()
    result = []
    def waiting_writer():
        with play_pg() as writer:
            result.append(writer.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                grant_owned_item(writer, profile_id=sid, item_key=next(iter(MUM_SNACK_ITEM_KEYS)), grant_key="old")
                writer.commit()
            except OwnedItemAccessError:
                result.append("denied")
    with play_pg.begin() as session:
        backend = session.scalar(text("SELECT pg_backend_pid()"))
        target = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == sid).with_for_update())
        worker = Thread(target=waiting_writer, daemon=True)
        worker.start()
        assert started.wait(5)
        cutoff = monotonic() + 5
        while not session.scalar(text("SELECT :holder = ANY(pg_blocking_pids(:writer))"),
                                 dict(holder=backend, writer=result[0])):
            assert monotonic() < cutoff, "writer did not wait on the account lock"
            sleep(0.02)
        target.status = "deleted"
    worker.join(5)
    assert not worker.is_alive() and result[1:] == ["denied"]
    with play_pg() as session:
        assert session.scalar(select(OwnedItemCopy.id).where(OwnedItemCopy.profile_id == sid)) is None


def test_recorded_school_authority_keeps_only_potentially_covered_charts_and_plans(play_pg, monkeypatch):
    from app import classroom, classroom_reporting, classroom_s2
    from app.classroom_invitation import create_program_invitation
    from app.age_models import AccountPrivacy
    from app.classroom_models import ClassroomReportingPeriod
    from app.models import Organization, TrustedVerifier
    from app.security import hash_pin
    from tests.school_approval_fixture import approve_programs
    from test_classroom_s2 import NOW as school_now
    from tests.test_classroom_s3_postgres import setup_reporting, withdraw

    for flag in ("CLASSROOM_S1_ENABLED", "CLASSROOM_S2_ENABLED", "CLASSROOM_S3_ENABLED"):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "synthetic-classroom-s2-admin")
    clock = [school_now]
    monkeypatch.setattr(classroom_s2, "_now", lambda: clock[0])
    monkeypatch.setattr(classroom_reporting, "_now", lambda: clock[0])
    with play_pg.begin() as session:
        pin = hash_pin("2468")
        session.add_all(TrustedVerifier(email=f"adult{n}@example.test", display_name=f"Adult {n}", pin_hash=pin)
                        for n in range(1, 5))
        session.add(Organization(id=1, name="Synthetic School", organization_type="music"))
        session.add(WoodchuckProfile(id=1, woodchuck_id="WC-SYNTHETIC", display_name="Student",
                                     pin_hash=pin, instrument="Flute", level="Beginner", goal="Practice"))
        session.flush()
        session.add(AccountPrivacy(profile_id=1, age_band="13to17", declared_at=school_now, public_from=school_now))
        token = create_program_invitation(1, "adult1@example.test")
        classroom.accept_program_invitation(session, token=token, pin="2468")
    approve_programs(play_pg, monkeypatch, (1,))
    refs = setup_reporting(play_pg)
    clock[0] += timedelta(days=7)
    with play_pg.begin() as session:
        withdraw(session, refs.class_id)
        period = session.get(ClassroomReportingPeriod, refs.period_id)
        assert period.starts_at == school_now and period.ended_at == clock[0]
        before, inside, after = school_now - timedelta(days=7), school_now + timedelta(days=1), clock[0] + timedelta(days=7)
        private, covered, subsequent = [
            PracticeChart(profile_id=1, practice_date=at.date(), minutes=15, instrument="Flute", created_at=at,
                          include_contests=False, include_team_contests=False) for at in (before, inside, after)]
        outside_plan, covered_plan = [
            WeeklyPlan(**plan_values(id=None, profile_id=1, issued_at=at, recent_results_cutoff=at,
                                     week_start=at.date() - timedelta(days=at.weekday()))) for at in (before, inside)]
        session.add_all([private, covered, subsequent, outside_plan, covered_plan])
        session.flush()
        clear_private_school_activity(session, 1)
    with play_pg() as session:
        assert session.get(ClassroomReportingPeriod, refs.period_id).ended_at == clock[0]
        assert session.get(PracticeChart, covered.id) is not None
        assert session.get(WeeklyPlan, covered_plan.id) is not None
        assert all(session.get(type(r), inspect(r).identity) is None for r in (private, subsequent, outside_plan))
