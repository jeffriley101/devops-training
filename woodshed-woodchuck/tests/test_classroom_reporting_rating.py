"""Leadership-confirmed square-root/five-day rating and calendar regressions."""
from datetime import date, timedelta
from math import sqrt
from types import SimpleNamespace

import pytest

from app.student_practice_metrics import (
    practice_totals, student_practice_rating, student_practice_snapshot, trend, week_start,
)


def chart(ident, day, minutes, source="p-book", seconds=None):
    return SimpleNamespace(id=ident, practice_date=day, minutes=minutes,
                           source=source, detected_playing_seconds=seconds)


@pytest.mark.parametrize("minutes", [120, 121, 999, 100000])
@pytest.mark.parametrize("verified,pristine,bonus", [
    (False, False, 0), (True, False, 3), (False, True, 1), (True, True, 4),
])
def test_accepted_volume_cap_and_independent_category_bonuses(minutes, verified, pristine, bonus):
    assert student_practice_rating(minutes, 5, verified, pristine) == 100 + bonus


@pytest.mark.parametrize("minutes,days,expected", [
    (20, 1, 39.19183588453085), (30, 1, 48.0), (60, 1, 67.88225099390857),
    (120, 0, 95.0), (120, 1, 96.0), (120, 2, 97.0), (120, 3, 98.0),
    (120, 4, 99.0), (120, 5, 100.0), (120, 7, 100.0), (120, -1, 95.0),
    (.25, 1, sqrt(.25 / 120) * 96),
])
def test_confirmed_square_root_curve_and_five_day_adjustment(minutes, days, expected):
    assert student_practice_rating(minutes, days, False, False) == pytest.approx(expected)


def test_confirmed_example_displays_one_decimal_without_rounding_computation():
    result = student_practice_rating(20, 1, False, False)
    assert f"{result:.1f}" == "39.2"
    assert result != 39.2


@pytest.mark.parametrize("minutes", [-10, 0])
@pytest.mark.parametrize("verified,pristine", [(False, False), (True, False),
                                             (False, True), (True, True)])
def test_no_positive_practice_cannot_receive_only_category_bonuses(minutes, verified, pristine):
    assert student_practice_rating(minutes, 5, verified, pristine) == 0


@pytest.mark.parametrize("verified,pristine,bonus", [
    (False, False, 0), (True, False, 3), (False, True, 1), (True, True, 4),
])
def test_category_bonuses_do_not_reduce_or_replace_the_unverified_core(verified, pristine, bonus):
    core = student_practice_rating(20, 1, False, False)
    assert student_practice_rating(20, 1, verified, pristine) == pytest.approx(core + bonus)


@pytest.mark.parametrize("delta,direction,arrow", [
    (-3.001, "down", "↓"), (-3, "steady", "→"), (0, "steady", "→"),
    (3, "steady", "→"), (3.001, "up", "↑"),
])
def test_accepted_inclusive_trend_thresholds(delta, direction, arrow):
    result = trend(delta)
    assert result["delta"] == delta
    assert result["direction"] == direction
    assert result["arrow"] == arrow


def test_last_completed_week_and_four_preceding_weeks_ignore_current_activity():
    today = date(2026, 11, 2)
    completed = date(2026, 10, 26)
    charts = [chart(offset + 1, completed - timedelta(weeks=offset), minutes)
              for offset, minutes in enumerate([120, 90, 60, 30, 10])]
    expected = [student_practice_rating(minutes, 1, False, False)
                for minutes in [120, 90, 60, 30, 10]]
    snapshot = student_practice_snapshot(charts, set(), today=today)
    assert snapshot["rating_week_values"] == expected
    assert snapshot["rating"] == expected[0]
    assert snapshot["trend"] == trend(expected[0] - sum(expected[1:]) / 4)
    charts.append(chart(99, today, 999))
    fresh = student_practice_snapshot(charts, set(), today=today)
    assert fresh["rating"] == snapshot["rating"]
    assert fresh["trend"] == snapshot["trend"]
    assert fresh["weekly"]["total"] == 999


def test_historical_week_selects_its_own_completed_rating_and_baseline():
    today = date(2026, 11, 4)
    selected = date(2026, 10, 19)
    charts = [chart(offset + 1, selected - timedelta(weeks=offset), minutes)
              for offset, minutes in enumerate([100, 80, 60, 40, 20])]
    charts.extend([chart(90, date(2026, 10, 26), 999), chart(91, today, 999)])
    snapshot = student_practice_snapshot(charts, set(), today=today, selected_week=selected)
    assert snapshot["rating_week_values"] == [student_practice_rating(minutes, 1, False, False)
                                             for minutes in [100, 80, 60, 40, 20]]
    assert snapshot["weekly"]["total"] == 100
    assert week_start(date(2026, 11, 1)) == date(2026, 10, 26)
    assert week_start(date(2026, 11, 2)) == date(2026, 11, 2)


def test_totals_keep_positive_days_distinct_and_pristine_mutually_exclusive():
    monday = date(2026, 10, 26)
    charts = [chart(1, monday, 30), chart(2, monday, 20),
              chart(3, monday + timedelta(days=1), 0, "pristine", 19),
              chart(4, monday + timedelta(days=2), 0)]
    result = practice_totals(charts, {1, 3})
    assert result == {"total": 50 + 19/60, "verified": 30.0, "pristine": 19/60,
                      "total_seconds": 3019, "verified_seconds": 1800, "pristine_seconds": 19,
                      "days": 2, "charts": 4}
