from datetime import datetime
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
                    dls.trade_date,
                    dls.low_bucket_time
                FROM daily_low_summary dls
                JOIN symbols s
                    ON dls.symbol_id = s.symbol_id
                WHERE s.symbol = %s
                ORDER BY dls.trade_date ASC;
                """,
                (SYMBOL,),
            )
            rows = cur.fetchall()

    if not rows:
        print("No summary data found. Chart not created.")
        return {
            "chart_created": False,
            "output_path": None,
            "day_count": 0,
        }

    trade_dates = [row[0] for row in rows]
    bucket_minutes = [(row[1].hour * 60) + row[1].minute for row in rows]

    output_dir = Path("charts")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "daily_low_by_date.png"

    plt.figure(figsize=(14, 6))
    plt.plot(trade_dates, bucket_minutes, marker="o", linewidth=1.5, markersize=4)

    ytick_minutes = list(range(0, 24 * 60, 60))
    ytick_labels = [f"{minutes // 60:02d}:{minutes % 60:02d}" for minutes in ytick_minutes]

    plt.title(f"JILT: Daily Low Bucket by Date ({SYMBOL})")
    plt.xlabel("Trade Date")
    plt.ylabel("Low Bucket Time (Eastern)")
    plt.yticks(ytick_minutes, ytick_labels)
    plt.xticks(rotation=45)
    plt.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

    print(f"Chart saved to: {output_path}")

    return {
        "chart_created": True,
        "output_path": str(output_path),
        "day_count": len(rows),
    }


if __name__ == "__main__":
    main()
