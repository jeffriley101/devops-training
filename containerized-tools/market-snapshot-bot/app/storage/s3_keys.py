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
