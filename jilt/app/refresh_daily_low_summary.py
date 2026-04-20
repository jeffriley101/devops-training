from app.config import MARKET_TIMEZONE
from app.db import get_connection


def main() -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET TIME ZONE '{MARKET_TIMEZONE}';")

            cur.execute(
                """
                INSERT INTO daily_low_summary (
                    symbol_id,
                    trade_date,
                    lowest_price,
                    low_bucket_time,
                    bar_timestamp
                )
                WITH ranked_lows AS (
                    SELECT
                        ib.symbol_id,
                        ib.trade_date,
                        ib.low_value,
                        ib.bar_timestamp,
                        ib.bar_timestamp::time AS low_bucket_time,
                        ROW_NUMBER() OVER (
                            PARTITION BY ib.symbol_id, ib.trade_date
                            ORDER BY ib.low_value ASC, ib.bar_timestamp ASC
                        ) AS row_num
                    FROM intraday_bars ib
                )
                SELECT
                    symbol_id,
                    trade_date,
                    low_value AS lowest_price,
                    low_bucket_time,
                    bar_timestamp
                FROM ranked_lows
                WHERE row_num = 1
                ON CONFLICT (symbol_id, trade_date) DO UPDATE
                SET
                    lowest_price = EXCLUDED.lowest_price,
                    low_bucket_time = EXCLUDED.low_bucket_time,
                    bar_timestamp = EXCLUDED.bar_timestamp,
                    created_at = NOW();
                """
            )
            refreshed_rows = cur.rowcount

    print(f"Daily low summary refreshed successfully. Rows affected: {refreshed_rows}")

    return {
        "refreshed_rows": refreshed_rows,
    }


if __name__ == "__main__":
    main()
