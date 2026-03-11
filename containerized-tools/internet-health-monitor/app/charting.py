from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt


def _parse_timestamp(timestamp_utc: str) -> datetime:
    return datetime.strptime(timestamp_utc, "%Y-%m-%dT%H:%M:%SZ")


def _display_label(timestamp_utc: str) -> str:
    dt = _parse_timestamp(timestamp_utc)
    return dt.strftime("%m-%d %H:%M")


def build_latency_chart(points: list[dict], output_path: str) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    unique_timestamps = sorted({point["timestamp_utc"] for point in points})
    x_index = {timestamp: idx for idx, timestamp in enumerate(unique_timestamps)}
    x_labels = [_display_label(ts) for ts in unique_timestamps]

    grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for point in points:
        grouped[point["target_name"]].append(
            (x_index[point["timestamp_utc"]], point["latency_ms"])
        )

    fig, ax = plt.subplots(figsize=(12, 6))

    for target_name, series in sorted(grouped.items()):
        series.sort(key=lambda item: item[0])
        x_values = [item[0] for item in series]
        y_values = [item[1] for item in series]
        ax.plot(x_values, y_values, marker="o", label=target_name)

    ax.set_title("Internet Health Monitor Latency Trends")
    ax.set_xlabel("Run Timestamp (UTC)")
    ax.set_ylabel("Latency (ms)")
    ax.set_xticks(range(len(x_labels)))
    ax.set_xticklabels(x_labels, rotation=45, ha="right")
    ax.legend()
    ax.grid(True)

    fig.tight_layout()
    fig.savefig(output)
    plt.close(fig)

    return output
