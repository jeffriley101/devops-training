from app.market_data import fetch_and_normalize_history


def main() -> None:
    df = fetch_and_normalize_history()

    print(df.head())
    print()
    print(df.dtypes)
    print()
    print(f"row_count={len(df)}")


if __name__ == "__main__":
    main()
