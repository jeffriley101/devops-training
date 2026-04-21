import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env.local"


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value.startswith(("'", '"')) and value.endswith(("'", '"')):
            value = value[1:-1]

        os.environ.setdefault(key, value)


load_env_file()


DB_HOST = os.environ["JILT_GAME_DB_HOST"]
DB_PORT = int(os.environ["JILT_GAME_DB_PORT"])
DB_NAME = os.environ["JILT_GAME_DB_NAME"]
DB_USER = os.environ["JILT_GAME_DB_USER"]
DB_PASSWORD = os.environ["JILT_GAME_DB_PASSWORD"]

JILT_RESULT_JSON_PATH = Path(os.environ["JILT_RESULT_JSON_PATH"])
JILT_CHARTS_DIR = Path(os.environ["JILT_CHARTS_DIR"])
