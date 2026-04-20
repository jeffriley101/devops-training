import argparse

from app.chart_low_bucket_frequency import main as chart_low_bucket_frequency_main
from app.ingest_gold import main as ingest_gold_main
from app.refresh_daily_low_summary import main as refresh_daily_low_summary_main
from app.report_daily_lows import main as report_daily_lows_main
from app.report_low_bucket_frequency import main as report_low_bucket_frequency_main
from app.retention import main as retention_main


def run_full_pipeline() -> None:
    print()
    print("JILT Run Starting")
    print("=================")

    print()
    print("[1/6] Ingesting raw intraday bars...")
    ingest_result = ingest_gold_main()

    print()
    print("[2/6] Refreshing daily low summary...")
    summary_result = refresh_daily_low_summary_main()

    print()
    print("[3/6] Applying raw retention...")
    retention_result = retention_main()

    print()
    print("[4/6] Printing daily low report...")
    daily_report_result = report_daily_lows_main()

    print()
    print("[5/6] Printing low-bucket frequency report...")
    frequency_result = report_low_bucket_frequency_main()

    print()
    print("[6/6] Saving low-bucket frequency chart...")
    chart_result = chart_low_bucket_frequency_main()

    print()
    print("JILT Run Summary")
    print("================")
    print(
        f"Raw bars processed: {ingest_result['processed_rows']} | "
        f"inserted: {ingest_result['inserted_rows']}"
    )
    print(f"Daily summary rows affected: {summary_result['refreshed_rows']}")
    print(f"Raw rows deleted by retention: {retention_result['deleted_rows']}")
    print(f"Daily report rows printed: {daily_report_result['row_count']}")
    print(
        f"Frequency buckets: {frequency_result['bucket_count']} | "
        f"days analyzed: {frequency_result['total_days']}"
    )
    print(
        f"Chart created: {chart_result['chart_created']} | "
        f"path: {chart_result['output_path']}"
    )

    print()
    print("JILT Run Complete")
    print("=================")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="JILT - Jeff's Intraday Low Toolkit"
    )

    parser.add_argument(
        "--mode",
        default="run",
        choices=[
            "run",
            "ingest",
            "refresh-summary",
            "retention",
            "report-daily",
            "report-frequency",
            "chart",
        ],
        help="Which JILT action to run.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.mode == "run":
        run_full_pipeline()
    elif args.mode == "ingest":
        ingest_gold_main()
    elif args.mode == "refresh-summary":
        refresh_daily_low_summary_main()
    elif args.mode == "retention":
        retention_main()
    elif args.mode == "report-daily":
        report_daily_lows_main()
    elif args.mode == "report-frequency":
        report_low_bucket_frequency_main()
    elif args.mode == "chart":
        chart_low_bucket_frequency_main()


if __name__ == "__main__":
    main()
