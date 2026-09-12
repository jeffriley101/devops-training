"""BOARD presentation uses the durable season supplied by its caller."""
from dataclasses import dataclass

from .models import Season


@dataclass(frozen=True)
class BoardSeason:
    key: str
    title: str

    @property
    def title_words(self) -> list[str]:
        return self.title.split()


def board_season_presentation(season: Season | None) -> BoardSeason:
    # Future theme metadata may be keyed here; dates never belong in this module.
    if season is None:
        return BoardSeason(key="", title="BOARD")
    return BoardSeason(key=season.key, title=season.name)
