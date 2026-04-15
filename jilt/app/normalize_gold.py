import yfinance as yf


def main() -> None:
    ticker = yf.Ticker("GC=F")
    df = ticker.history(period="5d", interval="5m")

    df = df.reset_index()
    df["Datetime"] = df["Datetime"].dt.tz_convert("America/New_York")
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
            "Volume",
        ]
    ]

    print(normalized.head())
    print()
    print(normalized.dtypes)
    print()
    print(f"row_count={len(normalized)}")


if __name__ == "__main__":
    main()
