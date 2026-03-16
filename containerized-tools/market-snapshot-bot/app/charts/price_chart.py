import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt

EASTERN_TZ = ZoneInfo("America/New_York")


def _parse_utc_timestamp(timestamp_str: str) -> datetime:
    return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))


def _format_et_label(timestamp_str: str) -> str:
    dt_utc = _parse_utc_timestamp(timestamp_str)
    dt_et = dt_utc.astimezone(EASTERN_TZ)
    return dt_et.strftime("%Y-%m-%d %I:%M %p")


def _load_history_rows(csv_path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    with csv_path.open("r", newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            rows.append(row)

    return rows


def generate_price_chart_all_runs(
    csv_path: Path,
    output_path: Path,
) -> None:
    rows_by_symbol: dict[str, list[tuple[datetime, str, float]]] = defaultdict(list)

    for row in _load_history_rows(csv_path):
        if row["status"] != "success":
            continue

        symbol = row["symbol"]
        collected_at_utc = row["collected_at_utc"]
        dt_utc = _parse_utc_timestamp(collected_at_utc)
        label = _format_et_label(collected_at_utc)
        price = float(row["price"])

        rows_by_symbol[symbol].append((dt_utc, label, price))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(12, 6))

    plotted = False

    for symbol, entries in rows_by_symbol.items():
        entries.sort(key=lambda item: item[0])
        labels = [item[1] for item in entries]
        prices = [item[2] for item in entries]
        plt.plot(labels, prices, marker="o", label=symbol)
        plotted = True

    plt.title("Price History - All Runs")
    plt.xlabel("Run Timestamp (ET)")
    plt.ylabel("Price")
    plt.xticks(rotation=45, ha="right")
    if plotted:
        plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def generate_price_chart_official_daily(
    csv_path: Path,
    output_path: Path,
) -> None:
    latest_by_symbol_and_date: dict[str, dict[str, tuple[datetime, float]]] = defaultdict(dict)

    for row in _load_history_rows(csv_path):
        if row["status"] != "success":
            continue
        if row.get("source") != "real":
            continue
        if row.get("run_context") != "scheduled":
            continue

        symbol = row["symbol"]
        date_utc = row["date_utc"]
        collected_at_utc = row["collected_at_utc"]
        dt_utc = _parse_utc_timestamp(collected_at_utc)
        price = float(row["price"])

        current = latest_by_symbol_and_date[symbol].get(date_utc)
        if current is None or dt_utc > current[0]:
            latest_by_symbol_and_date[symbol][date_utc] = (dt_utc, price)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 6))

    plotted = False

    for symbol, entries_by_date in latest_by_symbol_and_date.items():
        entries = sorted(entries_by_date.items(), key=lambda item: item[0])
        dates = [item[0] for item in entries]
        prices = [item[1][1] for item in entries]
        plt.plot(dates, prices, marker="o", label=symbol)
        plotted = True

    plt.title("Price History - Official Daily Scheduled Runs")
    plt.xlabel("Date (UTC)")
    plt.ylabel("Price")
    plt.xticks(rotation=45, ha="right")
    if plotted:
        plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

