from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import boto3


OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "artifacts/output"))
S3_BUCKET = os.getenv("ARTIFACT_S3_BUCKET")
S3_PREFIX = os.getenv("ARTIFACT_S3_PREFIX", "")


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


def upload_file_to_s3(path: Path) -> None:
    if not S3_BUCKET:
        return

    s3 = boto3.client("s3")
    key = f"{S3_PREFIX.rstrip('/')}/{path.name}" if S3_PREFIX else path.name

    s3.upload_file(str(path), S3_BUCKET, key)
    print(f"[INFO] Uploaded {path.name} to s3://{S3_BUCKET}/{key}")
