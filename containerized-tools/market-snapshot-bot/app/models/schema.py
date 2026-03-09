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
