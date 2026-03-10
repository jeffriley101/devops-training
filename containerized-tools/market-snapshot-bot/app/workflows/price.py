from datetime import datetime
from pathlib import Path

from app.artifacts.builders import build_human_summary, write_market_snapshot
from app.artifacts.csv_history import append_dict_row
from app.charts.price_chart import generate_price_chart
from app.config import load_config
from app.models.schema import InstrumentSnapshot, MarketSnapshot


def build_mock_instruments(source: str) -> list[InstrumentSnapshot]:
    return [
        InstrumentSnapshot(
            symbol="XAG/USD",
            display_name="Silver Spot / US Dollar",
            asset_class="commodity",
            price=31.42,
            currency="USD",
            as_of_utc="2026-03-09T01:30:45Z",
            source=source,
            source_symbol="XAG/USD",
            status="success",
        ),
        InstrumentSnapshot(
            symbol="WTI/USD",
            display_name="WTI Crude Oil / US Dollar",
            asset_class="commodity",
            price=67.85,
            currency="USD",
            as_of_utc="2026-03-09T01:30:45Z",
            source=source,
            source_symbol="WTI/USD",
            status="success",
        ),
    ]


def determine_overall_status(instruments: list[InstrumentSnapshot]) -> str:
    statuses = {instrument.status for instrument in instruments}
    if statuses == {"success"}:
        return "success"
    if "success" in statuses:
        return "partial_failure"
    return "failure"


def append_price_history(snapshot: MarketSnapshot) -> Path:
    history_path = Path("dev/price/history/price_history.csv")
    fieldnames = [
        "date_utc",
        "symbol",
        "price",
        "currency",
        "collected_at_utc",
        "status",
    ]

    date_utc = snapshot.collected_at_utc[:10]

    for instrument in snapshot.instruments:
        row = {
            "date_utc": date_utc,
            "symbol": instrument.symbol,
            "price": instrument.price,
            "currency": instrument.currency,
            "collected_at_utc": snapshot.collected_at_utc,
            "status": instrument.status,
        }
        append_dict_row(history_path, fieldnames, row)

    return history_path


def run_price_workflow() -> None:
    config = load_config()

    timestamp = datetime.utcnow()
    snapshot_id = timestamp.strftime("%Y%m%dT%H%M%SZ")
    collected_at_utc = timestamp.replace(microsecond=0).isoformat() + "Z"

    instruments = build_mock_instruments(config.data_source)
    overall_status = determine_overall_status(instruments)

    snapshot = MarketSnapshot(
        snapshot_id=snapshot_id,
        collected_at_utc=collected_at_utc,
        environment=config.environment,
        source=config.data_source,
        overall_status=overall_status,
        instruments=instruments,
    )

    output_path = (
        Path("dev/price/daily")
        / timestamp.strftime("%Y/%m/%d")
        / "price_snapshot.json"
    )
    write_market_snapshot(output_path, snapshot)

    history_path = append_price_history(snapshot)

    chart_path = Path("dev/price/charts/price_history.png")
    generate_price_chart(history_path, chart_path)

    print(build_human_summary(snapshot))
    print("")
    print(f"Price snapshot written to: {output_path}")
    print(f"Price history updated: {history_path}")
    print(f"Price chart updated: {chart_path}")
