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


