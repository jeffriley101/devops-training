from __future__ import annotations

import json
import os
from typing import Any

import boto3


S3_BUCKET = os.getenv("ARTIFACT_S3_BUCKET")
S3_PREFIX = os.getenv("ARTIFACT_S3_PREFIX", "internet-health-monitor/dev")
CHART_LOOKBACK_LIMIT = int(os.getenv("CHART_LOOKBACK_LIMIT", "50"))


def load_historical_result_files_from_s3() -> list[dict[str, Any]]:
    if not S3_BUCKET:
        raise ValueError("ARTIFACT_S3_BUCKET is required")

    s3 = boto3.client("s3")

    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX)

    result_keys: list[str] = []

    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "/results-" in key and key.endswith(".json"):
                result_keys.append(key)

    result_keys = sorted(result_keys)[-CHART_LOOKBACK_LIMIT:]

    payloads: list[dict[str, Any]] = []

    for key in result_keys:
        response = s3.get_object(Bucket=S3_BUCKET, Key=key)
        body = response["Body"].read().decode("utf-8")
        payloads.append(json.loads(body))

    return payloads


def extract_latency_points(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []

    for payload in payloads:
        run_metadata = payload.get("run_metadata", {})
        timestamp_utc = run_metadata.get("timestamp_utc")

        for result in payload.get("results", []):
            latency_ms = result.get("latency_ms")
            target_name = result.get("name")
            status = result.get("status")

            if timestamp_utc is None or target_name is None:
                continue

            if latency_ms is None:
                continue

            points.append(
                {
                    "timestamp_utc": timestamp_utc,
                    "target_name": target_name,
                    "latency_ms": latency_ms,
                    "status": status,
                }
            )

    return points
