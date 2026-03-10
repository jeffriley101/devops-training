import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


def generate_price_chart(
    csv_path: Path,
    output_path: Path,
) -> None:
    rows_by_symbol: dict[str, list[tuple[str, float]]] = defaultdict(list)

    with csv_path.open("r", newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            symbol = row["symbol"]
            date_utc = row["date_utc"]
            price = float(row["price"])
            rows_by_symbol[symbol].append((date_utc, price))

    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 6))

    for symbol, entries in rows_by_symbol.items():
        entries.sort(key=lambda item: item[0])
        dates = [item[0] for item in entries]
        prices = [item[1] for item in entries]
        plt.plot(dates, prices, marker="o", label=symbol)

    plt.title("Price History")
    plt.xlabel("Date (UTC)")
    plt.ylabel("Price")
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
