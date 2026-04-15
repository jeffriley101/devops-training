BEGIN;

CREATE TABLE IF NOT EXISTS symbols (
    symbol_id      BIGSERIAL PRIMARY KEY,
    symbol         TEXT NOT NULL UNIQUE,
    display_name   TEXT NOT NULL,
    asset_type     TEXT NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS intraday_bars (
    bar_id          BIGSERIAL PRIMARY KEY,
    symbol_id       BIGINT NOT NULL REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    bar_timestamp   TIMESTAMPTZ NOT NULL,
    trade_date      DATE NOT NULL,
    open_value      NUMERIC(18,6) NOT NULL,
    high_value      NUMERIC(18,6) NOT NULL,
    low_value       NUMERIC(18,6) NOT NULL,
    close_value     NUMERIC(18,6) NOT NULL,
    source_name     TEXT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT intraday_bars_price_order_chk
        CHECK (
            low_value <= open_value
            AND low_value <= close_value
            AND low_value <= high_value
            AND high_value >= open_value
            AND high_value >= close_value
        ),

    CONSTRAINT intraday_bars_unique_bar
        UNIQUE (symbol_id, bar_timestamp)
);

CREATE TABLE IF NOT EXISTS daily_low_summary (
    summary_id        BIGSERIAL PRIMARY KEY,
    symbol_id         BIGINT NOT NULL REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    trade_date        DATE NOT NULL,
    lowest_price      NUMERIC(18,6) NOT NULL,
    low_bucket_time   TIME NOT NULL,
    bar_timestamp     TIMESTAMPTZ NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT daily_low_summary_unique_day
        UNIQUE (symbol_id, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_intraday_bars_symbol_trade_date
    ON intraday_bars (symbol_id, trade_date);

CREATE INDEX IF NOT EXISTS idx_intraday_bars_symbol_timestamp
    ON intraday_bars (symbol_id, bar_timestamp);

CREATE INDEX IF NOT EXISTS idx_daily_low_summary_symbol_trade_date
    ON daily_low_summary (symbol_id, trade_date);

COMMIT;
