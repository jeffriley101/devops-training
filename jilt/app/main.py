from app.chart_low_bucket_frequency import main as chart_low_bucket_frequency_main
from app.ingest_gold import main as ingest_gold_main
from app.refresh_daily_low_summary import main as refresh_daily_low_summary_main
from app.report_daily_lows import main as report_daily_lows_main
from app.report_low_bucket_frequency import main as report_low_bucket_frequency_main


def main() -> None:
    print()
    print("JILT Run Starting")
    print("=================")

    print()
    print("[1/5] Ingesting raw intraday bars...")
    ingest_gold_main()

    print()
    print("[2/5] Refreshing daily low summary...")
    refresh_daily_low_summary_main()

    print()
    print("[3/5] Printing daily low report...")
    report_daily_lows_main()

    print()
    print("[4/5] Printing low-bucket frequency report...")
    report_low_bucket_frequency_main()

    print()
    print("[5/5] Saving low-bucket frequency chart...")
    chart_low_bucket_frequency_main()

    print()
    print("JILT Run Complete")
    print("=================")


if __name__ == "__main__":
    main()
