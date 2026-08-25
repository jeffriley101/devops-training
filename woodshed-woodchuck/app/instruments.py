from __future__ import annotations

import re


def instrument(
    key: str,
    label: str,
    symbol: str,
    *,
    team_label: str,
    image_url: str | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "team_label": team_label,
        "icon_type": "image" if image_url else "emoji",
        "icon": None if image_url else symbol,
        "image_url": image_url,
        "fallback_symbol": symbol,
    }


INSTRUMENT_DEFINITIONS = (
    instrument("flute", "Flute", "🪈", team_label="The Flutes"),
    instrument(
        "clarinet",
        "Clarinet",
        "♪",
        team_label="The Clarinets",
        image_url="/static/img/instruments/clarinet.svg",
    ),
    instrument("saxophone", "Saxophone", "🎷", team_label="The Saxophones"),
    instrument("trumpet", "Trumpet", "🎺", team_label="The Trumpets"),
    instrument("trombone", "Trombone", "🪊", team_label="The Trombones"),
    instrument(
        "tuba",
        "Tuba",
        "♫",
        team_label="The Tubas",
        image_url="/static/img/instruments/tuba.svg",
    ),
    instrument("percussion", "Percussion", "🥁", team_label="The Percussion"),
    instrument("drum-major", "Drum Major", "🫡", team_label="The Drum Majors"),
    instrument("color-guard", "Color Guard", "🚩", team_label="The Color Guard"),
    instrument("violin", "Violin", "🎻", team_label="The Violins"),
    instrument("guitar", "Guitar", "🎸", team_label="The Guitars"),
    instrument("banjo", "Banjo", "🪕", team_label="The Banjos"),
    instrument("piano-keyboard", "Piano / Keyboard", "🎹", team_label="The Pianos & Keyboards"),
    instrument("accordion", "Accordion", "🪗", team_label="The Accordions"),
    instrument("harp", "Harp", "🪉", team_label="The Harps"),
    instrument("hand-percussion", "Hand Percussion", "🪘", team_label="The Hand Percussion"),
    instrument("auxiliary-percussion", "Auxiliary Percussion", "🪇", team_label="The Auxiliary Percussion"),
)

_DEFAULT_SHED_ARTWORK_URL = "/static/img/shed-cabin-new.png"
_SHED_ARTWORK_BY_INSTRUMENT_KEY = {
    "trumpet": "/static/img/woodchuck-trumpet.png",
    "trombone": "/static/img/woodchuck-trumpet.png",
    "tuba": "/static/img/woodchuck-trumpet.png",
    "percussion": "/static/img/woodchuck-drum.png",
    "hand-percussion": "/static/img/woodchuck-drum.png",
    "auxiliary-percussion": "/static/img/woodchuck-drum.png",
    "color-guard": "/static/img/woodchuck-drum.png",
    "drum-major": "/static/img/woodchuck-drum.png",
    "harp": "/static/img/woodchuck-guitar.png",
    "piano-keyboard": "/static/img/woodchuck-guitar.png",
    "banjo": "/static/img/woodchuck-guitar.png",
    "guitar": "/static/img/woodchuck-guitar.png",
    "violin": "/static/img/woodchuck-guitar.png",
}


def shed_artwork_url(instrument_value: str | None) -> str:
    """Return the single production SHED artwork for every instrument."""
    return _DEFAULT_SHED_ARTWORK_URL


INSTRUMENT_OPTIONS = [item["label"] for item in INSTRUMENT_DEFINITIONS]
INSTRUMENTS_BY_LABEL = {
    item["label"].casefold(): item for item in INSTRUMENT_DEFINITIONS
}

_PIANO_KEYBOARD_ALIASES = frozenset({
    "piano keyboard", "piano", "keyboard",
})


def canonical_instrument_key(value: str) -> str:
    """Return a stable server-side key for supported instrument aliases."""
    if not isinstance(value, str):
        raise ValueError("Choose a supported instrument.")
    normalized = re.sub(r"[\s/_-]+", " ", value.strip().casefold())
    if normalized in _PIANO_KEYBOARD_ALIASES:
        return "piano-keyboard"
    for definition in INSTRUMENT_DEFINITIONS:
        key = str(definition["key"])
        label = re.sub(
            r"[\s/_-]+", " ", str(definition["label"]).strip().casefold()
        )
        if normalized in {label, key.replace("-", " ")}:
            return key
    raise ValueError("Choose a supported instrument.")


def normalize_supported_instrument(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Choose a supported instrument.")
    definition = INSTRUMENTS_BY_LABEL.get(" ".join(value.split()).casefold())
    if definition is None:
        raise ValueError("Choose a supported instrument.")
    return definition["label"]


def instrument_definition_payloads() -> list[dict[str, object]]:
    return [dict(item) for item in INSTRUMENT_DEFINITIONS]
