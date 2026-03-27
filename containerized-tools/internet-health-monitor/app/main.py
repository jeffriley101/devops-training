from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml

from checker import check_target
from models import build_summary, utc_now_iso
from report import render_report
from storage import write_json, write_text, upload_file_to_s3


CONFIG_PATH = Path("config/targets.yaml")
CHECKER_VERSION = "0.1.0"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def build_run_id(timestamp_utc: str) -> str:
    return timestamp_utc.replace("-", "").replace(":", "").replace("T", "T").replace("Z", "Z")


def build_history_results_key(environment: str, run_id: str) -> str:
    year = run_id[0:4]
    month = run_id[4:6]
    day = run_id[6:8]
    return f"internet-health-monitor/{environment}/{year}/{month}/{day}/results-{run_id}.json"


def main() -> None:
    app_start = time.perf_counter()
    config = load_config()
    targets = config.get("targets", [])

    results: list[dict[str, Any]] = []
    for target in targets:
        results.append(check_target(target))

    execution_duration_ms = round((time.perf_counter() - app_start) * 1000)
    timestamp_utc = utc_now_iso()

    environment = os.getenv("ENVIRONMENT", "dev")
    run_id = build_run_id(timestamp_utc)

    run_metadata = {
        "project": "internet-health-monitor",
        "environment": environment,
        "run_id": run_id,
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

    upload_file_to_s3(json_path)
    upload_file_to_s3(report_path)
    upload_file_to_s3(json_path, build_history_results_key(environment, run_id))

    print("[INFO] Health check run complete")
    print(f"[INFO] JSON artifact written to {json_path}")
    print(f"[INFO] Report written to {report_path}")
    print(report_text, end="")


if __name__ == "__main__":
    main()
