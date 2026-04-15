import yfinance as yf

from app.db import get_connection


def main() -> None:
    ticker = yf.Ticker("GC=F")
    df = ticker.history(period="5d", interval="5m")

    df = df.reset_index()
    df["Datetime"] = df["Datetime"].dt.tz_convert("America/New_York")
    df["trade_date"] = df["Datetime"].dt.date

    normalized = df.rename(
        columns={
            "Datetime": "bar_timestamp",
            "Open": "open_value",
            "High": "high_value",
            "Low": "low_value",
            "Close": "close_value",
        }
    )[
        [
            "bar_timestamp",
            "trade_date",
            "open_value",
            "high_value",
            "low_value",
            "close_value",
        ]
    ]

    sample_rows = normalized.head(5)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol_id
                FROM symbols
                WHERE symbol = %s;
                """,
                ("GC",),
            )
            result = cur.fetchone()

            if result is None:
                raise ValueError("Symbol GC not found in symbols table.")

            symbol_id = result[0]

            for row in sample_rows.itertuples(index=False):
                cur.execute(
                    """
                    INSERT INTO intraday_bars (
                        symbol_id,
                        bar_timestamp,
                        trade_date,
                        open_value,
                        high_value,
                        low_value,
                        close_value,
                        source_name
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol_id, bar_timestamp) DO NOTHING;
                    """,
                    (
                        symbol_id,
                        row.bar_timestamp,
                        row.trade_date,
                        row.open_value,
                        row.high_value,
                        row.low_value,
                        row.close_value,
                        "yfinance",
                    ),
                )

    print("Inserted sample rows successfully.")


if __name__ == "__main__":
    main()
