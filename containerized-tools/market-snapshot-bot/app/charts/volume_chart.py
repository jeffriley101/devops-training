from pathlib import Path
import csv

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def generate_volume_chart(
    payload: dict[str, object],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 6))

    for symbol_data in payload["symbols"]:
        symbol = symbol_data["symbol"]
        samples = symbol_data["samples"]

        times = [
            sample.get("timestamp_label_et", str(sample["timestamp_et"]))
            for sample in samples
        ]
        volumes = [int(sample["volume"]) for sample in samples]

        plt.plot(times, volumes, marker="o", label=symbol)

    plt.title("Intraday Volume Window")
    plt.xlabel("Timestamp (ET)")
    plt.ylabel("Volume")
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def _time_str_to_minutes(time_str: str) -> int:
    hour, minute = map(int, time_str.split(":"))
    return hour * 60 + minute


def _minutes_to_time_label(value: float, _pos: int) -> str:
    total_minutes = int(round(value))
    hour = total_minutes // 60
    minute = total_minutes % 60
    return f"{hour:02d}:{minute:02d}"


def generate_peak_volume_time_chart(
    daily_root: Path,
    output_path: Path,
) -> None:
    peak_points_by_symbol: dict[str, list[tuple[str, int]]] = {}

    for csv_path in sorted(daily_root.rglob("volume_samples.csv")):
        date_str = "/".join(csv_path.parts[-4:-1])  # YYYY/MM/DD
        date_label = date_str.replace("/", "-")

        rows_by_symbol: dict[str, list[dict[str, str]]] = {}

        with csv_path.open("r", newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                symbol = row["symbol"]
                rows_by_symbol.setdefault(symbol, []).append(row)

        for symbol, rows in rows_by_symbol.items():
            if symbol != "QQQ":
                continue

            if not rows:
                continue

            peak_row = max(rows, key=lambda row: int(row["volume"]))
            peak_time = peak_row["timestamp_et"]
            peak_minutes = _time_str_to_minutes(peak_time)

            peak_points_by_symbol.setdefault(symbol, []).append((date_label, peak_minutes))

    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 6))
    plotted = False

    for symbol, entries in peak_points_by_symbol.items():
        entries.sort(key=lambda item: item[0])
        dates = [item[0] for item in entries]
        peak_times = [item[1] for item in entries]

        plt.plot(dates, peak_times, marker="o", label=symbol)
        plotted = True

    plt.title("Daily Peak Volume Time")
    plt.xlabel("Date")
    plt.ylabel("Peak Volume Time (ET)")
    plt.xticks(rotation=45)
    plt.gca().yaxis.set_major_formatter(FuncFormatter(_minutes_to_time_label))
    if plotted:
        plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

