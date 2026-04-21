import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db import get_connection


BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "data" / "results"
RESULT_FILE = RESULTS_DIR / "latest_result.json"
ET_TZ = ZoneInfo("America/New_York")


def fetch_latest_daily_low_result() -> dict | None:
    today_et = datetime.now(ET_TZ).date()

    query = """
        SELECT
            s.symbol,
            d.trade_date,
            d.low_bucket_time
        FROM daily_low_summary d
        JOIN symbols s
          ON s.symbol_id = d.symbol_id
        WHERE s.symbol = 'GC'
          AND d.low_bucket_time IS NOT NULL
          AND d.trade_date < %s
        ORDER BY d.trade_date DESC
        LIMIT 1
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (today_et,))
            row = cur.fetchone()

    if row is None:
        return None

    trade_date = row[1]
    winning_bucket = row[2]

    if trade_date is None or winning_bucket is None:
        return None

    winning_bucket_str = str(winning_bucket)[:5]

    return {
        "game_date_et": trade_date.isoformat(),
        "symbol": "GOLD",
        "winning_bucket": winning_bucket_str,
        "resolved_at": datetime.now(ET_TZ).isoformat(),
        "source_name": "jilt",
        "source_version": "1.0",
    }


def publish_latest_result_artifact() -> dict | None:
    result = fetch_latest_daily_low_result()
    if result is None:
        return None

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    return {
        "artifact_written": True,
        "output_path": str(RESULT_FILE),
        "game_date_et": result["game_date_et"],
        "winning_bucket": result["winning_bucket"],
    }
