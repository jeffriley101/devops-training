import argparse
import sys

from app.workflows.price import run_price_workflow
from app.workflows.volume import run_volume_workflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Market Monitoring Platform"
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["price", "volume"],
        help="Which workflow to run",
    )
    parser.add_argument(
        "--allow-outside-window",
        action="store_true",
        help="Allow volume workflow to run outside the normal intraday window",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.mode == "price":
        run_price_workflow()
    elif args.mode == "volume":
        run_volume_workflow(args.allow_outside_window)
    else:
        raise ValueError(f"Unsupported mode: {args.mode}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
