from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.charting import build_latency_chart
from app.checker import check_target
from app.history_loader import extract_latency_points, load_historical_result_files_from_s3
from app.models import build_summary, utc_now_iso
from app.report import render_report
from app.storage import upload_file_to_s3, write_json, write_text


CONFIG_PATH = Path("config/targets.yaml")
CHECKER_VERSION = "0.1.0"
CHART_OUTPUT_PATH = "artifacts/output/latency-trend.png"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def build_run_id(timestamp_utc: str) -> str:
    return timestamp_utc.replace("-", "").replace(":", "").replace("T", "T").replace("Z", "Z")


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
    app_start = time.perf_counter()
    config = load_config()
    targets = config.get("targets", [])

    results: list[dict[str, Any]] = []
    for target in targets:
        results.append(check_target(target))

    execution_duration_ms = round((time.perf_counter() - app_start) * 1000)
    timestamp_utc = utc_now_iso()

    run_metadata = {
        "project": "internet-health-monitor",
        "environment": os.getenv("ENVIRONMENT", "dev"),
        "run_id": build_run_id(timestamp_utc),
        "timestamp_utc": timestamp_utc,
        "execution_duration_ms": execution_duration_ms,
        "checker_version": CHECKER_VERSION,
        "git_sha": os.getenv("GIT_SHA", "dev-local"),
    }

    summary = build_summary(results)

    payload = {
        "run_metadata": run_metadata,
        "summary": summary,
        "results": results,
    }

    report_text = render_report(run_metadata, summary, results)

    json_path = write_json("latest-results.json", payload)
    report_path = write_text("latest-report.txt", report_text)

    print("[INFO] Health check run complete")
    print(f"[INFO] JSON artifact written to {json_path}")
    print(f"[INFO] Report written to {report_path}")
    print(report_text, end="")

    try:
        environment = os.getenv("ENVIRONMENT", "dev")
        payloads = load_historical_result_files_from_s3()
        points = extract_latency_points(payloads)

        if not payloads:
            print("[INFO] No historical result files found in S3 for chart generation.")
            return

        if not points:
            print("[INFO] Historical files found in S3, but no latency points were extracted.")
            return

        chart_path = build_latency_chart(points, CHART_OUTPUT_PATH)
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
    except Exception as exc:
        print(f"[WARN] Chart generation/upload failed: {exc}")


if __name__ == "__main__":
    main()

