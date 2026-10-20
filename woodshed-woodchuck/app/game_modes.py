"""Canonical current musician preference; legacy Level is history, not XP."""
from copy import deepcopy
from typing import Literal, get_args


GameMode = Literal["Beginner", "Challenge", "Performance"]
GAME_MODES = get_args(GameMode)
GAME_MODE_CHECK = "game_mode IS NULL OR game_mode IN ({})".format(
    ", ".join(repr(mode) for mode in GAME_MODES)
)


def normalize_game_mode(value: object) -> GameMode:
    """Accept canonical names with surrounding form whitespace only."""
    if not isinstance(value, str) or value.strip() not in GAME_MODES:
        raise ValueError("Choose a supported Game Mode.")
    return value.strip()


def bootstrap_game_mode(legacy_level: object, saved_state: object = None) -> GameMode | None:
    """Conservative compatibility only; never use this for current mode reads.

    A migration must use its own frozen equivalent, not import runtime code.
    Persisted selections require exact strings; other musician Levels do not
    imply Challenge or Performance.
    """
    preferences = saved_state.get("_weekly_preferences") if isinstance(saved_state, dict) else None
    mode = preferences.get("game_mode") if isinstance(preferences, dict) else None
    if isinstance(mode, str) and mode in GAME_MODES:
        return mode
    return "Beginner" if legacy_level == "Beginner" else None


def set_game_mode(profile, value: object) -> None:
    """One validated writer. Caller owns locking and the transaction."""
    profile.game_mode = normalize_game_mode(value)


def mode_state(profile, state: object) -> dict:
    """A separate compatibility projection; saved/client JSON is not authority."""
    result = deepcopy(state) if isinstance(state, dict) else {}
    preferences = result.get("_weekly_preferences")
    preferences = dict(preferences) if isinstance(preferences, dict) else {}
    preferences["game_mode"] = profile.game_mode
    result["_weekly_preferences"] = preferences
    if isinstance(result.get("profile"), dict):
        result["profile"]["game_mode"] = profile.game_mode
    return result
