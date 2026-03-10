from __future__ import annotations

from typing import Any


def render_report(run_metadata: dict[str, Any], summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines: list[str] = []

    lines.append("Internet Health Monitor")
    lines.append(f"Execution Time: {run_metadata['timestamp_utc']}")
    lines.append(f"Environment: {run_metadata['environment']}")
    lines.append(f"Run ID: {run_metadata['run_id']}")
    lines.append(f"Checker Version: {run_metadata['checker_version']}")
    lines.append(f"Git SHA: {run_metadata['git_sha']}")
    lines.append("")

    lines.append("Summary")
    lines.append(f"- Total Targets: {summary['total_targets']}")
    lines.append(f"- Healthy: {summary['healthy']}")
    lines.append(f"- Degraded: {summary['degraded']}")
    lines.append(f"- Unhealthy: {summary['unhealthy']}")
    lines.append(f"- Overall Status: {summary['overall_status']}")
    lines.append(f"- Execution Duration: {run_metadata['execution_duration_ms']} ms")
    lines.append("")

    lines.append("Target Results")
    for result in results:
        actual_status = result["actual_status"] if result["actual_status"] is not None else "n/a"
        latency_ms = result["latency_ms"] if result["latency_ms"] is not None else "n/a"
        lines.append(
            f"- {result['name']:<10} | {result['status']:<9} | status={actual_status} | latency={latency_ms} ms"
        )

    lines.append("")
    lines.append("Failures")
    failures = [r for r in results if r["status"] == "unhealthy"]
    if failures:
        for result in failures:
            lines.append(
                f"- {result['name']}: error_type={result['error_type']} error_message={result['error_message']}"
            )
    else:
        lines.append("- None")

    lines.append("")
    lines.append("Slow Targets")
    slow = [
        r for r in results
        if r["latency_ms"] is not None and r["latency_ms"] > r["latency_threshold_ms"]
    ]
    if slow:
        for result in slow:
            lines.append(
                f"- {result['name']} exceeded latency threshold "
                f"({result['latency_ms']} ms > {result['latency_threshold_ms']} ms)"
            )
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"
