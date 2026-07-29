from __future__ import annotations


def instrument(
    key: str,
    label: str,
    symbol: str,
    *,
    image_url: str | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "icon_type": "image" if image_url else "emoji",
        "icon": None if image_url else symbol,
        "image_url": image_url,
        "fallback_symbol": symbol,
    }


INSTRUMENT_DEFINITIONS = (
    instrument("flute", "Flute", "🪈"),
    instrument(
        "clarinet",
        "Clarinet",
        "♪",
        image_url="/static/img/instruments/clarinet.svg",
    ),
    instrument("saxophone", "Saxophone", "🎷"),
    instrument("trumpet", "Trumpet", "🎺"),
    instrument("trombone", "Trombone", "🪊"),
    instrument(
        "tuba",
        "Tuba",
        "♫",
        image_url="/static/img/instruments/tuba.svg",
    ),
    instrument("percussion", "Percussion", "🥁"),
    instrument("drum-major", "Drum Major", "🫡"),
    instrument("color-guard", "Color Guard", "🚩"),
    instrument("violin", "Violin", "🎻"),
    instrument("guitar", "Guitar", "🎸"),
    instrument("banjo", "Banjo", "🪕"),
    instrument("piano-keyboard", "Piano / Keyboard", "🎹"),
    instrument("accordion", "Accordion", "🪗"),
    instrument("harp", "Harp", "🪉"),
    instrument("hand-percussion", "Hand Percussion", "🪘"),
    instrument("auxiliary-percussion", "Auxiliary Percussion", "🪇"),
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
