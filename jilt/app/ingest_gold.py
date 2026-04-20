from app.config import SYMBOL
from app.db import get_connection
from app.market_data import fetch_and_normalize_history


def main() -> dict:
    normalized = fetch_and_normalize_history()

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

            inserted_rows = 0

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
                if cur.rowcount == 1:
                    inserted_rows += 1

    processed_rows = len(normalized)

    print(
        f"Ingest completed. Rows processed: {processed_rows}, "
        f"rows inserted: {inserted_rows}"
    )

    return {
        "processed_rows": processed_rows,
        "inserted_rows": inserted_rows,
    }


if __name__ == "__main__":
    main()
