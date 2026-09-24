"""Server-owned appearance preferences in the existing revisioned state row."""
from copy import deepcopy

from .instruments import shed_character_url
from .memberships import student_has_full_access


PALETTE = [
    {"key": key, "label": key.title(), "hue": hue, "premium": premium,
     "saturation_floor": 0.65, "lightness_lift": 0.22 if key == "pink" else 0,
     "lightness_scale": 0.55 if key in {"navy", "maroon"} else 1}
    for key, hue, premium in (
        ("blue", 220, False), ("green", 120, False), ("red", 0, False),
        ("purple", 275, False), ("pink", 330, False),
        ("orange", 30, True), ("yellow", 60, True), ("teal", 175, True),
        ("cyan", 190, True), ("lime", 85, True), ("navy", 230, True),
        ("maroon", 350, True), ("gold", 45, True),
    )
]
COLORS = {color["key"]: color for color in PALETTE}
DEFAULTS = {"hoodie": "blue", "hat": "green"}


def appearance_payload(session, profile, state=None):
    saved = ((state.state_json or {}) if state else {}).get("appearance", {})
    saved = saved if isinstance(saved, dict) else {}
    choices = {part: saved.get(part, default) if isinstance(saved.get(part, default), str)
               and saved.get(part, default) in COLORS else default
               for part, default in DEFAULTS.items()}
    premium = student_has_full_access(session, profile.id)
    effective = {part: choice if premium or not COLORS[choice]["premium"] else DEFAULTS[part]
                 for part, choice in choices.items()}
    return {"instrument": profile.instrument, "source": shed_character_url(profile.instrument),
            "saved": choices, "effective": effective, "premium": premium, "palette": PALETTE}


def validate_choices(submitted, previous, premium):
    for part in DEFAULTS:
        choice = submitted[part]
        if choice not in COLORS:
            raise ValueError("Choose an available color.")
        # An expired preference can be kept, but cannot be newly selected.
        if COLORS[choice]["premium"] and not premium and previous.get(part) != choice:
            raise PermissionError("This color requires Premium.")
    return deepcopy(submitted)
