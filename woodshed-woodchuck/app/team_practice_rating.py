from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable


# TPR intentionally uses a meaningful-practice threshold and a conservative
# per-player cap. These values are centralized so the rating can be tuned as
# real ensemble data accumulates without changing its data source.
ACTIVE_MINUTES_THRESHOLD = 5
TPR_MEMBER_MINUTES_CAP = 120
PARTICIPATION_BASE = 0.75
PARTICIPATION_WEIGHT = 0.25
TPR_NORMALIZATION = 2.5


@dataclass(frozen=True)
class TeamPracticeRating:
    rating: float
    active_participants: int
    eligible_roster: int
    average_minutes: float
    participation_rate: float


def calculate_team_practice_rating(
    member_minutes: Iterable[int], *, eligible_roster: int
) -> TeamPracticeRating:
    """Calculate a readable, size-softened weekly team practice rating."""
    if eligible_roster < 0:
        raise ValueError("Eligible roster size cannot be negative.")
    active = [
        min(max(0, int(minutes)), TPR_MEMBER_MINUTES_CAP)
        for minutes in member_minutes
        if int(minutes) >= ACTIVE_MINUTES_THRESHOLD
    ]
    active_count = len(active)
    roster = max(eligible_roster, active_count)
    if active_count == 0 or roster == 0:
        return TeamPracticeRating(0.0, active_count, roster, 0.0, 0.0)

    average = sum(active) / active_count
    participation_rate = active_count / roster
    participation_factor = (
        PARTICIPATION_BASE + PARTICIPATION_WEIGHT * participation_rate
    )
    raw = average * sqrt(active_count) * participation_factor
    return TeamPracticeRating(
        rating=round(raw / TPR_NORMALIZATION, 1),
        active_participants=active_count,
        eligible_roster=roster,
        average_minutes=average,
        participation_rate=participation_rate,
    )
