from app.config import JILT_CHARTS_DIR


def get_chart_definitions() -> list[dict]:
    charts = [
        {
            "title": "Low Bucket Frequency",
            "filename": "low_bucket_frequency.png",
            "description": "Historical frequency of which 5-minute bucket most often contains GOLD's daily low.",
        },
        {
            "title": "Daily Low by Date",
            "filename": "daily_low_by_date.png",
            "description": "Daily mapping showing which 5-minute bucket contained the low on each trading date.",
        },
        {
            "title": "Daily Low Hour Heatmap",
            "filename": "daily_low_hour_heatmap.png",
            "description": "Heatmap view showing where GOLD daily lows cluster across the 24-hour futures cycle.",
        },
    ]

    for chart in charts:
        filepath = JILT_CHARTS_DIR / chart["filename"]
        chart["exists"] = filepath.exists()
        chart["url_path"] = f"/chart-files/{chart['filename']}"

    return charts
