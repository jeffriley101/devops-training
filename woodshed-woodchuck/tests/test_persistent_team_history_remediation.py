"""Historical Hall publication and deletion interval regressions on both databases."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import contests
from app.account_deletion import anonymize_woodchuck_account
from app.age_models import AccountPrivacy
from app.models import (CampPointAward, Contest, ContestResult, ContestWeek, PersistentTeamControl,
                        PracticeChart, Team, TeamMembership, WoodchuckProfile)
from app.team_authority import effective_membership, utc
from tests.team_factory import make_team
from tests.test_persistent_team_authority import authority_db, NOW as DELETE_AT
from tests.test_persistent_team_http import http_db, NOW
from tests.test_persistent_team_boundary_runtime import (
    boundary_db, BOUNDARY, activate, approve_book, book, stage,
)


def historical_team_result(session):
    season, _, week = contests.ensure_current_contest_data(
        session, now=datetime(2026, 8, 3, 18, tzinfo=timezone.utc))
    team = make_team(session, season_id=season.id, display_name="Historical Winners",
                     normalized_name="historical winners", emblem_key="emoji:dog")
    session.add(team)
    session.flush()
    week.status = "finalized"
    week.finalized_at = datetime(2026, 8, 10, 18, tzinfo=timezone.utc)
    contest = session.scalar(select(Contest).where(Contest.key == "team-weekly-practice"))
    result = ContestResult(contest_week_id=week.id, contest_id=contest.id,
        division="open", subject_type="team", subject_key=str(team.id),
        team_id=team.id, display_name_snapshot=team.display_name, score=30,
        rank=1, medal="gold", created_at=week.finalized_at)
    session.add(result)
    session.commit()
    return team, result


def test_historical_only_family_keeps_finalized_hall_aggregate_after_activation(http_db):
    factory, _, _ = http_db
    with factory() as session:
        team, result = historical_team_result(session)
        control = session.get(PersistentTeamControl, 1)
        activation = control.activated_at, control.rules_from_week_start
        control.activated_at = control.rules_from_week_start = None
        session.commit()
        before = contests.hall_of_champions_payload(session, now=NOW)["teams"]
        assert len(before) == 1
        assert before[0]["team_id"] == team.id
        assert before[0]["team_name"] == "Historical Winners"
        assert before[0]["medals"]["gold"] == 1
        saved_result = dict(session.execute(select(ContestResult.__table__).where(
            ContestResult.id == result.id)).mappings().one())
        saved_teams = list(session.execute(select(Team.__table__).order_by(Team.id)).mappings())

        control.activated_at, control.rules_from_week_start = activation
        session.commit()
        assert contests.hall_of_champions_payload(session, now=NOW)["teams"] == before
        assert session.scalar(select(Team.id).where(
            Team.family_id == team.family_id, Team.is_operating.is_(True))) is None
        assert dict(session.execute(select(ContestResult.__table__).where(
            ContestResult.id == result.id)).mappings().one()) == saved_result
        assert list(session.execute(select(Team.__table__).order_by(Team.id)).mappings()) == saved_teams


@pytest.mark.parametrize("restricted", ["private", "hidden"])
@pytest.mark.parametrize("operating_restriction", ["absent", None, "private", "hidden"])
def test_hall_retains_historical_publication_policy_regardless_of_successor(
        http_db, restricted, operating_restriction):
    factory, _, _ = http_db
    with factory() as session:
        team, result = historical_team_result(session)
        if restricted == "private":
            team.visibility = "private"
        else:
            team.moderation_status = "hidden"
        if operating_restriction != "absent":
            session.add(Team(family_id=team.family_id, season_id=None,
                display_name="Current Winners", normalized_name="current winners",
                emblem_key="emoji:cat", is_operating=True,
                visibility="private" if operating_restriction == "private" else "public",
                moderation_status="hidden" if operating_restriction == "hidden" else "active"))
        session.commit()
        saved_result = dict(session.execute(select(ContestResult.__table__).where(
            ContestResult.id == result.id)).mappings().one())
        saved_teams = list(session.execute(select(Team.__table__).order_by(Team.id)).mappings())
        assert contests.hall_of_champions_payload(session, now=NOW)["teams"] == []
        assert dict(session.execute(select(ContestResult.__table__).where(
            ContestResult.id == result.id)).mappings().one()) == saved_result
        assert list(session.execute(select(Team.__table__).order_by(Team.id)).mappings()) == saved_teams


@pytest.mark.parametrize("operating_restriction", [None, "private", "hidden"])
def test_hall_uses_public_operating_identity_or_preserves_historical_identity(
        http_db, operating_restriction):
    factory, _, _ = http_db
    with factory() as session:
        historical, result = historical_team_result(session)
        operating = Team(family_id=historical.family_id, season_id=None,
            display_name="Current Winners", normalized_name="current winners",
            emblem_key="emoji:cat", is_operating=True)
        session.add(operating)
        session.commit()
        before = dict(session.execute(select(ContestResult.__table__).where(
            ContestResult.id == result.id)).mappings().one())
        if operating_restriction == "private":
            operating.visibility = "private"
        elif operating_restriction == "hidden":
            operating.moderation_status = "hidden"
        session.commit()
        saved_teams = list(session.execute(select(Team.__table__).order_by(Team.id)).mappings())
        teams = contests.hall_of_champions_payload(session, now=NOW)["teams"]
        assert len(teams) == 1
        assert teams[0]["medals"]["gold"] == 1
        if operating_restriction:
            assert teams[0]["team_id"] == historical.id
            assert teams[0]["team_name"] == "Historical Winners"
            assert teams[0]["emblem_key"] == "emoji:dog"
        else:
            assert teams[0]["team_id"] == operating.id
            assert teams[0]["team_name"] == "Current Winners"
            assert teams[0]["emblem_key"] == "emoji:cat"
        assert dict(session.execute(select(ContestResult.__table__).where(
            ContestResult.id == result.id)).mappings().one()) == before
        assert list(session.execute(select(Team.__table__).order_by(Team.id)).mappings()) == saved_teams


@pytest.mark.parametrize("operating_restriction", [None, "private", "hidden"])
def test_hall_preserves_contributor_privacy_with_public_historical_team(
        http_db, operating_restriction):
    factory, _, profile = http_db
    with factory() as session:
        historical, result = historical_team_result(session)
        week = session.get(ContestWeek, result.contest_week_id)
        rule = session.get(AccountPrivacy, profile.id)
        rule.age_band, rule.public_from = "under13", None
        session.add(TeamMembership(profile_id=profile.id, team_id=historical.id,
            season_id=historical.season_id, started_at=datetime(2026, 8, 3, 5, tzinfo=timezone.utc),
            ended_at=week.finalized_at, selected_week_start=week.week_start))
        for team_id, key in ((historical.id, "historical-private-team"),
                             (None, "historical-private-null")):
            session.add(PracticeChart(profile_id=profile.id, practice_date=date(2026, 8, 4),
                minutes=30, instrument="Flute", submission_key=key,
                include_contests=True, include_team_contests=True, team_id=team_id,
                created_at=datetime(2026, 8, 4, 18, tzinfo=timezone.utc)))
        for key, subject_type, subject_key in (
                ("weekly-points-leaders", "student", str(profile.id)),
                ("weekly-practice-by-instrument", "instrument", "flute")):
            contest = session.scalar(select(Contest).where(Contest.key == key))
            session.add(ContestResult(contest_week_id=week.id, contest_id=contest.id,
                division="open", subject_type=subject_type, subject_key=subject_key,
                profile_id=profile.id if subject_type == "student" else None,
                instrument="Flute" if subject_type == "instrument" else None,
                display_name_snapshot=profile.display_name if subject_type == "student" else "Flute",
                score=60, rank=1, medal="gold", created_at=week.finalized_at))
        operating = Team(family_id=historical.family_id, season_id=None,
            display_name="Current Winners", normalized_name="current winners",
            emblem_key="emoji:cat", is_operating=True,
            visibility="private" if operating_restriction == "private" else "public",
            moderation_status="hidden" if operating_restriction == "hidden" else "active")
        session.add(operating)
        session.commit()
        models = (Team, TeamMembership, PracticeChart, CampPointAward, ContestResult)
        saved = {model: list(session.execute(select(model.__table__).order_by(model.id)).mappings())
                 for model in models}

        payload = contests.hall_of_champions_payload(session, now=NOW)
        # Stored public Team medal aggregates follow identity publication policy;
        # the individual and instrument results still enforce contributor privacy.
        assert payload["students"] == []
        assert payload["instruments"] == []
        assert len(payload["teams"]) == 1
        assert payload["teams"][0]["medals"]["gold"] == 1
        display = historical if operating_restriction else operating
        assert payload["teams"][0]["team_id"] == display.id
        assert payload["teams"][0]["team_name"] == display.display_name
        for model in models:
            assert list(session.execute(select(model.__table__).order_by(model.id)).mappings()) == saved[model]


@pytest.mark.parametrize("starts_at_deletion", [False, True])
def test_deletion_ends_finite_effective_membership_only(authority_db, starts_at_deletion):
    ids = authority_db.ids
    with authority_db() as session:
        start = DELETE_AT if starts_at_deletion else DELETE_AT - timedelta(days=1)
        old = TeamMembership(profile_id=ids["profile"], team_id=ids["teams"][0],
            season_id=None, started_at=start - timedelta(days=2), ended_at=start,
            selected_week_start=date(2026, 9, 28), is_persistent=True)
        effective = TeamMembership(profile_id=ids["profile"], team_id=ids["teams"][1],
            season_id=None, started_at=start, ended_at=DELETE_AT + timedelta(days=1),
            selected_week_start=date(2026, 10, 5), is_persistent=True)
        future = TeamMembership(profile_id=ids["profile"], team_id=ids["teams"][0],
            season_id=None, started_at=DELETE_AT + timedelta(days=1), ended_at=None,
            selected_week_start=date(2026, 10, 5), is_persistent=True)
        legacy = TeamMembership(profile_id=ids["profile"], team_id=ids["legacy"],
            season_id=ids["current"], started_at=DELETE_AT - timedelta(days=5),
            selected_week_start=date(2026, 9, 28))
        session.add_all([old, effective, future, legacy])
        session.commit()
        other_ids = (old.id, future.id, legacy.id)
        unchanged = list(session.execute(select(TeamMembership.__table__).where(
            TeamMembership.id.in_(other_ids)).order_by(TeamMembership.id)).mappings())
        assert effective_membership(session, ids["profile"], DELETE_AT).id == effective.id

        profile = session.get(WoodchuckProfile, ids["profile"])
        anonymize_woodchuck_account(session, profile=profile, now=DELETE_AT)
        session.commit()
        session.refresh(effective)
        assert utc(effective.ended_at) == DELETE_AT
        assert utc(effective.started_at) == start
        assert effective_membership(session, profile.id, DELETE_AT) is None
        if not starts_at_deletion:
            assert effective_membership(
                session, profile.id, DELETE_AT - timedelta(microseconds=1)).id == effective.id
        assert list(session.execute(select(TeamMembership.__table__).where(
            TeamMembership.id.in_(other_ids)).order_by(TeamMembership.id)).mappings()) == unchanged
        assert profile.status == "deleted"
        # A retry cannot change a historical interval's end time.
        anonymize_woodchuck_account(session, profile=profile, now=DELETE_AT + timedelta(hours=1))
        session.commit()
        session.refresh(effective)
        assert utc(effective.ended_at) == DELETE_AT


def test_later_persistent_monday_switch_keeps_sunday_practice_on_prior_week_team(boundary_db):
    db = boundary_db
    approved = stage(db)
    db.clock["at"] = BOUNDARY
    activate(db, approved)
    db.clock["at"] = BOUNDARY + timedelta(days=6, hours=13)
    result = book(db, date(2026, 10, 11), "original-sunday")
    assert result.status_code == 201, result.text
    approve_book(db, "original-sunday")
    with db.factory() as session:
        original = dict(session.execute(select(PracticeChart.__table__).where(
            PracticeChart.submission_key == "original-sunday")).mappings().one())
        assert original["team_id"] == 10

    db.clock["at"] = BOUNDARY + timedelta(days=7)
    switched = db.client.post("/teams/selection", json={"team_id": 11})
    assert switched.status_code == 200, switched.text
    db.clock["at"] += timedelta(minutes=30)
    result = book(db, date(2026, 10, 11), "late-persistent-sunday")
    assert result.status_code == 201, result.text
    approve_book(db, "late-persistent-sunday")
    result = book(db, date(2026, 10, 12), "current-persistent-monday", minutes=10)
    assert result.status_code == 201, result.text
    with db.factory() as session:
        prior = session.get(ContestWeek, 3)
        assert prior.team_membership_rules_version == "persistent_v1"
        relevant = session.scalars(select(PracticeChart).where(
            PracticeChart.submission_key.in_(
                ("late-persistent-sunday", "current-persistent-monday"))))
        charts = {chart.submission_key: chart.team_id for chart in relevant}
        assert charts == {"late-persistent-sunday": 10, "current-persistent-monday": 11}
        rosters = contests._eligible_weekly_team_rosters(session, prior)
        assert set(rosters) == {10} and len(rosters[10]) == 20
        scores = contests._weekly_team_scores(session, prior)["open"]
        assert scores["totals"] == {10: 60}
        assert scores["tpr"] == {10: 18.3}
        assert effective_membership(session, db.profile.id, db.clock["at"]).team_id == 11
        assert dict(session.execute(select(PracticeChart.__table__).where(
            PracticeChart.id == original["id"])).mappings().one()) == original
