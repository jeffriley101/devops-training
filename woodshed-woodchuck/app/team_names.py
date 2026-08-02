from __future__ import annotations

import re
import unicodedata


MAX_TEAM_NAME_LENGTH = 30
RESERVED_NAMES = {
    "admin", "administrator", "official", "staff", "moderator",
    "woodshed", "woodshed woodchuck",
}
PROHIBITED_SKELETONS = {
    "fuck", "fucker", "fucking", "shit", "bullshit", "bitch",
    "asshole", "nigger", "nigga", "faggot", "cunt",
}
SUBSTITUTIONS = str.maketrans({"@": "a", "4": "a", "3": "e", "1": "i", "!": "i", "0": "o", "$": "s", "5": "s", "7": "t"})


class InvalidTeamName(ValueError):
    pass


def display_team_name(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidTeamName("Enter a team name.")
    normalized = unicodedata.normalize("NFKC", value)
    display = " ".join(normalized.split())
    if not display:
        raise InvalidTeamName("Enter a team name.")
    if len(display) > MAX_TEAM_NAME_LENGTH:
        raise InvalidTeamName("Team names must be 30 characters or fewer.")
    if not any(unicodedata.category(character)[0] in {"L", "N"} for character in display):
        raise InvalidTeamName("Team names must include letters or numbers.")
    return display


def normalized_team_name(value: object) -> tuple[str, str]:
    display = display_team_name(value)
    normalized = unicodedata.normalize("NFKC", display).casefold()
    words = " ".join(normalized.split())
    skeleton = re.sub(r"[^a-z0-9]+", "", words.translate(SUBSTITUTIONS))
    reserved_skeletons = {re.sub(r"[^a-z0-9]+", "", item) for item in RESERVED_NAMES}
    if words in RESERVED_NAMES or skeleton in reserved_skeletons:
        raise InvalidTeamName("That team name is reserved.")
    if any(term in skeleton for term in PROHIBITED_SKELETONS):
        raise InvalidTeamName("Choose another team name.")
    return display, words
