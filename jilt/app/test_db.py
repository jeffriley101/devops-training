from app.db import get_connection


def main() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol_id, symbol, display_name, asset_type, is_active
                FROM symbols
                ORDER BY symbol_id;
                """
            )
            rows = cur.fetchall()

    print("Connected successfully.")
    print("Symbols table rows:")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
