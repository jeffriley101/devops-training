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


def get_bucket_definitions() -> list[dict]:
    buckets = generate_all_buckets()
    chaperone_map = load_chaperone_map()

    for bucket in buckets:
        bucket["chaperone"] = chaperone_map.get(bucket["bucket_time"], "")

    return buckets


def get_bucket_choices() -> list[str]:
    return [bucket["bucket_time"] for bucket in get_bucket_definitions()]

def get_buckets_grouped_by_hour() -> list[dict]:
    buckets = get_bucket_definitions()
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
