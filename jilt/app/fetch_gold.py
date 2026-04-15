import yfinance as yf


def main() -> None:
    ticker = yf.Ticker("GC=F")
    df = ticker.history(period="5d", interval="5m")

    print(df.head())
    print()
    print(df.tail())
    print()
    print(df.columns)
    print(f"row_count={len(df)}")


if __name__ == "__main__":
    main()
