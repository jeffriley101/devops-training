from contextlib import contextmanager
from datetime import date, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from app.buckets import load_chaperone_map
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


def bucket_choice_to_index(bucket_choice: str) -> int | None:
    try:
        hour_text, minute_text = bucket_choice.split(":")
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError):
        return None

    if not (0 <= hour <= 23):
        return None

    if minute not in {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}:
        return None

    return (hour * 60 + minute) // 5


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


def count_bucket_guesses(
    *,
    symbol_code: str,
    game_date_et: date,
    bucket_choice: str,
) -> int:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return 0

    query = """
        SELECT COUNT(*) AS bucket_guess_count
        FROM jilt_game_daily_guesses
        WHERE symbol_id = %s
          AND game_date_et = %s
          AND bucket_choice = %s
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_id, game_date_et, bucket_choice))
            row = cur.fetchone()

    return int(row["bucket_guess_count"] or 0)


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


def list_hall_of_famers(
    *,
    symbol_code: str,
    limit: int = 100,
) -> list[dict]:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return []

    query = """
        SELECT
            r.game_date_et,
            g.guess_id,
            g.nickname,
            g.bucket_choice,
            g.submitted_at
        FROM jilt_game_daily_results r
        JOIN jilt_game_daily_guesses g
          ON g.symbol_id = r.symbol_id
         AND g.game_date_et = r.game_date_et
         AND g.bucket_choice = r.winning_bucket
        WHERE r.symbol_id = %s
        ORDER BY r.game_date_et DESC, g.submitted_at ASC, g.guess_id ASC
        LIMIT %s
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_id, limit))
            rows = cur.fetchall()

    return [dict(row) for row in rows]


def _build_closest_rows_for_game_date(
    *,
    game_date_et: date,
    winning_bucket: str,
    guesses: list[dict],
) -> list[dict]:
    winning_index = bucket_choice_to_index(winning_bucket)
    if winning_index is None:
        return []

    chaperone_map = load_chaperone_map()
    candidate_rows: list[dict] = []

    for guess in guesses:
        guess_bucket = str(guess["bucket_choice"])
        guess_index = bucket_choice_to_index(guess_bucket)
        if guess_index is None:
            continue

        distance_buckets = abs(guess_index - winning_index)

        candidate_rows.append(
            {
                "game_date_et": game_date_et,
                "guess_id": guess["guess_id"],
                "nickname": guess["nickname"],
                "bucket_choice": guess_bucket,
                "submitted_at": guess["submitted_at"],
                "distance_buckets": distance_buckets,
                "chaperone": chaperone_map.get(guess_bucket, ""),
            }
        )

    if not candidate_rows:
        return []

    min_distance = min(row["distance_buckets"] for row in candidate_rows)

    closest_rows = [
        row
        for row in candidate_rows
        if row["distance_buckets"] == min_distance
    ]

    closest_rows.sort(
        key=lambda row: (row["submitted_at"], row["guess_id"])
    )

    return closest_rows



def get_daily_result_outcome(
    *,
    symbol_code: str,
    game_date_et: date,
) -> dict | None:
    result = get_daily_result(symbol_code=symbol_code, game_date_et=game_date_et)
    if result is None:
        return None

    winning_bucket = str(result["winning_bucket"])
    exact_winners = list_winning_guesses(
        symbol_code=symbol_code,
        game_date_et=game_date_et,
        winning_bucket=winning_bucket,
    )
    all_guesses = list_daily_guesses(
        symbol_code=symbol_code,
        game_date_et=game_date_et,
    )
    closest_winners = _build_closest_rows_for_game_date(
        game_date_et=game_date_et,
        winning_bucket=winning_bucket,
        guesses=all_guesses,
    )

    return {
        "game_date_et": game_date_et,
        "winning_bucket": winning_bucket,
        "has_winners": len(exact_winners) > 0,
        "winner_count": len(exact_winners),
        "winners": exact_winners,
        "closest_winners": closest_winners,
        "closest_winner_count": len(closest_winners),
    }


def list_closest_winners(
    *,
    symbol_code: str,
    limit: int = 100,
) -> list[dict]:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return []

    results_query = """
        SELECT
            game_date_et,
            winning_bucket
        FROM jilt_game_daily_results
        WHERE symbol_id = %s
        ORDER BY game_date_et DESC
        LIMIT %s
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(results_query, (symbol_id, limit))
            result_rows = cur.fetchall()

    closest_rows: list[dict] = []

    for result_row in result_rows:
        game_date_et = result_row["game_date_et"]
        winning_bucket = result_row["winning_bucket"]

        guesses = list_daily_guesses(
            symbol_code=symbol_code,
            game_date_et=game_date_et,
        )
        daily_closest = _build_closest_rows_for_game_date(
            game_date_et=game_date_et,
            winning_bucket=winning_bucket,
            guesses=guesses,
        )
        closest_rows.extend(daily_closest)

    closest_rows.sort(
        key=lambda row: (row["game_date_et"], row["submitted_at"], row["guess_id"]),
        reverse=True,
    )

    return closest_rows[:limit]


def get_guess_for_nickname_and_date(
    *,
    symbol_code: str,
    game_date_et: date,
    nickname: str,
) -> dict | None:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        return None

    query = """
        SELECT
            guess_id,
            nickname,
            bucket_choice,
            submitted_at
        FROM jilt_game_daily_guesses
        WHERE symbol_id = %s
          AND game_date_et = %s
          AND nickname = %s
        LIMIT 1
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_id, game_date_et, nickname))
            row = cur.fetchone()

    if row is None:
        return None

    return dict(row)


def insert_two_day_guess(
    *,
    symbol_code: str,
    game_date_et: date,
    nickname: str,
    bucket_choice: str,
) -> tuple[date, date]:
    symbol_id = get_symbol_id(symbol_code)
    if symbol_id is None:
        raise ValueError(f"Unknown or inactive symbol: {symbol_code}")

    tomorrow_et = game_date_et + timedelta(days=1)

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
            cur.execute(query, (symbol_id, game_date_et, nickname, bucket_choice))
            cur.execute(query, (symbol_id, tomorrow_et, nickname, bucket_choice))
        conn.commit()

    return game_date_et, tomorrow_et


