import argparse

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
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.mode == "price":
        run_price_workflow()
    elif args.mode == "volume":
        run_volume_workflow()
    else:
        raise ValueError(f"Unsupported mode: {args.mode}")


if __name__ == "__main__":
    main()
