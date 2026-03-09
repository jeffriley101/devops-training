import json
from pathlib import Path
from typing import Any

from app.models.schema import MarketSnapshot


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_market_snapshot(path: Path, snapshot: MarketSnapshot) -> None:
    write_json_file(path, snapshot.to_dict())


def build_human_summary(snapshot: MarketSnapshot) -> str:
    lines: list[str] = []
    lines.append(f"Market Snapshot [{snapshot.overall_status}]")
    lines.append(f"Snapshot ID: {snapshot.snapshot_id}")
    lines.append(f"Collected At: {snapshot.collected_at_utc}")
    lines.append(f"Environment: {snapshot.environment}")
    lines.append(f"Source: {snapshot.source}")
    lines.append("")

    for instrument in snapshot.instruments:
        price_text = "unavailable" if instrument.price is None else f"{instrument.price:.2f}"
        lines.append(
            f"- {instrument.symbol} | {instrument.display_name} | "
            f"price={price_text} {instrument.currency} | status={instrument.status}"
        )

    return "\n".join(lines)
