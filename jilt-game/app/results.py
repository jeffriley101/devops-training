import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import JILT_RESULT_JSON_PATH


ET_TZ = ZoneInfo("America/New_York")


def format_result_timestamp_et(value: str) -> str:
    dt = datetime.fromisoformat(value)
    et_value = dt.astimezone(ET_TZ)
    return et_value.strftime("%Y-%m-%d %I:%M:%S %p ET")


def load_latest_result() -> dict | None:
    if not JILT_RESULT_JSON_PATH.exists():
        return None

    raw = json.loads(JILT_RESULT_JSON_PATH.read_text(encoding="utf-8"))

    required_keys = {
        "game_date_et",
        "symbol",
        "winning_bucket",
        "resolved_at",
        "source_name",
        "source_version",
    }

    if not required_keys.issubset(raw.keys()):
        return None

    return {
        "game_date_et": raw["game_date_et"],
        "symbol": raw["symbol"],
        "winning_bucket": raw["winning_bucket"],
        "resolved_at": raw["resolved_at"],
        "resolved_at_display": format_result_timestamp_et(raw["resolved_at"]),
        "source_name": raw["source_name"],
        "source_version": raw["source_version"],
    }
