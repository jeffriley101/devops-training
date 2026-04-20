import yfinance as yf
import pandas as pd

from app.config import HISTORY_PERIOD, INTERVAL, MARKET_TIMEZONE, YFINANCE_TICKER


def fetch_raw_history(
    ticker_symbol: str = YFINANCE_TICKER,
    period: str = HISTORY_PERIOD,
    interval: str = INTERVAL,
) -> pd.DataFrame:
    ticker = yf.Ticker(ticker_symbol)
    return ticker.history(period=period, interval=interval)


def normalize_bars(df: pd.DataFrame, market_timezone: str = MARKET_TIMEZONE) -> pd.DataFrame:
    normalized_df = df.reset_index().copy()

    normalized_df["Datetime"] = normalized_df["Datetime"].dt.tz_convert(market_timezone)
    normalized_df["trade_date"] = normalized_df["Datetime"].dt.date

    normalized_df = normalized_df.rename(
        columns={
            "Datetime": "bar_timestamp",
            "Open": "open_value",
            "High": "high_value",
            "Low": "low_value",
            "Close": "close_value",
            "Volume": "volume",
        }
    )[
        [
            "bar_timestamp",
            "trade_date",
            "open_value",
            "high_value",
            "low_value",
            "close_value",
            "volume",
        ]
    ]

    return normalized_df


def fetch_and_normalize_history(
    ticker_symbol: str = YFINANCE_TICKER,
    period: str = HISTORY_PERIOD,
    interval: str = INTERVAL,
    market_timezone: str = MARKET_TIMEZONE,
) -> pd.DataFrame:
    raw_df = fetch_raw_history(
        ticker_symbol=ticker_symbol,
        period=period,
        interval=interval,
    )
    return normalize_bars(raw_df, market_timezone=market_timezone)
