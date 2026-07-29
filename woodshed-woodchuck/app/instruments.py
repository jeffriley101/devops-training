from __future__ import annotations


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

INSTRUMENT_OPTIONS = [item["label"] for item in INSTRUMENT_DEFINITIONS]
INSTRUMENTS_BY_LABEL = {
    item["label"].casefold(): item for item in INSTRUMENT_DEFINITIONS
}


def normalize_supported_instrument(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Choose a supported instrument.")
    definition = INSTRUMENTS_BY_LABEL.get(" ".join(value.split()).casefold())
    if definition is None:
        raise ValueError("Choose a supported instrument.")
    return definition["label"]


def instrument_definition_payloads() -> list[dict[str, object]]:
    return [dict(item) for item in INSTRUMENT_DEFINITIONS]
