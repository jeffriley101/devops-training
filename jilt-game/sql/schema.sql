CREATE TABLE IF NOT EXISTS jilt_game_symbols (
    symbol_id BIGSERIAL PRIMARY KEY,
    symbol_code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS jilt_game_daily_guesses (
    guess_id BIGSERIAL PRIMARY KEY,
    symbol_id BIGINT NOT NULL REFERENCES jilt_game_symbols(symbol_id),
    game_date_et DATE NOT NULL,
    nickname TEXT NOT NULL,
    bucket_choice CHAR(5) NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT jilt_game_daily_guesses_bucket_format_chk
        CHECK (bucket_choice ~ '^[0-2][0-9]:[0-5][0-9]$')
);

CREATE UNIQUE INDEX IF NOT EXISTS jilt_game_daily_guesses_one_guess_per_nickname_day
    ON jilt_game_daily_guesses (symbol_id, game_date_et, nickname);

CREATE TABLE IF NOT EXISTS jilt_game_daily_results (
    result_id BIGSERIAL PRIMARY KEY,
    symbol_id BIGINT NOT NULL REFERENCES jilt_game_symbols(symbol_id),
    game_date_et DATE NOT NULL,
    winning_bucket CHAR(5) NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL,
    source_name TEXT NOT NULL,
    source_version TEXT NOT NULL,
    CONSTRAINT jilt_game_daily_results_symbol_date_uniq
        UNIQUE (symbol_id, game_date_et),
    CONSTRAINT jilt_game_daily_results_bucket_format_chk
        CHECK (winning_bucket ~ '^[0-2][0-9]:[0-5][0-9]$')
);

INSERT INTO jilt_game_symbols (symbol_code, display_name)
VALUES ('GOLD', 'Gold')
ON CONFLICT (symbol_code) DO NOTHING;
