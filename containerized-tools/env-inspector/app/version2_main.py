import argparse
import json
import os
import platform
import socket
import sys
from datetime import UTC, datetime


def log(msg: str) -> None:
    # Logs go to stderr so stdout can stay machine-readable JSON
    print(f"[INFO] {msg}", file=sys.stderr)


def gather_environment_data():
    now = datetime.now(UTC)
    return {
        "timestamp_utc_iso": now.isoformat(),
        "timestamp_utc_human": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "hostname": socket.gethostname(),
        "ip_address": socket.gethostbyname(socket.gethostname()),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "python_version": platform.python_version(),
        "environment_variables_count": len(os.environ),
    }


def parse_args():
    p = argparse.ArgumentParser(description="env-inspector: emit deterministic environment metadata")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    p.add_argument(
        "--fields",
        type=str,
        default="",
        help="Comma-separated list of fields to include (default: all)",
    )
    return p.parse_args()


def main():
    args = parse_args()
    data = gather_environment_data()

    if args.fields.strip():
        requested = [f.strip() for f in args.fields.split(",") if f.strip()]
        unknown = [f for f in requested if f not in data]
        if unknown:
            log(f"Unknown field(s): {', '.join(unknown)}")
            sys.exit(2)
        data = {k: data[k] for k in requested}

    log("Collected environment data")
    print(json.dumps(data, indent=2 if args.pretty else None, sort_keys=True))


if __name__ == "__main__":
    main()
