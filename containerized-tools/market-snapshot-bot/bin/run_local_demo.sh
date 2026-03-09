#Market Snapshot Bot

RESET='\033[0m'
AMBER='\033[33m'
RED='\033[31m'


##Create the repo directory in /tmp and run the demo

mkdir -p /tmp/market-snapshot-bot-demo 
cd ; cd /tmp/market-snapshot-bot-demo
mkdir -p app/{clients,transformers,models,artifacts,storage,runtime_metadata}
mkdir -p tests
mkdir -p samples
mkdir -p infra
mkdir -p docs
touch app/__init__.py
touch app/clients/__init__.py
touch app/transformers/__init__.py
touch app/models/__init__.py
touch app/artifacts/__init__.py
touch app/storage/__init__.py
touch app/runtime_metadata/__init__.py
touch app/main.py
touch app/config.py
touch tests/__init__.py
touch README.md
touch Dockerfile
touch requirements.txt
touch app/main.py
touch app/config.py
touch app/models/schema.py
touch app/artifacts/builders.py
touch app/storage/s3_keys.py
touch samples/twelvedata_xagusd.json
touch samples/twelvedata_wtiusd.json
touch README.md
mkdir -p dev/raw/2026/03/09/013045Z
mkdir -p dev/normalized/2026/03/09/013045Z
mkdir -p dev/summary/2026/03/09/013045Z
mkdir -p dev/latest
touch dev/raw/2026/03/09/013045Z/raw.json
touch dev/normalized/2026/03/09/013045Z/snapshot.json
touch dev/summary/2026/03/09/013045Z/summary.json
touch dev/latest/latest_snapshot.json
touch dev/latest/latest_summary.json
tree -a -I '.git|__pycache__'

cat > app/config.py << 'EOF'
import os
from dataclasses import dataclass
from typing import List


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class AppConfig:
    environment: str
    data_source: str
    symbols: List[str]
    s3_bucket: str
    s3_prefix: str
    log_level: str


def _get_env(name: str, required: bool = True, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _parse_symbols(raw: str) -> List[str]:
    symbols = [s.strip() for s in raw.split(",") if s.strip()]
    if not symbols:
        raise ConfigError("SYMBOLS must contain at least one symbol")
    return symbols


def load_config() -> AppConfig:
    environment = _get_env("APP_ENV")
    data_source = _get_env("DATA_SOURCE")
    symbols_raw = _get_env("SYMBOLS")
    s3_bucket = _get_env("S3_BUCKET")
    s3_prefix = _get_env("S3_PREFIX", default="market-snapshot-bot")
    log_level = _get_env("LOG_LEVEL", required=False, default="INFO")

    symbols = _parse_symbols(symbols_raw)

    return AppConfig(
        environment=environment,
        data_source=data_source,
        symbols=symbols,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        log_level=log_level,
    )
EOF







cat > app/models/schema.py << 'EOF'
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Literal


Status = Literal["success", "partial_failure", "failure"]


@dataclass(frozen=True)
class InstrumentSnapshot:
    symbol: str
    display_name: str
    asset_class: str
    price: float | None
    currency: str
    as_of_utc: str
    source: str
    source_symbol: str
    status: Status


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    collected_at_utc: str
    environment: str
    source: str
    overall_status: Status
    instruments: List[InstrumentSnapshot]

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "collected_at_utc": self.collected_at_utc,
            "environment": self.environment,
            "source": self.source,
            "overall_status": self.overall_status,
            "instruments": [asdict(i) for i in self.instruments],
        }


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
EOF





cat >  app/storage/s3_keys.py << 'EOF'

from datetime import datetime


def _ts_parts(ts: datetime) -> tuple[str, str, str, str]:
    return (
        ts.strftime("%Y"),
        ts.strftime("%m"),
        ts.strftime("%d"),
        ts.strftime("%H%M%SZ"),
    )


def raw_key(prefix: str, env: str, ts: datetime) -> str:
    y, m, d, hms = _ts_parts(ts)
    return f"{prefix}/{env}/raw/{y}/{m}/{d}/{hms}/raw.json"


def normalized_key(prefix: str, env: str, ts: datetime) -> str:
    y, m, d, hms = _ts_parts(ts)
    return f"{prefix}/{env}/normalized/{y}/{m}/{d}/{hms}/snapshot.json"


def summary_key(prefix: str, env: str, ts: datetime) -> str:
    y, m, d, hms = _ts_parts(ts)
    return f"{prefix}/{env}/summary/{y}/{m}/{d}/{hms}/summary.json"


def latest_snapshot_key(prefix: str, env: str) -> str:
    return f"{prefix}/{env}/latest/latest_snapshot.json"


def latest_summary_key(prefix: str, env: str) -> str:
    return f"{prefix}/{env}/latest/latest_summary.json"
EOF



cat > README.md << 'EOF'

# Market Snapshot Bot

Scheduled financial market data automation platform built on a reusable AWS container automation foundation.

## Purpose

This project demonstrates platform engineering skills applied to financial data workflows:

- Scheduled container automation (ECS Fargate + EventBridge)
- External API integration for commodity market data
- Data normalization into stable internal schemas
- Versioned artifact publishing to S3
- Production-style platform reuse across domains

## Scope (v1)

Instruments:
- XAG/USD (Silver)
- WTI/USD (Crude Oil)

Cadence:
- Scheduled batch snapshot

Artifacts:
- Raw source payload
- Normalized snapshot (internal schema)
- Human-readable summary

## Architecture Overview

EventBridge Schedule  
→ ECS Fargate Task  
→ Containerized Snapshot Engine  
→ External Market Data API  
→ Artifact Generation  
→ S3 Versioned Storage  
→ CloudWatch Logs

## Normalized Data Contract

Each run produces a MarketSnapshot object containing:

- snapshot_id
- collected_at_utc
- environment
- source
- overall_status
- instruments[]

Each instrument includes:

- symbol
- display_name
- asset_class
- price
- currency
- as_of_utc
- source
- source_symbol
- status

Status values:
- success
- partial_failure
- failure

## Artifact Layout (S3)

<prefix>/<env>/raw/YYYY/MM/DD/HHMMSSZ/raw.json
<prefix>/<env>/normalized/YYYY/MM/DD/HHMMSSZ/snapshot.json
<prefix>/<env>/summary/YYYY/MM/DD/HHMMSSZ/summary.json
<prefix>/<env>/latest/latest_snapshot.json
<prefix>/<env>/latest/latest_summary.json


## Configuration (Environment Variables)

- APP_ENV
- DATA_SOURCE
- SYMBOLS
- S3_BUCKET
- S3_PREFIX
- LOG_LEVEL

## Strategic Value

This project demonstrates:

- Transferable platform engineering patterns
- Reliable scheduled automation
- Structured artifact versioning
- External dependency integration
- Data contract discipline
EOF



cat > app/artifacts/builders.py << 'EOF'
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
EOF
 
    
cat >  app/main.py << 'EOF'

from datetime import datetime
from pathlib import Path

from app.artifacts.builders import build_human_summary, write_market_snapshot
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


def main() -> None:
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

    output_path = Path("dev/normalized") / timestamp.strftime("%Y/%m/%d/%H%M%SZ") / "snapshot.json"
    write_market_snapshot(output_path, snapshot)

    print(build_human_summary(snapshot))
    print("")
    print(f"Normalized snapshot written to: {output_path}")


if __name__ == "__main__":
    main()
EOF
    
    
export APP_ENV=dev
export DATA_SOURCE=twelve_data
export SYMBOLS=XAG/USD,WTI/USD
export S3_BUCKET=dummy-local-bucket
export S3_PREFIX=market-snapshot-bot
export LOG_LEVEL=INFO
python3 -m app.main
printf "%b\n" "${AMBER}Verifications:${RESET} vim \$(find /tmp/market-snapshot-bot-demo/dev/normalized -name snapshot.json | sort | tail -n 1)"
printf "${RED}Destroy this test:${RESET} cd ; rm -rI /tmp/market-snapshot-bot-demo\n"
