from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CHAPERONE_FILE = BASE_DIR / "data" / "bucket_chaperones.json"


def generate_all_buckets() -> list[dict]:
    buckets: list[dict] = []
    index = 0

    for hour in range(24):
        for minute in range(0, 60, 5):
            bucket_time = f"{hour:02d}:{minute:02d}"
            buckets.append(
                {
                    "bucket_time": bucket_time,
                    "bucket_index": index,
                    "hour": hour,
                    "minute": minute,
                    "chaperone": "",
                    "heatmap_color": "",
                }
            )
            index += 1

    return buckets


def is_valid_bucket_time(bucket_time: str) -> bool:
    if len(bucket_time) != 5 or bucket_time[2] != ":":
        return False

    hour_text, minute_text = bucket_time.split(":", 1)

    if not (hour_text.isdigit() and minute_text.isdigit()):
        return False

    hour = int(hour_text)
    minute = int(minute_text)

    return 0 <= hour <= 23 and minute in {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}


def load_chaperone_map() -> dict[str, str]:
    if not CHAPERONE_FILE.exists():
        return {}

    with CHAPERONE_FILE.open("r", encoding="utf-8") as f:
        raw_items = json.load(f)

    chaperone_map: dict[str, str] = {}

    for item in raw_items:
        bucket_time = str(item.get("bucket_time", "")).strip()
        chaperone = str(item.get("chaperone", "")).strip()

        if bucket_time and is_valid_bucket_time(bucket_time):
            if bucket_time not in chaperone_map and chaperone:
                chaperone_map[bucket_time] = chaperone

    return chaperone_map


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def interpolate_color(
    start_hex: str,
    end_hex: str,
    fraction: float,
) -> str:
    fraction = max(0.0, min(1.0, fraction))
    start = hex_to_rgb(start_hex)
    end = hex_to_rgb(end_hex)

    mixed = tuple(
        round(start[i] + (end[i] - start[i]) * fraction)
        for i in range(3)
    )
    return rgb_to_hex(mixed)


def build_heatmap_color_map(
    frequency_map: dict[str, int],
) -> dict[str, str]:
    all_buckets = generate_all_buckets()
    all_bucket_times = [bucket["bucket_time"] for bucket in all_buckets]

    counts = [frequency_map.get(bucket_time, 0) for bucket_time in all_bucket_times]
    min_count = min(counts) if counts else 0
    max_count = max(counts) if counts else 0

    cool_color = "#e5e7eb"
    hot_color = "#2563eb"

    color_map: dict[str, str] = {}

    for bucket_time in all_bucket_times:
        count = frequency_map.get(bucket_time, 0)

        if max_count == min_count:
            fraction = 0.0
        else:
            fraction = (count - min_count) / (max_count - min_count)

        color_map[bucket_time] = interpolate_color(cool_color, hot_color, fraction)

    return color_map


def get_bucket_definitions(
    *,
    frequency_map: dict[str, int] | None = None,
) -> list[dict]:
    buckets = generate_all_buckets()
    chaperone_map = load_chaperone_map()
    heatmap_map = build_heatmap_color_map(frequency_map or {})

    for bucket in buckets:
        bucket["chaperone"] = chaperone_map.get(bucket["bucket_time"], "")
        bucket["heatmap_color"] = heatmap_map.get(bucket["bucket_time"], "")

    return buckets


def get_bucket_choices() -> list[str]:
    return [bucket["bucket_time"] for bucket in generate_all_buckets()]


def get_buckets_grouped_by_hour(
    *,
    frequency_map: dict[str, int] | None = None,
) -> list[dict]:
    buckets = get_bucket_definitions(frequency_map=frequency_map)
    grouped: list[dict] = []

    for hour in range(24):
        hour_buckets = [bucket for bucket in buckets if bucket["hour"] == hour]
        grouped.append(
            {
                "hour": hour,
                "hour_label": f"{hour:02d}:00",
                "buckets": hour_buckets,
            }
        )

    return grouped
