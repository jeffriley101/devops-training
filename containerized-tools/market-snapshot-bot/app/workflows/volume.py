import csv
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from app.charts.volume_chart import generate_volume_chart


WINDOW_START_ET = time(10, 8)
WINDOW_END_ET = time(10, 40)
EASTERN_TZ = ZoneInfo("America/New_York")


def is_weekday_in_eastern(now_et: datetime) -> bool:
    return now_et.weekday() < 5


def is_within_volume_window(now_et: datetime) -> bool:
    current_time = now_et.time()
    return WINDOW_START_ET <= current_time <= WINDOW_END_ET


def validate_volume_run_window(allow_outside_window: bool) -> None:
    if allow_outside_window:
        return

    now_et = datetime.now(EASTERN_TZ)

    if not is_weekday_in_eastern(now_et):
        raise RuntimeError(
            "Volume workflow is only allowed Monday through Friday in Eastern Time. "
            "Use --allow-outside-window for development."
        )

    if not is_within_volume_window(now_et):
        raise RuntimeError(
            "Volume workflow is only allowed between 10:08 ET and 10:40 ET. "
            "Use --allow-outside-window for development."
        )


def build_mock_volume_samples() -> dict[str, list[dict[str, int | str]]]:
    times = [
        "10:08", "10:09", "10:10", "10:11", "10:12", "10:13", "10:14", "10:15",
        "10:16", "10:17", "10:18", "10:19", "10:20", "10:21", "10:22", "10:23",
        "10:24", "10:25", "10:26", "10:27", "10:28", "10:29", "10:30", "10:31",
        "10:32", "10:33", "10:34", "10:35", "10:36", "10:37", "10:38", "10:39",
        "10:40",
    ]

    xag_volumes = [
        1180, 1215, 1240, 1285, 1310, 1360, 1425, 1490, 1560, 1635, 1710,
        1680, 1605, 1540, 1495, 1450, 1400, 1380, 1410, 1460, 1525, 1595,
        1660, 1735, 1810, 1765, 1685, 1600, 1520, 1455, 1385, 1305, 1215,
    ]

    wti_volumes = [
        2010, 2055, 2105, 2160, 2245, 2335, 2480, 2575, 2665, 2750, 2890,
        2815, 2725, 2650, 2580, 2495, 2410, 2360, 2395, 2460, 2555, 2675,
        2790, 2910, 3040, 2965, 2855, 2740, 2625, 2495, 2365, 2215, 2055,
    ]

    return {
        "XAG/USD": [
            {"timestamp_et": time_value, "volume": volume_value}
            for time_value, volume_value in zip(times, xag_volumes)
        ],
        "WTI/USD": [
            {"timestamp_et": time_value, "volume": volume_value}
            for time_value, volume_value in zip(times, wti_volumes)
        ],
    }


def summarize_symbol(
    symbol: str,
    samples: list[dict[str, int | str]],
) -> dict[str, object]:
    sample_count = len(samples)
    volumes = [int(sample["volume"]) for sample in samples]
    max_volume = max(volumes)
    avg_volume = round(sum(volumes) / sample_count)
    peak_sample = max(samples, key=lambda sample: int(sample["volume"]))

    return {
        "symbol": symbol,
        "sample_count": sample_count,
        "max_volume": max_volume,
        "avg_volume": avg_volume,
        "peak_timestamp_et": str(peak_sample["timestamp_et"]),
        "status": "success",
        "samples": samples,
    }


def build_volume_payload() -> dict[str, object]:
    timestamp = datetime.utcnow()
    collected_at_utc = timestamp.replace(microsecond=0).isoformat() + "Z"

    raw_samples = build_mock_volume_samples()
    symbol_summaries = [
        summarize_symbol(symbol, samples)
        for symbol, samples in raw_samples.items()
    ]

    return {
        "workflow": "volume",
        "window_start_et": "10:08",
        "window_end_et": "10:40",
        "collected_at_utc": collected_at_utc,
        "overall_status": "success",
        "symbols": symbol_summaries,
    }


def write_json_artifact(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_daily_csv(payload: dict[str, object], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["symbol", "timestamp_et", "volume"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for symbol_data in payload["symbols"]:
            for sample in symbol_data["samples"]:
                writer.writerow(
                    {
                        "symbol": symbol_data["symbol"],
                        "timestamp_et": sample["timestamp_et"],
                        "volume": sample["volume"],
                    }
                )


def print_human_summary(payload: dict[str, object]) -> None:
    print("Volume Window Summary [success]")
    print(
        f"Window: {payload['window_start_et']} ET - "
        f"{payload['window_end_et']} ET"
    )
    print("")

    for symbol_data in payload["symbols"]:
        print(
            f"Symbol: {symbol_data['symbol']} | "
            f"samples={symbol_data['sample_count']} | "
            f"max_volume={symbol_data['max_volume']} | "
            f"avg_volume={symbol_data['avg_volume']} | "
            f"peak={symbol_data['peak_timestamp_et']} ET | "
            f"status={symbol_data['status']}"
        )


def run_volume_workflow(allow_outside_window: bool = False) -> None:
    validate_volume_run_window(allow_outside_window)

    timestamp = datetime.utcnow()
    payload = build_volume_payload()

    base_path = Path("dev/volume/daily") / timestamp.strftime("%Y/%m/%d")

    json_path = base_path / "volume_samples.json"
    csv_path = base_path / "volume_samples.csv"
    chart_path = (
        Path("dev/volume/charts")
        / timestamp.strftime("%Y/%m/%d")
        / "volume_window.png"
    )

    write_json_artifact(json_path, payload)
    write_daily_csv(payload, csv_path)
    generate_volume_chart(payload, chart_path)

    print_human_summary(payload)
    print("")
    print(f"Volume JSON written to: {json_path}")
    print(f"Volume CSV written to: {csv_path}")
    print(f"Volume chart updated: {chart_path}")
