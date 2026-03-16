from datetime import datetime
import os
from pathlib import Path

from app.clients.market_data import MarketDataError, fetch_yahoo_last_price
from app.artifacts.builders import build_human_summary, write_market_snapshot
from app.artifacts.csv_history import append_dict_row
from app.charts.price_chart import (
    generate_price_chart_all_runs,
    generate_price_chart_official_daily,
)
from app.config import load_config
from app.models.schema import InstrumentSnapshot, MarketSnapshot

PRICE_SOURCE_SYMBOLS = {
    "XAG/USD": "SI=F",
    "WTI/USD": "CL=F",
}

PRICE_DISPLAY_NAMES = {
    "XAG/USD": "Silver Futures",
    "WTI/USD": "WTI Crude Oil Futures",
}


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


def build_real_instruments(symbols: list[str]) -> list[InstrumentSnapshot]:
    instruments: list[InstrumentSnapshot] = []

    for symbol in symbols:
        source_symbol = PRICE_SOURCE_SYMBOLS.get(symbol, symbol)
        display_name = PRICE_DISPLAY_NAMES.get(symbol, symbol)

        try:
            quote = fetch_yahoo_last_price(source_symbol)
            instruments.append(
                InstrumentSnapshot(
                    symbol=symbol,
                    display_name=display_name,
                    asset_class="commodity",
                    price=quote.price,
                    currency=quote.currency,
                    as_of_utc=quote.as_of_utc,
                    source=quote.source,
                    source_symbol=quote.source_symbol,
                    status="success",
                )
            )
        except MarketDataError as exc:
            instruments.append(
                InstrumentSnapshot(
                    symbol=symbol,
                    display_name=display_name,
                    asset_class="commodity",
                    price=0.0,
                    currency="USD",
                    as_of_utc="",
                    source="yahoo_finance",
                    source_symbol=source_symbol,
                    status=f"failure: {exc}",
                )
            )

    return instruments


def determine_overall_status(instruments: list[InstrumentSnapshot]) -> str:
    statuses = {instrument.status for instrument in instruments}
    if statuses == {"success"}:
        return "success"
    if "success" in statuses:
        return "partial_failure"
    return "failure"


def get_run_context() -> str:
    return os.getenv("RUN_CONTEXT", "manual").strip() or "manual"


def append_price_history(snapshot: MarketSnapshot) -> Path:
    history_path = Path("dev/price/history/price_history.csv")
    fieldnames = [
        "date_utc",
        "symbol",
        "price",
        "currency",
        "collected_at_utc",
        "status",
        "source",
        "run_context",
    ]

    date_utc = snapshot.collected_at_utc[:10]
    run_context = get_run_context()

    for instrument in snapshot.instruments:
        row = {
            "date_utc": date_utc,
            "symbol": instrument.symbol,
            "price": round(instrument.price, 2),
            "currency": instrument.currency,
            "collected_at_utc": snapshot.collected_at_utc,
            "status": instrument.status,
            "source": snapshot.source,
            "run_context": run_context,
        }
        append_dict_row(history_path, fieldnames, row)

    return history_path


def run_price_workflow() -> None:
    config = load_config()

    timestamp = datetime.utcnow()
    snapshot_id = timestamp.strftime("%Y%m%dT%H%M%SZ")
    collected_at_utc = timestamp.replace(microsecond=0).isoformat() + "Z"

    if config.data_source == "mock":
        instruments = build_mock_instruments(config.data_source)
    elif config.data_source == "real":
        instruments = build_real_instruments(config.symbols)
    else:
        raise ValueError(f"Unsupported DATA_SOURCE: {config.data_source}")

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

    all_runs_chart_path = Path("dev/price/charts/price_history_all_runs.png")
    official_daily_chart_path = Path("dev/price/charts/price_history_official_daily.png")

    generate_price_chart_all_runs(history_path, all_runs_chart_path)
    generate_price_chart_official_daily(history_path, official_daily_chart_path)

    print(build_human_summary(snapshot))
    print("")
    print(f"Price snapshot written to: {output_path}")
    print(f"Price history updated: {history_path}")
    print(f"Price all-runs chart updated: {all_runs_chart_path}")
    print(f"Price official daily chart updated: {official_daily_chart_path}")

