import argparse
import json
import os
import platform
import socket
import sys
from datetime import UTC, datetime
from typing import Dict, Any


def log(msg: str, quiet: bool) -> None:
    if quiet:
        return
    print(f"[INFO] {msg}", file=sys.stderr)


def gather_environment_data() -> Dict[str, Any]:
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
    p.add_argument(
        "--format",
        choices=["json", "table"],
        default="json",
        help="Output format (default: json)",
    )
    p.add_argument(
        "--out",
        type=str,
        default="",
        help="Write JSON output to a file (still prints to stdout unless --quiet)",
    )
    p.add_argument("--quiet", action="store_true", help="Suppress logs to stderr")
    p.add_argument(
        "--require-var",
        action="append",
        default=[],
        help="Require an environment variable to be set (repeatable)",
    )
    p.add_argument(
        "--require-eq",
        action="append",
        default=[],
        help="Require VAR=VALUE match (repeatable). Example: --require-eq APP_ENV=production",
    )
    return p.parse_args()



def render_table(data: Dict[str, Any]) -> str:
    # Simple, dependency-free table
    key_width = max(len(k) for k in data.keys()) if data else 0
    lines = []
    for k in sorted(data.keys()):
        lines.append(f"{k.ljust(key_width)}  {data[k]}")
    return "\n".join(lines)


def main():
    args = parse_args()
    data = gather_environment_data()

    if args.fields.strip():
        requested = [f.strip() for f in args.fields.split(",") if f.strip()]
        unknown = [f for f in requested if f not in data]
        if unknown:
            log(f"Unknown field(s): {', '.join(unknown)}", args.quiet)
            sys.exit(2)
        data = {k: data[k] for k in requested}

    # Enforce required environment variables
    missing = [v for v in args.require_var if not os.getenv(v)]
    if missing:
        log(f"Missing required env var(s): {', '.join(missing)}", args.quiet)
        sys.exit(3)

    # Enforce required equality matches
    for expr in args.require_eq:
        if "=" not in expr:
            log(f"Invalid --require-eq value (expected VAR=VALUE): {expr}", args.quiet)
            sys.exit(4)
        var, expected = expr.split("=", 1)
        actual = os.getenv(var)
        if actual is None:
            log(f"Missing required env var for equality check: {var}", args.quiet)
            sys.exit(3)
        if actual != expected:
            log(f"Env var mismatch: {var}={actual!r} (expected {expected!r})", args.quiet)
            sys.exit(5)

    log("Collected environment data", args.quiet)

    # Always produce a canonical JSON string for machine use / file output
    json_text = json.dumps(data, indent=2 if args.pretty else None, sort_keys=True)

    # Optional file output (JSON only)
    if args.out:
        tmp_path = args.out + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json_text + "\n")
        os.replace(tmp_path, args.out)
        log(f"Wrote JSON to {args.out}", args.quiet)

    # Stdout output
    if args.format == "json":
        print(json_text)
    else:
        # Table is always human-oriented
        print(render_table(data))


if __name__ == "__main__":
    main()
