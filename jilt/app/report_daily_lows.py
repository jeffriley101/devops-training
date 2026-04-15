import yfinance as yf

from app.config import HISTORY_PERIOD, INTERVAL, MARKET_TIMEZONE, SYMBOL, YFINANCE_TICKER
from app.db import get_connection


def main() -> None:
    ticker = yf.Ticker(YFINANCE_TICKER)
    df = ticker.history(period=HISTORY_PERIOD, interval=INTERVAL)

    df = df.reset_index()
    df["Datetime"] = df["Datetime"].dt.tz_convert(MARKET_TIMEZONE)
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

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol_id
                FROM symbols
                WHERE symbol = %s;
                """,
                (SYMBOL,),
            )
            result = cur.fetchone()

            if result is None:
                raise ValueError(f"Symbol {SYMBOL} not found in symbols table.")

            symbol_id = result[0]

            inserted_attempts = 0

            for row in normalized.itertuples(index=False):
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
                inserted_attempts += 1

    print(f"Ingest completed. Rows processed: {inserted_attempts}")


if __name__ == "__main__":
    main()
