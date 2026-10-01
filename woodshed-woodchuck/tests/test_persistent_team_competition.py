"""Versioned competition/identity consumers on disposable SQLite/PostgreSQL."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import contests, director_dashboard, season_team_activation
from app.band_director_context import current_student_team
from app.models import (Contest, ContestResult, ContestWeek, PracticeChart,
    PracticeChartVerification, ProfileCapability, Season, Team, TeamMembership,
    TeamWeekMembershipSnapshot)
from app.team_authority import LEGACY_RULES, PERSISTENT_RULES
from tests.test_persistent_team_http import http_db, NOW


def next_week(session):
    return contests.ensure_current_contest_data(
        session, now=datetime(2026, 10, 6, 18, tzinfo=timezone.utc))


def approved_chart(session, profile_id, team_id, practice_date, minutes=10):
    at = datetime.combine(practice_date, datetime.min.time(), timezone.utc) + timedelta(hours=18)
    chart = PracticeChart(profile_id=profile_id, team_id=team_id,
        practice_date=practice_date, minutes=minutes, source="p-book",
        instrument="Trumpet", include_contests=True, include_team_contests=True,
        created_at=at)
    session.add(chart)
    session.flush()
    session.add(PracticeChartVerification(practice_chart_id=chart.id,
        status="approved", responded_at=at + timedelta(minutes=1)))
    session.flush()
    return chart


def test_new_week_versioned_roster_keeps_current_week_legacy(http_db):
    factory, _, profile = http_db
    with factory() as session:
        _, _, current = contests.ensure_current_contest_data(session, now=NOW)
        assert current.team_membership_rules_version == LEGACY_RULES
        assert contests._eligible_weekly_team_rosters(session, current) == {}
        membership = session.scalar(select(TeamMembership).where(TeamMembership.is_persistent.is_(True)))
        original = (membership.id, membership.started_at, membership.team_id)
        _, _, future = next_week(session)
        assert future.team_membership_rules_version == PERSISTENT_RULES
        assert contests._eligible_weekly_team_rosters(session, future) == {10: {profile.id}}
        assert (membership.id, membership.started_at, membership.team_id) == original
        assert len(session.scalars(select(Team).where(Team.is_operating.is_(True))).all()) == 5
        assert current.team_membership_rules_version == LEGACY_RULES


def test_persistent_snapshot_excludes_exact_next_week_transition(http_db):
    factory, _, profile = http_db
    with factory() as session:
        _, _, week = next_week(session)
        end = datetime(2026, 10, 12, 5, tzinfo=timezone.utc)
        old = session.scalar(select(TeamMembership).where(TeamMembership.is_persistent.is_(True)))
        old.ended_at = end
        session.flush()
        session.add(TeamMembership(profile_id=profile.id, team_id=11,
            season_id=session.get(Team, 11).season_id, started_at=end,
            selected_week_start=date(2026, 10, 12), is_persistent=True))
        session.flush()
        assert contests._eligible_weekly_team_rosters(session, week) == {10: {profile.id}}
        rows = contests._snapshot_memberships(session, week)
        assert [(row.profile_id, row.team_id, row.membership_id) for row in rows] == [
            (profile.id, 10, old.id)]
        assert contests.aware_utc(rows[0].snapshot_at) == end - timedelta(microseconds=1)
        assert contests._student_emblem_keys_for_week(session,
            contest_week=week, profile_ids={profile.id}) == {profile.id: "letter:A"}
        session.commit()
        before = dict(session.execute(select(TeamWeekMembershipSnapshot.__table__)).mappings().one())
        assert contests._snapshot_memberships(session, week)[0].id == rows[0].id
        assert dict(session.execute(select(TeamWeekMembershipSnapshot.__table__)).mappings().one()) == before


@pytest.mark.parametrize("persistent", [False, True])
def test_finalizer_versions_are_explicit_and_retry_preserves_results(http_db, persistent):
    factory, _, profile = http_db
    with factory() as session:
        if persistent:
            _, _, week = next_week(session)
            practice_date, final_at = date(2026, 10, 6), datetime(2026, 10, 12, 18, tzinfo=timezone.utc)
        else:
            _, _, week = contests.ensure_current_contest_data(session, now=NOW)
            practice_date, final_at = date(2026, 10, 1), datetime(2026, 10, 5, 18, tzinfo=timezone.utc)
        approved_chart(session, profile.id, 10, practice_date)
        session.commit()
        contests.finalize_contest_week(session, week_start=week.week_start, now=final_at)
        session.commit()
        expected = (contests.PERSISTENT_FINALIZER_RULES_VERSION if persistent
                    else contests.FINALIZER_RULES_VERSION)
        assert week.finalizer_rules_version == expected
        assert contests.historical_rules_incompatibility(week) is None
        before_results = list(session.execute(select(ContestResult.__table__)).mappings())
        before_snapshots = list(session.execute(select(TeamWeekMembershipSnapshot.__table__)).mappings())
        contests.finalize_contest_week(session, week_start=week.week_start, now=final_at + timedelta(days=8))
        session.commit()
        assert list(session.execute(select(ContestResult.__table__)).mappings()) == before_results
        assert list(session.execute(select(TeamWeekMembershipSnapshot.__table__)).mappings()) == before_snapshots
        assert len(before_snapshots) == int(persistent)


def test_unknown_week_reader_fails_closed(http_db):
    factory, _, _ = http_db
    with factory() as session:
        _, _, week = next_week(session)
        # Unknown rules cannot silently fall back to season-scoped authority.
        week.team_membership_rules_version = "future_unknown"
        with session.no_autoflush, pytest.raises(ValueError):
            contests._membership_snapshot_at(week)
        with session.no_autoflush, pytest.raises(ValueError):
            contests._eligible_weekly_team_rosters(session, week)


def test_lifetime_family_totals_operating_display_and_moderation(http_db):
    factory, _, profile = http_db
    with factory() as session:
        season, _, week = next_week(session)
        operating = session.get(Team, 10)
        origin = session.scalar(select(Season).where(Season.key == "band-camp-2026"))
        historical = Team(family_id=operating.family_id, season_id=origin.id,
            display_name="Old Eureka", normalized_name="old eureka", emblem_key="emoji:dog")
        session.add(historical)
        session.flush()
        old_chart = approved_chart(session, profile.id, historical.id, date(2026, 8, 3), minutes=30)
        new_chart = approved_chart(session, profile.id, operating.id, date(2026, 10, 6), minutes=40)
        session.commit()
        board = contests.team_leaderboards(session, season=season, contest_week=week)
        assert [(row["team_id"], row["team_name"], row["score"]) for row in
            board["team-lifetime-practice"]["open"]] == [(10, "Eureka", 70)]
        _, _, later = contests.ensure_current_contest_data(
            session, now=datetime(2026, 11, 3, 18, tzinfo=timezone.utc))
        assert contests._lifetime_team_practice_scores(session, later) == {10: 70}
        operating.moderation_status = "hidden"
        session.commit()
        assert contests._lifetime_team_practice_scores(session, later) == {}
        assert session.get(PracticeChart, old_chart.id).team_id == historical.id
        assert session.get(PracticeChart, new_chart.id).team_id == 10


def test_current_dashboard_team_independent_of_presentation_label(http_db):
    factory, _, profile = http_db
    with factory() as session:
        halloween = session.scalar(select(Season).where(Season.key == "halloween-2026"))
        assert current_student_team(session, profile_id=profile.id, season=halloween, at=NOW).id == 10
        assert current_student_team(session, profile_id=profile.id, season=None, at=NOW).id == 10


def test_director_contest_accepts_operating_teams_from_different_origins(http_db):
    factory, _, profile = http_db
    with factory() as session:
        from app.models import WoodchuckProfile
        owner = session.get(WoodchuckProfile, profile.id)
        origin = session.scalar(select(Season).where(Season.key == "band-camp-2026"))
        first, second = session.get(Team, 10), session.get(Team, 11)
        second.season_id = origin.id
        for index, team in enumerate((first, second)):
            team.visibility = "private"
            team.director_led = True
            team.creator_profile_id = profile.id
            team.join_code = f"PERSIST{index}"
        session.add(ProfileCapability(profile_id=profile.id, capability="band_director"))
        session.commit()
        submitted = director_dashboard.DirectorContestCreate(title="Persistent Classes",
            starts_at=NOW, ends_at=NOW + timedelta(days=8), finalizes_at=NOW + timedelta(days=9),
            metric="total_minutes", team_ids=[10, 11])
        event = director_dashboard.create_director_contest(session, profile=owner, submitted=submitted, now=NOW)
        assert event.id is not None
        assert first.join_code == "PERSIST0" and second.join_code == "PERSIST1"
        assert director_dashboard._period_roster(session, team_id=10,
            starts_at=NOW, ends_at=NOW + timedelta(days=8)) == {profile.id}


def test_current_hall_moderation_cannot_bypass_hidden_operating_team(http_db):
    factory, _, profile = http_db
    with factory() as session:
        _, _, week = next_week(session)
        operating = session.get(Team, 10)
        origin = session.scalar(select(Season).where(Season.key == "band-camp-2026"))
        historical = Team(family_id=operating.family_id, season_id=origin.id,
            display_name="Old Eureka", normalized_name="old eureka", emblem_key="emoji:dog")
        session.add(historical)
        session.flush()
        legacy_membership = TeamMembership(profile_id=profile.id, team_id=historical.id,
            season_id=origin.id, started_at=datetime(2026, 7, 27, 5, tzinfo=timezone.utc),
            ended_at=datetime(2026, 8, 4, 5, tzinfo=timezone.utc), selected_week_start=date(2026, 7, 27))
        session.add(legacy_membership)
        week.status = "finalized"
        week.finalized_at = NOW
        contest = session.scalar(select(Contest).where(Contest.key == "team-lifetime-practice"))
        result = ContestResult(contest_week_id=week.id, contest_id=contest.id,
            division="open", subject_type="team", subject_key=str(historical.id),
            team_id=historical.id, display_name_snapshot="Old Eureka", score=30,
            rank=1, medal="gold")
        session.add(result)
        session.commit()
        stored = dict(session.execute(select(ContestResult.__table__).where(ContestResult.id == result.id)).mappings().one())
        assert contests.hall_of_champions_payload(session, now=NOW)["teams"][0]["team_name"] == "Eureka"
        internal_before = contests.hall_of_champions_payload(session, _include_internal=True, now=NOW)["teams"]
        operating.moderation_status = "hidden"
        session.commit()
        assert contests.hall_of_champions_payload(session, now=NOW)["teams"] == []
        assert contests.hall_of_champions_payload(session, _include_internal=True, now=NOW)["teams"] == internal_before
        assert dict(session.execute(select(ContestResult.__table__).where(ContestResult.id == result.id)).mappings().one()) == stored


def test_season_activation_never_opens_writer(http_db, monkeypatch):
    _, _, _ = http_db
    def forbidden(*args, **kwargs):
        raise AssertionError("season activation must not open a writer")
    monkeypatch.setattr(season_team_activation.repair, "writer", forbidden)
    result = season_team_activation.activate("unused")
    assert result["status"] == "NOT_READY"
    assert result["reason_codes"] == ["seasonal_team_activation_retired"]


def test_director_hall_preserves_event_without_presentation_label(http_db):
    factory, _, profile = http_db
    with factory() as session:
        from app.models import DirectorTeamContest, DirectorTeamContestResult
        event = DirectorTeamContest(season_id=None, owner_profile_id=profile.id,
            title="Unlabelled event", metric="total_minutes", starts_at=NOW,
            ends_at=NOW + timedelta(days=1), finalizes_at=NOW + timedelta(days=1),
            status="finalized", finalized_at=NOW + timedelta(days=2))
        session.add(event)
        session.flush()
        session.add(DirectorTeamContestResult(contest_id=event.id, team_id=10,
            team_name_snapshot="Eureka", emblem_key_snapshot="letter:A",
            score=10, rank=1, active_participant_count=1, eligible_roster_count=1))
        session.commit()
        events = contests.hall_of_champions_payload(session, _include_internal=True, now=NOW)["director_team_contests"]
        assert len(events) == 1
        assert events[0]["title"] == "Unlabelled event" and events[0]["season"] is None
        assert events[0]["winners"][0]["team_name"] == "Eureka"


@pytest.mark.parametrize("kind", ["book", "board"])
def test_earning_revalidates_profile_after_authority_fence(http_db, monkeypatch, kind):
    factory, client, profile = http_db
    from app import practice_chart_routes
    from app.models import WoodchuckProfile
    module = practice_chart_routes if kind == "book" else contests
    original = module.lock_authority
    invoked = []
    def deletion_before_fence(session):
        if not invoked:
            invoked.append(True)
            with factory() as other:
                row = other.get(WoodchuckProfile, profile.id)
                row.status = "deleted"
                row.session_version += 1
                other.commit()
        return original(session)
    monkeypatch.setattr(module, "lock_authority", deletion_before_fence)
    if kind == "book":
        result = client.post("/practice-charts", json={"practice_date": "2026-10-01",
            "minutes": 10, "submission_key": "deleted-before-attribution", "include_team_contests": True})
    else:
        result = client.post("/contests/camp-points/awards", json={
            "activity_date": "2026-10-01", "activity_type": "care"})
    assert result.status_code == 401, result.text
    with factory() as session:
        assert session.scalar(select(PracticeChart.id).where(PracticeChart.profile_id == profile.id)) is None
        assert session.scalar(select(contests.CampPointAward.id).where(
            contests.CampPointAward.profile_id == profile.id,
            ~contests.CampPointAward.duplicate_key.like("historical-%"))) is None


def test_existing_competition_week_survives_closed_presentation_label(http_db):
    factory, _, _ = http_db
    moment = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
    with factory() as session:
        label, _, week = next_week(session)
        original = dict(session.execute(select(ContestWeek.__table__).where(ContestWeek.id == week.id)).mappings().one())
        label.status = "closed"
        session.commit()
        _, _, same = contests.ensure_current_contest_data(session, now=moment)
        assert same.id == week.id
        assert dict(session.execute(select(ContestWeek.__table__).where(ContestWeek.id == week.id)).mappings().one()) == original


def test_director_mutation_fences_authentication_before_clock(http_db, monkeypatch):
    factory, client, profile = http_db
    from app.models import WoodchuckProfile
    with factory() as session:
        session.add(ProfileCapability(profile_id=profile.id, capability="band_director"))
        team = session.get(Team, 10)
        team.visibility, team.director_led, team.creator_profile_id = "private", True, profile.id
        team.join_code = "FENCED01"
        session.commit()
    trace, mutating = [], [True]
    original_lock = director_dashboard.lock_authority
    def lock(session):
        trace.append("authority")
        return original_lock(session)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            if mutating[0]:
                assert trace, "Director mutation read its clock before authority fence"
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(director_dashboard, "lock_authority", lock)
    monkeypatch.setattr(director_dashboard, "datetime", Clock)
    result = client.post("/director/contests", json={"title": "Fenced Classes",
        "starts_at": NOW.isoformat(), "ends_at": (NOW + timedelta(days=1)).isoformat(),
        "finalizes_at": (NOW + timedelta(days=2)).isoformat(), "metric": "total_minutes", "team_ids": [10]})
    assert result.status_code == 201, result.text
    trace.clear()
    mutating[0] = False
    assert client.get("/director/contests").status_code == 200
    assert trace == []


def test_trivia_duplicate_retry_reacquires_authority_before_profile_lock(http_db, monkeypatch):
    factory, client, profile = http_db
    from sqlalchemy.orm import Session
    from app.models import DailyTriviaAttempt
    question = contests.trivia_question_for(NOW.astimezone(contests.CENTRAL).date())
    answer = question["correct_answer_id"]
    with factory() as session:
        session.add(DailyTriviaAttempt(profile_id=profile.id, activity_date=date(2026, 10, 1),
            selected_answer=answer, correct=True))
        session.commit()
    original_scalar, original_lock, original_state = Session.scalar, contests.lock_authority, contests.lock_state
    hidden, trace = [], []
    def scalar(session, statement, *args, **kwargs):
        descriptions = getattr(statement, "column_descriptions", [])
        if not hidden and descriptions and descriptions[0].get("entity") is DailyTriviaAttempt:
            hidden.append(True)
            return None  # Force the real duplicate INSERT/unique-error retry path.
        return original_scalar(session, statement, *args, **kwargs)
    def lock(session):
        trace.append("authority")
        return original_lock(session)
    def state(session, profile_id):
        trace.append("state")
        return original_state(session, profile_id)
    monkeypatch.setattr(Session, "scalar", scalar)
    monkeypatch.setattr(contests, "lock_authority", lock)
    monkeypatch.setattr(contests, "lock_state", state)
    result = client.post("/contests/trivia/answer", json={"activity_date": "2026-10-01", "selected_answer_id": answer})
    assert result.status_code == 200, result.text
    assert trace[:4] == ["authority", "state", "authority", "state"]
    with factory() as session:
        assert len(session.scalars(select(DailyTriviaAttempt)).all()) == 1
        awards = session.scalars(select(contests.CampPointAward).where(
            ~contests.CampPointAward.duplicate_key.like("historical-%"))).all()
        assert len(awards) == 1 and awards[0].team_id == 10
