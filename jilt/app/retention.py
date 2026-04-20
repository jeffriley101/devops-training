from app.config import RAW_RETENTION_DAYS
from app.db import get_connection


def main() -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM intraday_bars
                WHERE trade_date < CURRENT_DATE - (%s * INTERVAL '1 day');
                """,
                (RAW_RETENTION_DAYS,),
            )
            deleted_rows = cur.rowcount

    print(f"Raw retention applied. Rows deleted: {deleted_rows}")

    return {
        "deleted_rows": deleted_rows,
    }


if __name__ == "__main__":
    main()
