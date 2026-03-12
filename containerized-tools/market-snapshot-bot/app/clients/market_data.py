from dataclasses import dataclass

import yfinance as yf


class MarketDataError(Exception):
    pass


@dataclass(frozen=True)
class QuoteResult:
    symbol: str
    price: float
    currency: str
    as_of_utc: str
    source: str
    source_symbol: str


def fetch_yahoo_intraday_volume_series(
    symbol: str,
    period: str = "1d",
    interval: str = "1m",
) -> list[dict[str, int | str]]:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=False, prepost=False)

    if df.empty:
        raise MarketDataError(f"No Yahoo Finance intraday data returned for {symbol}")

    rows: list[dict[str, int | str]] = []

    for idx, row in df.iterrows():
        ts = idx.tz_convert("America/New_York") if getattr(idx, "tzinfo", None) else idx
        rows.append(
            {
                "timestamp_et": ts.strftime("%H:%M"),
                "volume": int(row["Volume"]),
            }
        )

    return rows

def fetch_alpha_vantage_global_quote(symbol: str) -> QuoteResult:
    raise MarketDataError(
        "fetch_alpha_vantage_global_quote is temporarily disabled during provider transition"
    )

def fetch_yahoo_last_price(symbol: str) -> QuoteResult:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="1d", interval="1m", auto_adjust=False, prepost=False)

    if df.empty:
        raise MarketDataError(f"No Yahoo Finance price data returned for {symbol}")

    last_idx = df.index[-1]
    last_row = df.iloc[-1]

    if getattr(last_idx, "tzinfo", None):
        as_of_utc = last_idx.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        as_of_utc = last_idx.strftime("%Y-%m-%dT%H:%M:%SZ")

    return QuoteResult(
        symbol=symbol,
        price=float(last_row["Close"]),
        currency="USD",
        as_of_utc=as_of_utc,
        source="yahoo_finance",
        source_symbol=symbol,
    )
