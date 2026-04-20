from app.config import MARKET_TIMEZONE, SYMBOL
from app.db import get_connection


def main() -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET TIME ZONE '{MARKET_TIMEZONE}';")

            cur.execute(
                """
                SELECT COUNT(*)
                FROM daily_low_summary dls
                JOIN symbols s
                    ON dls.symbol_id = s.symbol_id
                WHERE s.symbol = %s;
                """,
                (SYMBOL,),
            )
            total_days = cur.fetchone()[0]

            cur.execute(
                """
                SELECT
                    dls.low_bucket_time,
                    COUNT(*) AS times_was_daily_low
                FROM daily_low_summary dls
                JOIN symbols s
                    ON dls.symbol_id = s.symbol_id
                WHERE s.symbol = %s
                GROUP BY dls.low_bucket_time
                ORDER BY times_was_daily_low DESC, dls.low_bucket_time ASC;
                """,
                (SYMBOL,),
            )
            rows = cur.fetchall()

    print()
    print("JILT Low Bucket Frequency Report")
    print("================================")
    print()
    print(f"Total trading days analyzed: {total_days}")
    print()

    if not rows:
        print("No summary data found.")
        return {
            "total_days": total_days,
            "bucket_count": 0,
            "top_bucket_time": None,
            "top_bucket_days": 0,
        }

    for low_bucket_time, count in rows:
        print(f"{low_bucket_time} | {count}")

    top_bucket_time, top_count = rows[0]
    print()
    print(f"Most common low bucket so far: {top_bucket_time} ({top_count} days)")

    return {
        "total_days": total_days,
        "bucket_count": len(rows),
        "top_bucket_time": str(top_bucket_time),
        "top_bucket_days": top_count,
    }


if __name__ == "__main__":
    main()
