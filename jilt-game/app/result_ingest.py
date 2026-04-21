from app.db import upsert_daily_result
from app.results import load_latest_result


def ingest_latest_result_file_to_db() -> dict | None:
    latest_result = load_latest_result()
    if latest_result is None:
        return None

    upsert_daily_result(
        symbol_code=latest_result["symbol"],
        game_date_et=latest_result["game_date_et"],
        winning_bucket=latest_result["winning_bucket"],
        resolved_at=latest_result["resolved_at"],
        source_name=latest_result["source_name"],
        source_version=latest_result["source_version"],
    )

    return latest_result
