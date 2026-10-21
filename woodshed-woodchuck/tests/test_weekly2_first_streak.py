"""WL2-15b reuses final WL-07: timely first-streak credit, durable settlement and legacy ownership."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import sys
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from alembic import command
from tests.test_weekly2_foundation_migration import db, config
from app.security import hash_pin

from app import models, practice_charts, weekly
from app.age_privacy import declare_age
from app.db import Base
from app.feature_access import FEATURES, Feature
from app.login_streaks import apply_daily_login
from app.models import PracticeChart, RewardGrant, WoodchuckProfile, WoodchuckState
from app.student_practice_metrics import practice_totals
from app.weekly import CENTRAL, first_seven_date_milestone, weekly_credit
from app.weekly_rewards import award_first_practice_streak


SOURCE = "first-seven-practice-dates"
START = date(2026, 10, 5)
END = START + timedelta(days=6)


def instant(day, hour=12, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=CENTRAL).astimezone(timezone.utc)


NOW = instant(END, 18)


def chart(day, seconds=300, *, submitted=None, source="p-book", pid=1, instrument="Flute"):
    return PracticeChart(profile_id=pid, practice_date=day, minutes=seconds // 60,
        source=source, detected_playing_seconds=seconds if source == "pristine" else None,
        instrument=instrument, created_at=submitted or instant(day),
        include_contests=False, include_team_contests=False)


def seven(*, pid=1):
    return [chart(START + timedelta(days=offset), pid=pid) for offset in range(7)]


@pytest.fixture
def streak_db(db, monkeypatch):
    from app.main import app
    engine = db
    command.upgrade(config(), 'w25weekly004')
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    for gate in tuple(FEATURES):
        if gate.startswith("weekly_"):
            monkeypatch.setitem(FEATURES, gate, Feature(gate == "weekly_pants", "open"))
    factory.clock = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = factory.clock[0]
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    # Control server timestamps, including the actual chart creation default.
    from app import login_streaks, practice_chart_routes
    for module in (models, practice_charts, weekly, login_streaks, practice_chart_routes):
        monkeypatch.setattr(module, "datetime", Clock)
    with factory() as session:
        for pid in (1, 2):
            session.add(WoodchuckProfile(id=pid, woodchuck_id=f"WC-STREAK-{pid}",
                display_name=f"Synthetic {pid}", pin_hash=hash_pin("2468"),
                instrument="Flute", game_mode="Challenge", level="Advanced", goal="Keep goal"))
        session.flush()
        for pid in (1, 2):
            declare_age(session, pid, "13to17", at=instant(date(2026, 1, 1)))
            session.add(WoodchuckState(profile_id=pid, revision=8, state_json={
                "progress": {"credits": 40, "level": 12, "xp": 321, "streak": 3},
                "inventory": {"ownedItems": ["existing-pants"]}}))
        session.commit()
    yield factory
    engine.dispose()


def rows(session):
    """Whole-schema row values catch unrelated XP/Activity/currency changes."""
    return {name: [dict(row) for row in session.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
            for name, table in Base.metadata.tables.items()}


def seed(factory, charts):
    with factory.begin() as session:
        session.add_all(charts)


def grants(session, pid=1):
    return session.scalars(select(RewardGrant).where(
        RewardGrant.profile_id == pid, RewardGrant.source_key == SOURCE)).all()


def test_seven_timely_dates_grant_exactly_300_and_nothing_else(streak_db):
    seed(streak_db, seven())
    with streak_db() as session:
        before = rows(session)
        state_before = deepcopy(session.get(WoodchuckState, 1).state_json)
        assert award_first_practice_streak(session, 1, now=NOW) is True
        assert award_first_practice_streak(session, 1, now=NOW) is False
        session.commit()
        after = rows(session)
        assert {name for name in before if before[name] != after[name]} == {"reward_grants", "woodchuck_states"}
        state_before["progress"]["credits"] += 300
        assert session.get(WoodchuckState, 1).state_json == state_before
        assert session.get(WoodchuckState, 1).revision == 9
        assert [(g.reward_type, g.amount) for g in grants(session)] == [("dandelion", 300)]
    with streak_db() as session:
        before = rows(session)
        assert not award_first_practice_streak(session, 1, now=NOW + timedelta(days=8))
        session.commit()
        assert rows(session) == before


@pytest.mark.parametrize("case", ["six-dates", "under-five-minutes", "missing-date", "late-date"])
def test_incomplete_streak_does_not_grant(streak_db, case):
    charts = seven()
    if case == "six-dates":
        charts.pop()
    elif case == "under-five-minutes":
        charts[3].minutes = 4
    elif case == "missing-date":
        charts[3].practice_date -= timedelta(days=1)
        charts[3].created_at = instant(charts[3].practice_date)
    else:
        charts[3].created_at += timedelta(days=1)
    seed(streak_db, charts)
    with streak_db() as session:
        before = rows(session)
        assert not award_first_practice_streak(session, 1, now=NOW)
        session.commit()
        assert rows(session) == before


def test_late_topup_preserves_history_without_repairing_streak(streak_db):
    charts = seven()
    charts[3].minutes = 4
    seed(streak_db, charts)
    with streak_db() as session:
        assert not award_first_practice_streak(session, 1, now=NOW)
        session.rollback()
    seed(streak_db, [chart(START + timedelta(days=3), 60, submitted=NOW, source="pristine")])
    with streak_db() as session:
        saved = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1)).all()
        assert practice_totals(saved, set())["total_seconds"] == 2100
        assert weekly_credit(saved, week_start=START, now=NOW) == {
            "practice_seconds": 2100, "practice_days": 7, "logging_days": 7}
        assert first_seven_date_milestone(saved, now=NOW) is None
        assert not award_first_practice_streak(session, 1, now=NOW)
        session.commit()
        assert len(saved) == 8 and not grants(session)
        assert session.get(WoodchuckState, 1).state_json["progress"]["credits"] == 40


def test_book_and_pristine_add_exact_seconds_across_instruments():
    charts = [item for offset in range(7) for item in (
        chart(START + timedelta(days=offset), 240),
        chart(START + timedelta(days=offset), 59, source="pristine", instrument="Tuba"))]
    assert first_seven_date_milestone(charts, now=NOW) is None  # 299, never rounded up.
    charts += [chart(START + timedelta(days=offset), 1, source="pristine") for offset in range(7)]
    assert first_seven_date_milestone(charts, now=NOW) == END
    assert weekly_credit(charts, week_start=START, now=NOW)["practice_seconds"] == 2100
    charts[-1].created_at += timedelta(days=1)
    assert first_seven_date_milestone(charts, now=NOW + timedelta(days=1)) is None


@pytest.mark.parametrize("end", [date(2026, 3, 8), date(2026, 11, 1)], ids=["spring-dst", "fall-dst"])
@pytest.mark.parametrize("naive", [False, True], ids=["aware", "sqlite-naive-utc"])
def test_chicago_same_date_midnight_and_dst(end, naive):
    charts = [chart(end - timedelta(days=offset), submitted=instant(end - timedelta(days=offset), 23, 59))
              for offset in range(7)]
    if naive:
        for item in charts:
            item.created_at = item.created_at.replace(tzinfo=None)
    now = instant(end + timedelta(days=1), 0, 1)
    assert first_seven_date_milestone(charts, now=now) == end
    late = instant(end + timedelta(days=1), 0)
    charts[0].created_at = late.replace(tzinfo=None) if naive else late
    assert first_seven_date_milestone(charts, now=now) is None  # Sunday -> Monday.


def test_future_submission_future_practice_and_ineligible_source_do_not_fill_date():
    charts = seven()
    charts[-1].created_at = NOW + timedelta(seconds=1)
    assert first_seven_date_milestone(charts, now=NOW) is None
    charts[-1] = chart(END + timedelta(days=1))
    assert first_seven_date_milestone(charts, now=NOW) is None
    charts[-1] = chart(END, source="legacy")
    assert first_seven_date_milestone(charts, now=NOW) is None


@pytest.mark.parametrize("reward_type,amount", [("pants_choice", 1), ("dandelion", 300)])
@pytest.mark.parametrize("pending", [False, True], ids=["committed", "same-transaction"])
def test_legacy_or_new_receipt_settles_semantic_milestone(streak_db, reward_type, amount, pending):
    # The legacy accomplishment stays settled even when its charts were late.
    charts = seven()
    for item in charts:
        item.created_at = NOW
    seed(streak_db, charts)
    with streak_db() as session:
        receipt = RewardGrant(profile_id=1, source_key=SOURCE, reward_type=reward_type, amount=amount)
        session.add(receipt)
        if not pending:
            session.commit()
        wallet = deepcopy(session.get(WoodchuckState, 1).state_json)
        assert not award_first_practice_streak(session, 1, now=NOW)
        session.commit()
        assert grants(session) == [receipt]
        assert (receipt.source_key, receipt.reward_type, receipt.amount) == (SOURCE, reward_type, amount)
        assert session.get(WoodchuckState, 1).state_json == wallet
        assert session.get(WoodchuckState, 1).revision == 8
    # Even a later entirely timely streak must not pay this profile again.
    seed(streak_db, seven())
    with streak_db.begin() as session:
        assert not award_first_practice_streak(session, 1, now=NOW)
        assert len(grants(session)) == 1


def test_foreign_milestone_does_not_settle_current_student(streak_db):
    seed(streak_db, seven())
    with streak_db.begin() as session:
        session.add(RewardGrant(profile_id=2, source_key=SOURCE, reward_type="pants_choice", amount=1))
        assert award_first_practice_streak(session, 1, now=NOW)
    with streak_db() as session:
        assert [(g.reward_type, g.amount) for g in grants(session)] == [("dandelion", 300)]
        assert [(g.reward_type, g.amount) for g in grants(session, 2)] == [("pants_choice", 1)]
        assert session.get(WoodchuckState, 2).state_json["progress"]["credits"] == 40


@pytest.mark.parametrize("source", ["p-book", "pristine"])
def test_chart_creation_and_retry_settle_once_with_server_timestamp(streak_db, source):
    seed(streak_db, seven()[:-1])
    with streak_db() as session:
        profile = session.get(WoodchuckProfile, 1)
        arguments = dict(profile=profile, verifier_id=None, practice_date=END,
            minutes=5, source=source, submission_key="seventh-timely-date")
        if source == "pristine":
            arguments["detected_playing_seconds"] = 300
        first = practice_charts.create_practice_chart_verification_request(session, **arguments)
        repeated = practice_charts.create_practice_chart_verification_request(session, **arguments)
        assert first.created is True and repeated.created is False
        assert first.chart.id == repeated.chart.id
        assert (first.chart.created_at.replace(tzinfo=timezone.utc) if first.chart.created_at.tzinfo is None
                else first.chart.created_at.astimezone(timezone.utc)) == NOW
        assert first.chart.credits_awarded == 0  # The milestone is its own durable grant.
        assert [(g.reward_type, g.amount) for g in grants(session)] == [("dandelion", 300)]
        assert session.get(WoodchuckState, 1).state_json["progress"]["credits"] == 340
        # A subsequent login evaluation cannot award this milestone again.
        result = apply_daily_login(session, profile_id=1, now=NOW)
        session.commit()
        assert result["dandelion_balance"] == 341  # Existing day-one login +1.
        assert len(grants(session)) == 1


def test_late_chart_creation_stays_recorded_but_login_cannot_repair(streak_db):
    seed(streak_db, seven()[:-1])
    streak_db.clock[0] = NOW + timedelta(days=1)
    with streak_db() as session:
        result = practice_charts.create_practice_chart_verification_request(session,
            profile=session.get(WoodchuckProfile, 1), verifier_id=None,
            practice_date=END, minutes=5, submission_key="late-sunday")
        assert result.created and result.chart.practice_date == END
        assert not grants(session)
        saved = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1)).all()
        assert practice_totals(saved, set())["total_seconds"] == 2100
        assert weekly_credit(saved, week_start=START, now=streak_db.clock[0])["practice_seconds"] == 2100
        result = apply_daily_login(session, profile_id=1, now=streak_db.clock[0])
        session.commit()
        assert result["dandelion_balance"] == 41 and not grants(session)


def test_daily_login_awards_streak_once_and_same_day_retry_is_read_only(streak_db):
    seed(streak_db, seven())
    with streak_db() as session:
        result = apply_daily_login(session, profile_id=1, now=NOW)
        session.commit()
        assert result["dandelion_balance"] == 341
        assert [(g.reward_type, g.amount) for g in grants(session)] == [("dandelion", 300)]
        before = rows(session)
        assert apply_daily_login(session, profile_id=1, now=NOW)["dandelion_balance"] == 341
        session.commit()
        assert rows(session) == before


@pytest.mark.parametrize("failure", ["grant-insert", "balance-update", "before-commit"])
@pytest.mark.parametrize("caller", ["milestone", "daily-login"])
def test_grant_and_protected_wallet_roll_back_together(streak_db, failure, caller):
    seed(streak_db, seven())
    engine = streak_db.kw["bind"]
    with streak_db() as session:
        before = rows(session)
    def fail_sql(connection, cursor, statement, parameters, context, many):
        sql = statement.lower()
        if (failure == "grant-insert" and sql.startswith("insert into reward_grants")) or (
                failure == "balance-update" and sql.startswith("update woodchuck_states")):
            raise RuntimeError("synthetic failure after SQL")
    event.listen(engine, "after_cursor_execute", fail_sql)
    try:
        with pytest.raises(RuntimeError, match="synthetic failure"), streak_db.begin() as session:
            if caller == "milestone":
                assert award_first_practice_streak(session, 1, now=NOW)
            else:
                assert apply_daily_login(session, profile_id=1, now=NOW)["dandelion_balance"] == 341
            session.flush()
            if failure == "before-commit":
                def fail_commit(_):
                    raise RuntimeError("synthetic failure before commit")
                event.listen(session, "before_commit", fail_commit)
    finally:
        event.remove(engine, "after_cursor_execute", fail_sql)
    with streak_db() as session:
        assert rows(session) == before


def test_chart_creation_commit_failure_rolls_back_chart_grant_and_wallet(streak_db):
    seed(streak_db, seven()[:-1])
    with streak_db() as session:
        before = rows(session)
        def fail_commit(_):
            session.flush()  # Both writes reached SQL before the failed commit.
            raise RuntimeError("synthetic chart commit failure")
        event.listen(session, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="synthetic chart commit failure"):
            practice_charts.create_practice_chart_verification_request(session,
                profile=session.get(WoodchuckProfile, 1), verifier_id=None,
                practice_date=END, minutes=5, submission_key="rollback-seventh")
        session.rollback()
    with streak_db() as session:
        assert rows(session) == before


@pytest.mark.parametrize("db", ["sqlite"], indirect=True)
def test_two_sqlite_evaluations_refresh_cached_wallet_and_settle_once(streak_db):
    seed(streak_db, seven())
    barrier = Barrier(2)
    def evaluate(_):
        with streak_db() as session:
            cached = session.get(WoodchuckState, 1)
            assert cached.state_json["progress"]["credits"] == 40
            barrier.wait(timeout=5)
            awarded = award_first_practice_streak(session, 1, now=NOW)
            session.commit()
            return awarded, cached.state_json["progress"]["credits"]
    # Evidence for this disposable SQLite writer fence, not PostgreSQL proof.
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(evaluate, (1, 2)))
    assert sorted(result) == [(False, 340), (True, 340)]
    with streak_db() as session:
        assert len(grants(session)) == 1
        assert session.get(WoodchuckState, 1).revision == 9


def test_disabled_historical_gate_preserves_default_no_award(streak_db, monkeypatch):
    seed(streak_db, seven())
    monkeypatch.setitem(FEATURES, "weekly_pants", Feature(False, "open"))
    with streak_db() as session:
        before = rows(session)
        assert not award_first_practice_streak(session, 1, now=NOW)
        session.commit()
        assert rows(session) == before
