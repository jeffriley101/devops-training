from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BoardSeason:
    key: str
    title: str
    starts_on: date

    @property
    def title_words(self) -> list[str]:
        return self.title.split()


# BOARD presentation is deliberately independent from durable contest records.
# Add later seasons here; the newest season whose start date has arrived wins.
BOARD_SEASONS = (
    BoardSeason(
        key="band-camp",
        title="Band Camp",
        starts_on=date(2026, 7, 27),
    ),
    BoardSeason(
        key="back-to-school",
        title="Back to School",
        starts_on=date(2026, 8, 3),
    ),
)


def board_season_for_date(activity_date: date) -> BoardSeason:
    eligible = [season for season in BOARD_SEASONS if season.starts_on <= activity_date]
    return max(eligible or BOARD_SEASONS[:1], key=lambda season: season.starts_on)
