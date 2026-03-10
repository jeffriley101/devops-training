from __future__ import annotations

import time
from typing import Any

import certifi
import requests

from models import build_result


def classify_result(
    *,
    actual_status: int | None,
    expected_statuses: list[int],
    latency_ms: int | None,
    latency_threshold_ms: int,
    error_type: str | None,
) -> str:
    if error_type is not None:
        return "unhealthy"

    if actual_status not in expected_statuses:
        return "unhealthy"

    if latency_ms is not None and latency_ms > latency_threshold_ms:
        return "degraded"

    return "healthy"


def check_target(target: dict[str, Any]) -> dict[str, Any]:
    name = target["name"]
    url = target["url"]
    method = target.get("method", "GET").upper()
    timeout_seconds = target.get("timeout_seconds", 5)
    expected_statuses = target.get("expected_statuses", [200])
    latency_threshold_ms = target.get("latency_threshold_ms", 2000)

    actual_status: int | None = None
    latency_ms: int | None = None
    error_type: str | None = None
    error_message: str | None = None

    start = time.perf_counter()

    try:
        response = requests.request(
            method=method,
            url=url,
            timeout=timeout_seconds,
            verify=certifi.where(),
        )
        latency_ms = round((time.perf_counter() - start) * 1000)
        actual_status = response.status_code
    except requests.exceptions.Timeout as exc:
        error_type = "timeout"
        error_message = str(exc)
    except requests.exceptions.SSLError as exc:
        error_type = "ssl_error"
        error_message = str(exc)
    except requests.exceptions.ConnectionError as exc:
        error_type = "connection_error"
        error_message = str(exc)
    except requests.exceptions.RequestException as exc:
        error_type = "request_error"
        error_message = str(exc)

    status = classify_result(
        actual_status=actual_status,
        expected_statuses=expected_statuses,
        latency_ms=latency_ms,
        latency_threshold_ms=latency_threshold_ms,
        error_type=error_type,
    )

    return build_result(
        name=name,
        url=url,
        method=method,
        expected_statuses=expected_statuses,
        actual_status=actual_status,
        latency_ms=latency_ms,
        latency_threshold_ms=latency_threshold_ms,
        status=status,
        success=(status != "unhealthy"),
        error_type=error_type,
        error_message=error_message,
    )
