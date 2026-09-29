"""BOARD presentation uses the durable season supplied by its caller."""
from dataclasses import dataclass

from .models import Season


# Artwork names are presentation metadata, never season identities or dates.
SEASON_ARTWORK = {"halloween-2026": "harvest"}


@dataclass(frozen=True)
class BoardSeason:
    key: str
    title: str

    @property
    def title_words(self) -> list[str]:
        return self.title.split()

    @property
    def artwork_theme(self) -> str | None:
        return SEASON_ARTWORK.get(self.key)

    @property
    def polaroid_url(self) -> str | None:
        if self.artwork_theme:
            return f"/static/img/seasonal/{self.artwork_theme}/board_polaroid_1200x900.jpg"
        return None

    @property
    def master_url(self) -> str | None:
        if self.artwork_theme:
            return f"/static/img/seasonal/{self.artwork_theme}/master_2x1.png"
        return None


def board_season_presentation(season: Season | None) -> BoardSeason:
    # Dates and current-season selection remain with the caller's durable lookup.
    if season is None:
        return BoardSeason(key="", title="BOARD")
    return BoardSeason(key=season.key, title=season.name)
