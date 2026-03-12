from __future__ import annotations

import os
from datetime import datetime, UTC

from charting import build_latency_chart
from history_loader import extract_latency_points, load_historical_result_files_from_s3
from storage import upload_file_to_s3


OUTPUT_PATH = "artifacts/output/latency-trend.png"


def get_environment() -> str:
    return os.getenv("ENVIRONMENT", "dev")


def build_chart_latest_key(environment: str) -> str:
    return f"internet-health-monitor/{environment}/charts/latest-latency-trend.png"


def build_chart_history_key(environment: str, run_id: str) -> str:
    year = run_id[0:4]
    month = run_id[4:6]
    day = run_id[6:8]
    return f"internet-health-monitor/{environment}/charts/{year}/{month}/{day}/latency-trend-{run_id}.png"


def build_chart_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def main() -> None:
    environment = get_environment()
    payloads = load_historical_result_files_from_s3()
    points = extract_latency_points(payloads)

    if not payloads:
        print("[INFO] No historical result files found in S3.")
        return

    if not points:
        print("[INFO] Historical files found in S3, but no latency points were extracted.")
        return

    chart_path = build_latency_chart(points, OUTPUT_PATH)
    chart_run_id = build_chart_run_id()
    latest_key = build_chart_latest_key(environment)
    history_key = build_chart_history_key(environment, chart_run_id)

    upload_file_to_s3(chart_path, latest_key)
    upload_file_to_s3(chart_path, history_key)

    print(f"[INFO] Loaded {len(payloads)} historical result files from S3")
    print(f"[INFO] Extracted {len(points)} latency points")
    print(f"[INFO] Chart written to {chart_path}")
    print(f"[INFO] Uploaded latest chart to s3://.../{latest_key}")
    print(f"[INFO] Uploaded historical chart for run {chart_run_id} to s3://.../{history_key}")


if __name__ == "__main__":
    main()
