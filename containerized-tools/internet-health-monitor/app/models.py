from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_result(
    *,
    name: str,
    url: str,
    method: str,
    expected_statuses: list[int],
    actual_status: int | None,
    latency_ms: int | None,
    latency_threshold_ms: int,
    status: str,
    success: bool,
    error_type: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    return {
        "name": name,
        "url": url,
        "method": method,
        "expected_statuses": expected_statuses,
        "actual_status": actual_status,
        "latency_ms": latency_ms,
        "latency_threshold_ms": latency_threshold_ms,
        "status": status,
        "success": success,
        "error_type": error_type,
        "error_message": error_message,
        "checked_at_utc": utc_now_iso(),
    }


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    healthy = sum(1 for r in results if r["status"] == "healthy")
    degraded = sum(1 for r in results if r["status"] == "degraded")
    unhealthy = sum(1 for r in results if r["status"] == "unhealthy")

    if unhealthy > 0:
        overall_status = "unhealthy"
    elif degraded > 0:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return {
        "total_targets": len(results),
        "healthy": healthy,
        "degraded": degraded,
        "unhealthy": unhealthy,
        "overall_status": overall_status,
    }
