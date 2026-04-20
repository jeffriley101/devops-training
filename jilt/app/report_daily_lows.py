from app.config import MARKET_TIMEZONE, SYMBOL
from app.db import get_connection


def main() -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET TIME ZONE '{MARKET_TIMEZONE}';")

            cur.execute(
                """
                SELECT
                    s.symbol,
                    s.display_name,
                    dls.trade_date,
                    dls.lowest_price,
                    dls.low_bucket_time,
                    dls.bar_timestamp
                FROM daily_low_summary dls
                JOIN symbols s
                    ON dls.symbol_id = s.symbol_id
                WHERE s.symbol = %s
                ORDER BY dls.trade_date;
                """,
                (SYMBOL,),
            )

            rows = cur.fetchall()

    print()
    print("JILT Daily Low Report")
    print("=====================")
    print()

    for row in rows:
        symbol, display_name, trade_date, lowest_price, low_bucket_time, bar_timestamp = row
        print(
            f"{trade_date} | {symbol} | {display_name} | "
            f"low={lowest_price:.6f} | bucket={low_bucket_time} | timestamp={bar_timestamp}"
        )

    return {
        "row_count": len(rows),
    }


if __name__ == "__main__":
    main()
