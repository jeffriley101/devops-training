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
                    EXTRACT(HOUR FROM dls.low_bucket_time)::int AS hour_bucket,
                    COUNT(*) AS day_count
                FROM daily_low_summary dls
                JOIN symbols s
                    ON dls.symbol_id = s.symbol_id
                WHERE s.symbol = %s
                GROUP BY hour_bucket
                ORDER BY hour_bucket ASC;
                """,
                (SYMBOL,),
            )
            rows = cur.fetchall()

    if not rows:
        print("No summary data found. Chart not created.")
        return {
            "chart_created": False,
            "output_path": None,
            "hour_count": 0,
        }

    hours = [row[0] for row in rows]
    counts = [row[1] for row in rows]

    output_dir = Path("charts")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "daily_low_hour_heatmap.png"

    plt.figure(figsize=(12, 2.8))
    plt.imshow([counts], aspect="auto")
    plt.title(f"JILT: Daily Low Hour Heatmap ({SYMBOL})")
    plt.xlabel("Hour of Day (Eastern)")
    plt.yticks([])
    plt.xticks(range(len(hours)), [f"{hour:02d}:00" for hour in hours])

    for index, count in enumerate(counts):
        plt.text(index, 0, str(count), ha="center", va="center")

    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

    print(f"Chart saved to: {output_path}")

    return {
        "chart_created": True,
        "output_path": str(output_path),
        "hour_count": len(rows),
    }


if __name__ == "__main__":
    main()
