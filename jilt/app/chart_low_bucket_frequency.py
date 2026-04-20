from pathlib import Path

import matplotlib.pyplot as plt

from app.config import MARKET_TIMEZONE, SYMBOL
from app.db import get_connection


def main() -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET TIME ZONE '{MARKET_TIMEZONE}';")
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
                ORDER BY dls.low_bucket_time ASC;
                """,
                (SYMBOL,),
            )
            rows = cur.fetchall()

    if not rows:
        print("No summary data found. Chart not created.")
        return {
            "chart_created": False,
            "output_path": None,
            "bucket_count": 0,
        }

    bucket_labels = [str(row[0]) for row in rows]
    counts = [row[1] for row in rows]

    output_dir = Path("charts")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "low_bucket_frequency.png"

    plt.figure(figsize=(12, 6))
    plt.bar(bucket_labels, counts)
    plt.title(f"JILT: Daily Low Bucket Frequency ({SYMBOL})")
    plt.xlabel("5-Minute Bucket Time (Eastern)")
    plt.ylabel("Number of Days")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

    print(f"Chart saved to: {output_path}")

    return {
        "chart_created": True,
        "output_path": str(output_path),
        "bucket_count": len(rows),
    }


if __name__ == "__main__":
    main()
