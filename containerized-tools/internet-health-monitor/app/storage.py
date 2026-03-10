from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "artifacts/output"))


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def write_json(filename: str, payload: dict[str, Any]) -> Path:
    ensure_output_dir()
    path = OUTPUT_DIR / filename
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def write_text(filename: str, content: str) -> Path:
    ensure_output_dir()
    path = OUTPUT_DIR / filename
    path.write_text(content, encoding="utf-8")
    return path
