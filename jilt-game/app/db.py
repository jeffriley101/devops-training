from contextlib import contextmanager
from datetime import date, datetime

import psycopg
from psycopg.rows import dict_row

from app.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER


def build_dsn() -> str:
    return (
        f"host={DB_HOST} "
        f"port={DB_PORT} "
        f"dbname={DB_NAME} "
        f"user={DB_USER} "
        f"password={DB_PASSWORD}"
    )


@contextmanager
def get_connection():
    conn = psycopg.connect(build_dsn(), row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def get_symbol_id(symbol_code: str) -> int | None:
    query = """
        SELECT symbol_id
        FROM jilt_game_symbols
        WHERE symbol_code = %s
          AND is_active = TRUE
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_code,))
            row = cur.fetchone()

    if row is None:
        return None

    return int(row["symbol_id"])


def insert_daily_guess(
    *,
    symbol_code: str,
    game_date_et: date,
    nickname: str,
    bucket_choice: str,
) -> None:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        raise ValueError(f"Unknown or inactive symbol: {symbol_code}")

    query = """
        INSERT INTO jilt_game_daily_guesses (
            symbol_id,
            game_date_et,
            nickname,
            bucket_choice
        )
        VALUES (%s, %s, %s, %s)
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (symbol_id, game_date_et, nickname, bucket_choice),
            )
        conn.commit()


def list_daily_guesses(
    *,
    symbol_code: str,
    game_date_et: date,
) -> list[dict]:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return []

    query = """
        SELECT
            guess_id,
            nickname,
            bucket_choice,
            submitted_at
        FROM jilt_game_daily_guesses
        WHERE symbol_id = %s
          AND game_date_et = %s
        ORDER BY submitted_at DESC, guess_id DESC
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_id, game_date_et))
            rows = cur.fetchall()

    return list(rows)


def list_winning_guesses(
    *,
    symbol_code: str,
    game_date_et: date,
    winning_bucket: str,
) -> list[dict]:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return []

    query = """
        SELECT
            guess_id,
            nickname,
            bucket_choice,
            submitted_at
        FROM jilt_game_daily_guesses
        WHERE symbol_id = %s
          AND game_date_et = %s
          AND bucket_choice = %s
        ORDER BY submitted_at ASC, guess_id ASC
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_id, game_date_et, winning_bucket))
            rows = cur.fetchall()

    return list(rows)


def upsert_daily_result(
    *,
    symbol_code: str,
    game_date_et: str,
    winning_bucket: str,
    resolved_at: str,
    source_name: str,
    source_version: str,
) -> None:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        raise ValueError(f"Unknown or inactive symbol: {symbol_code}")

    resolved_at_dt = datetime.fromisoformat(resolved_at)

    query = """
        INSERT INTO jilt_game_daily_results (
            symbol_id,
            game_date_et,
            winning_bucket,
            resolved_at,
            source_name,
            source_version
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol_id, game_date_et)
        DO UPDATE SET
            winning_bucket = EXCLUDED.winning_bucket,
            resolved_at = EXCLUDED.resolved_at,
            source_name = EXCLUDED.source_name,
            source_version = EXCLUDED.source_version
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    symbol_id,
                    game_date_et,
                    winning_bucket,
                    resolved_at_dt,
                    source_name,
                    source_version,
                ),
            )
        conn.commit()


def get_latest_daily_result(
    symbol_code: str,
    *,
    before_game_date_et: date | None = None,
) -> dict | None:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return None

    if before_game_date_et is None:
        query = """
            SELECT
                r.game_date_et,
                s.symbol_code AS symbol,
                r.winning_bucket,
                r.resolved_at,
                r.source_name,
                r.source_version
            FROM jilt_game_daily_results r
            JOIN jilt_game_symbols s
              ON s.symbol_id = r.symbol_id
            WHERE r.symbol_id = %s
            ORDER BY r.game_date_et DESC, r.resolved_at DESC
            LIMIT 1
        """
        params = (symbol_id,)
    else:
        query = """
            SELECT
                r.game_date_et,
                s.symbol_code AS symbol,
                r.winning_bucket,
                r.resolved_at,
                r.source_name,
                r.source_version
            FROM jilt_game_daily_results r
            JOIN jilt_game_symbols s
              ON s.symbol_id = r.symbol_id
            WHERE r.symbol_id = %s
              AND r.game_date_et < %s
            ORDER BY r.game_date_et DESC, r.resolved_at DESC
            LIMIT 1
        """
        params = (symbol_id, before_game_date_et)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()

    if row is None:
        return None

    return dict(row)

def list_recent_daily_results(
    *,
    symbol_code: str,
    limit: int = 30,
) -> list[dict]:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return []

    query = """
        SELECT
            r.result_id,
            r.game_date_et,
            s.symbol_code AS symbol,
            r.winning_bucket,
            r.resolved_at,
            r.source_name,
            r.source_version,
            COUNT(g.guess_id) FILTER (
                WHERE g.bucket_choice = r.winning_bucket
            ) AS winner_count
        FROM jilt_game_daily_results r
        JOIN jilt_game_symbols s
          ON s.symbol_id = r.symbol_id
        LEFT JOIN jilt_game_daily_guesses g
          ON g.symbol_id = r.symbol_id
         AND g.game_date_et = r.game_date_et
        WHERE r.symbol_id = %s
        GROUP BY
            r.result_id,
            r.game_date_et,
            s.symbol_code,
            r.winning_bucket,
            r.resolved_at,
            r.source_name,
            r.source_version
        ORDER BY r.game_date_et DESC, r.resolved_at DESC
        LIMIT %s
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_id, limit))
            rows = cur.fetchall()

    return list(rows)


def get_daily_result(
    *,
    symbol_code: str,
    game_date_et: date,
) -> dict | None:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return None

    query = """
        SELECT
            r.game_date_et,
            s.symbol_code AS symbol,
            r.winning_bucket,
            r.resolved_at,
            r.source_name,
            r.source_version
        FROM jilt_game_daily_results r
        JOIN jilt_game_symbols s
          ON s.symbol_id = r.symbol_id
        WHERE r.symbol_id = %s
          AND r.game_date_et = %s
        LIMIT 1
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_id, game_date_et))
            row = cur.fetchone()

    if row is None:
        return None

    return dict(row)
