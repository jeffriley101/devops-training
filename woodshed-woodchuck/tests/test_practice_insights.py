"""Full gates protect analysis, never custody of the student's practice records."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from test_memberships import db, client, grant, NOW, ADMIN
from app import main, practice_chart_routes as routes, feature_access as features
from app import memberships as memberships
from app.models import PracticeChart, PracticeChartVerification, Membership, MembershipSeat
from app.student_practice_metrics import practice_insights


@pytest.fixture
def insights_db(db, monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", db)
    monkeypatch.setattr(routes, "SessionLocal", db)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 14, 12, tzinfo=tz)
    monkeypatch.setattr(routes, "datetime", Clock)
    with db() as session:
        for pid, day, minutes, source, seconds in [
            (1, 17, 10, "manual", None), (1, 17, 5, "manual", None),
            (1, 24, 20, "pristine", 1200), (1, 25, 0, "pristine", 20),
            (2, 17, 999, "manual", None),
        ]:
            session.add(PracticeChart(profile_id=pid, practice_date=date(2026, 8, day),
                minutes=minutes, instrument="Trumpet", source=source,
                detected_playing_seconds=seconds))
        session.add(PracticeChart(profile_id=1, practice_date=date(2026, 9, 14),
            minutes=777, instrument="Trumpet", source="manual"))
        session.flush()
        chart = session.scalar(select(PracticeChart).where(PracticeChart.minutes == 10))
        session.add(PracticeChartVerification(practice_chart_id=chart.id,
                    verifier_id=1, status="approved"))
        session.commit()
    return db


def test_registry(insights_db):
    assert features.FEATURES["practice_insights"] == features.Feature(True, "full")
    reserved = {"advanced_practice_analytics", "practice_history_tools",
                "customization_collections", "bonus_game_content",
                "advanced_exercises", "seasonal_side_activities"}
    assert set(features.FEATURES) == reserved | {"practice_insights"}
    grant(insights_db, "student")
    with insights_db() as session:
        assert features.can_use_feature(session, 1, "practice_insights")
        for key in reserved | {"unknown"}:
            assert not features.can_use_feature(session, 1, key)


@pytest.mark.parametrize("kind,code", [(None, 401), ("adult", 401), ("student", 403)])
def test_denied_no_data(insights_db, kind, code):
    response = client(kind).get("/practice-charts/insights?profile_id=2&full=true")
    assert response.status_code == code
    assert set(response.json()) == {"detail"}
    assert "no-store" in response.headers["cache-control"]


def test_full_summary_and_privacy(insights_db):
    grant(insights_db, "student")
    response = client("student").get("/practice-charts/insights?profile_id=2&week=2026-09-14")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    data = response.json()
    assert set(data) == {"weeks", "total_minutes", "average_weekly_minutes", "total_seconds", "average_weekly_seconds"}
    assert data["total_minutes"] == 2120 / 60
    assert data["average_weekly_minutes"] == 2120 / 240
    assert data["weeks"][0] == dict(week_start="2026-08-17", week_end="2026-08-23",
        minutes=15, days=1, verified_minutes=10, pristine_minutes=0,
        seconds=900, verified_seconds=600, pristine_seconds=0)
    assert data["weeks"][1]["pristine_minutes"] == 1220 / 60
    assert data["weeks"][1]["days"] == 2  # Positive sub-minute practice counts.
    assert data["weeks"][3]["week_end"] == "2026-09-13"
    assert "payer" not in response.text and "profile_id" not in response.text
    assert client("student", id=2).get("/practice-charts/insights").status_code == 403


@pytest.mark.parametrize("loss", ["expired", "revoked", "removed"])
def test_access_loss_preserves_open_data(insights_db, loss, monkeypatch):
    member = grant(insights_db, "student")
    browser = client("student")
    assert 'data-enabled="true"' in browser.get("/p-book").text
    with insights_db() as session:
        if loss == "expired":
            session.get(Membership, member).access_until = NOW + timedelta(seconds=1)
        elif loss == "revoked":
            memberships.revoke_complimentary_membership(session, member, ADMIN)
        else:
            seat = session.scalar(select(MembershipSeat).where(MembershipSeat.membership_id == member))
            memberships.remove_seat(session, member, seat.id, ADMIN)
        session.commit()
    monkeypatch.setattr(memberships, "clock", lambda: NOW + timedelta(seconds=2))
    assert browser.get("/practice-charts/insights").status_code == 403
    page = browser.get("/p-book")
    assert 'data-enabled="false"' in page.text
    assert "no-store" in page.headers["cache-control"]
    assert browser.get("/practice-charts").status_code == 200
    assert browser.get("/practice-charts/totals").status_code == 200
    assert browser.get("/practice-charts/streak").status_code == 200
    with insights_db() as session:
        assert len(session.scalars(select(PracticeChart)).all()) == 6


def test_open_book_and_truthful_membership(insights_db):
    browser = client("student")
    page = browser.get("/p-book")
    assert page.status_code == 200
    assert 'data-enabled="false"' in page.text
    assert "Full Access includes a four-week practice summary." in page.text
    assert "raw history, current-week totals, streak" in page.text
    assert '/membership?as_account=student' in page.text
    membership = browser.get("/membership?as_account=student").text
    assert "Practice Insights with Full Access" in membership
    for key in features.FEATURES.keys() - {"practice_insights"}:
        assert key not in page.text and key not in membership


def test_adult_funded_seat_has_insights_without_payer_data(insights_db):
    member = grant(insights_db, "adult")
    with insights_db() as session:
        memberships.add_seat_by_woodchuck_id(session, member, "WC-MEMBER1",
                                            memberships.Actor("adult", 1))
        session.commit()
    response = client("student").get("/practice-charts/insights")
    assert response.status_code == 200
    assert response.json()["total_minutes"] == 2120 / 60
    for private in ("payer1", "Adult 1", "billing_account", "membership_id", "email"):
        assert private not in response.text
    assert client("adult").get("/practice-charts/insights").status_code == 401


def test_practice_submission_remains_open(insights_db):
    browser = client("student")
    response = browser.post("/practice-charts", json={"minutes": 10,
        "practice_date": "2026-09-13", "instrument": "Trumpet",
        "practice_details": ["Scales"], "include_in_contests": False,
        "include_in_team": False})
    assert response.status_code in (200, 201)
    assert browser.get("/practice-charts/insights").status_code == 403


def test_pristine_precedence_and_approval_semantics():
    charts = [SimpleNamespace(id=1, practice_date=date(2026, 9, 7), minutes=4,
                              source="pristine"),
              SimpleNamespace(id=2, practice_date=date(2026, 9, 8), minutes=7,
                              source="manual")]
    week = practice_insights(charts, {1}, today=date(2026, 9, 14))["weeks"][-1]
    assert week["pristine_minutes"] == 4
    assert week["verified_minutes"] == 0
    assert week["days"] == 2


@pytest.mark.parametrize("today", [date(2026, 9, 14), date(2026, 9, 20)])
def test_exact_completed_weeks(today):
    charts = [SimpleNamespace(id=i, practice_date=day, minutes=minutes, source="manual")
              for i, (day, minutes) in enumerate([
                  (date(2026, 8, 16), 999), (date(2026, 8, 17), 1),
                  (date(2026, 9, 13), 3), (date(2026, 9, 14), 999)])]
    result = practice_insights(charts, set(), today=today)
    assert len(result["weeks"]) == 4
    assert result["total_minutes"] == 4
    assert result["average_weekly_minutes"] == 1
    assert [w["week_start"] for w in result["weeks"]] == [
        "2026-08-17", "2026-08-24", "2026-08-31", "2026-09-07"]


def test_empty_weeks_and_year_boundary():
    result = practice_insights([], set(), today=date(2027, 1, 4))
    assert result["weeks"][0]["week_start"] == "2026-12-07"
    assert result["weeks"][-1]["week_end"] == "2027-01-03"
    assert result["average_weekly_minutes"] == 0
