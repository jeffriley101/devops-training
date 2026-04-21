import argparse

from app.publish_result_artifact import publish_latest_result_artifact
from app.chart_low_bucket_frequency import main as chart_low_bucket_frequency_main
from app.chart_daily_low_by_date import main as chart_daily_low_by_date_main
from app.chart_daily_low_hour_heatmap import main as chart_daily_low_hour_heatmap_main
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
    print("[1/8] Ingesting raw intraday bars...")
    ingest_result = ingest_gold_main()

    print()
    print("[2/8] Refreshing daily low summary...")
    summary_result = refresh_daily_low_summary_main()

    print()
    print("[3/8] Printing daily low report...")
    daily_report_result = report_daily_lows_main()

    print()
    print("[4/8] Printing low-bucket frequency report...")
    frequency_result = report_low_bucket_frequency_main()

    print()
    print("[5/8] Saving low-bucket frequency chart...")
    frequency_chart_result = chart_low_bucket_frequency_main()

    print()
    print("[6/8] Saving daily-low-by-date chart...")
    date_chart_result = chart_daily_low_by_date_main()

    print()
    print("[7/8] Saving daily-low-hour heatmap...")
    heatmap_result = chart_daily_low_hour_heatmap_main()

    print()
    print("[8/8] Publishing latest result artifact...")
    result_artifact = publish_latest_result_artifact()

    print()
    print("JILT Run Summary")
    print("================")
    print(
        f"Raw bars processed: {ingest_result['processed_rows']} | "
        f"inserted: {ingest_result['inserted_rows']}"
    )
    print(f"Daily summary rows affected: {summary_result['refreshed_rows']}")
    print(f"Daily report rows printed: {daily_report_result['row_count']}")
    print(
        f"Frequency buckets: {frequency_result['bucket_count']} | "
        f"days analyzed: {frequency_result['total_days']}"
    )
    print(
        f"Frequency chart created: {frequency_chart_result['chart_created']} | "
        f"path: {frequency_chart_result['output_path']}"
    )
    print(
        f"Date chart created: {date_chart_result['chart_created']} | "
        f"path: {date_chart_result['output_path']}"
    )
    print(
        f"Hour heatmap created: {heatmap_result['chart_created']} | "
        f"path: {heatmap_result['output_path']}"
    )
    if result_artifact is None:
        print("Latest result artifact created: False | no daily low result found")
    else:
        print(
            f"Latest result artifact created: {result_artifact['artifact_written']} | "
            f"path: {result_artifact['output_path']} | "
            f"game_date_et: {result_artifact['game_date_et']} | "
            f"winning_bucket: {result_artifact['winning_bucket']}"
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
            "chart-frequency",
            "chart-date",
            "chart-heatmap",
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
    elif args.mode == "chart-frequency":
        chart_low_bucket_frequency_main()
    elif args.mode == "chart-date":
        chart_daily_low_by_date_main()
    elif args.mode == "chart-heatmap":
        chart_daily_low_hour_heatmap_main()


if __name__ == "__main__":
    main()
