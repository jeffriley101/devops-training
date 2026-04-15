import os

DB_HOST = os.environ.get("JILT_DB_HOST", "localhost")
DB_NAME = os.environ.get("JILT_DB_NAME", "jilt")
DB_USER = os.environ.get("JILT_DB_USER", "jilt_app")

MARKET_TIMEZONE = os.environ.get("JILT_MARKET_TIMEZONE", "America/New_York")
SYMBOL = os.environ.get("JILT_SYMBOL", "GC")
YFINANCE_TICKER = os.environ.get("JILT_YFINANCE_TICKER", "GC=F")
HISTORY_PERIOD = os.environ.get("JILT_HISTORY_PERIOD", "5d")
INTERVAL = os.environ.get("JILT_INTERVAL", "5m")
