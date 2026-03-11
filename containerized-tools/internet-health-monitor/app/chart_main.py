from __future__ import annotations

from datetime import datetime, UTC

from charting import build_latency_chart
from history_loader import extract_latency_points, load_historical_result_files_from_s3
from storage import upload_file_to_s3


OUTPUT_PATH = "artifacts/output/latency-trend.png"
CHART_LATEST_KEY = "internet-health-monitor/dev/charts/latest-latency-trend.png"


def build_chart_history_key(run_id: str) -> str:
    year = run_id[0:4]
    month = run_id[4:6]
    day = run_id[6:8]
    return f"internet-health-monitor/dev/charts/{year}/{month}/{day}/latency-trend-{run_id}.png"


def build_chart_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

def main() -> None:
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

    upload_file_to_s3(chart_path, CHART_LATEST_KEY)
    upload_file_to_s3(chart_path, build_chart_history_key(chart_run_id))

    print(f"[INFO] Loaded {len(payloads)} historical result files from S3")
    print(f"[INFO] Extracted {len(points)} latency points")
    print(f"[INFO] Chart written to {chart_path}")
    print(f"[INFO] Uploaded latest chart to s3://.../{CHART_LATEST_KEY}")
    print(f"[INFO] Uploaded historical chart for run {chart_run_id}")


if __name__ == "__main__":
    main()
