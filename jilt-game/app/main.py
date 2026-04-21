from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.errors import UniqueViolation

from app.charts import get_chart_definitions
from app.config import JILT_CHARTS_DIR
from app.db import (
    get_daily_result,
    get_latest_daily_result,
    insert_daily_guess,
    list_daily_guesses,
    list_recent_daily_results,
    list_winning_guesses,
)
from app.result_ingest import ingest_latest_result_file_to_db

BASE_DIR = Path(__file__).resolve().parent.parent
ET_TZ = ZoneInfo("America/New_York")

app = FastAPI(title="JILT GAME")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/chart-files", StaticFiles(directory=JILT_CHARTS_DIR), name="chart-files")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def build_bucket_choices() -> list[str]:
    buckets: list[str] = []
    for hour in range(24):
        for minute in range(0, 60, 5):
            buckets.append(f"{hour:02d}:{minute:02d}")
    return buckets


def current_et_date() -> date:
    return datetime.now(ET_TZ).date()


def format_timestamp_et(value: datetime) -> str:
    et_value = value.astimezone(ET_TZ)
    return et_value.strftime("%Y-%m-%d %I:%M:%S %p ET")


def build_latest_result_outcome(latest_result: dict | None) -> dict | None:
    if latest_result is None:
        return None

    game_date_value = latest_result["game_date_et"]
    if isinstance(game_date_value, str):
        try:
            result_date = date.fromisoformat(game_date_value)
        except ValueError:
            return None
    else:
        result_date = game_date_value

    symbol_code = latest_result["symbol"]
    winning_bucket = latest_result["winning_bucket"]

    winning_rows = list_winning_guesses(
        symbol_code=symbol_code,
        game_date_et=result_date,
        winning_bucket=winning_bucket,
    )

    winners = []
    for row in winning_rows:
        winners.append(
            {
                "nickname": row["nickname"],
                "bucket_choice": row["bucket_choice"],
                "submitted_at_display": format_timestamp_et(row["submitted_at"]),
            }
        )

    return {
        "game_date_et": result_date.isoformat(),
        "symbol": symbol_code,
        "winning_bucket": winning_bucket,
        "winner_count": len(winners),
        "winners": winners,
        "has_winners": len(winners) > 0,
    }


def build_redirect_url(
    *,
    error_message: str = "",
    success_message: str = "",
) -> str:
    params: dict[str, str] = {}

    if error_message:
        params["error"] = error_message

    if success_message:
        params["success"] = success_message

    if not params:
        return "/"

    return f"/?{urlencode(params)}"


def normalize_latest_result(latest_result: dict | None) -> dict | None:
    if latest_result is None:
        return None

    return {
        "game_date_et": latest_result["game_date_et"].isoformat(),
        "symbol": latest_result["symbol"],
        "winning_bucket": latest_result["winning_bucket"],
        "resolved_at": latest_result["resolved_at"],
        "resolved_at_display": format_timestamp_et(latest_result["resolved_at"]),
        "source_name": latest_result["source_name"],
        "source_version": latest_result["source_version"],
    }


def render_homepage(
    request: Request,
    *,
    error_message: str = "",
    success_message: str = "",
) -> HTMLResponse:
    symbol_code = "GOLD"
    game_date_et = current_et_date()

    ingest_latest_result_file_to_db()

    latest_result = normalize_latest_result(
        get_latest_daily_result(
            symbol_code,
            before_game_date_et=game_date_et,
        )
    )
    latest_result_outcome = build_latest_result_outcome(latest_result)

    todays_guesses_raw = list_daily_guesses(
        symbol_code=symbol_code,
        game_date_et=game_date_et,
    )

    todays_guesses = []
    for guess in todays_guesses_raw:
        todays_guesses.append(
            {
                "nickname": guess["nickname"],
                "bucket_choice": guess["bucket_choice"],
                "submitted_at_display": format_timestamp_et(guess["submitted_at"]),
            }
        )

    recent_results_raw = list_recent_daily_results(
        symbol_code=symbol_code,
        limit=10,
    )

    recent_results = []
    for row in recent_results_raw:
        winner_count = int(row["winner_count"] or 0)
        recent_results.append(
            {
                "game_date_et": row["game_date_et"].isoformat(),
                "symbol": row["symbol"],
                "winning_bucket": row["winning_bucket"],
                "resolved_at_display": format_timestamp_et(row["resolved_at"]),
                "winner_count": winner_count,
                "result_state": "Winners" if winner_count > 0 else "No winners",
                "detail_url": f"/history/{row['game_date_et'].isoformat()}",
            }
        )

    context = {
        "request": request,
        "page_title": "JILT GAME",
        "symbol": symbol_code,
        "bucket_choices": build_bucket_choices(),
        "today_et": game_date_et.isoformat(),
        "error_message": error_message,
        "success_message": success_message,
        "latest_result": latest_result,
        "latest_result_outcome": latest_result_outcome,
        "recent_results": recent_results,
        "todays_guesses": todays_guesses,
    }
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=context,
    )

@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    error_message = request.query_params.get("error", "")
    success_message = request.query_params.get("success", "")

    return render_homepage(
        request,
        error_message=error_message,
        success_message=success_message,
    )


@app.get("/history", response_class=HTMLResponse)
def history(request: Request) -> HTMLResponse:
    symbol_code = "GOLD"

    ingest_latest_result_file_to_db()

    history_rows_raw = list_recent_daily_results(
        symbol_code=symbol_code,
        limit=30,
    )

    history_rows = []
    for row in history_rows_raw:
        winner_count = int(row["winner_count"] or 0)
        history_rows.append(
            {
                "game_date_et": row["game_date_et"].isoformat(),
                "symbol": row["symbol"],
                "winning_bucket": row["winning_bucket"],
                "resolved_at_display": format_timestamp_et(row["resolved_at"]),
                "winner_count": winner_count,
                "result_state": "Winners" if winner_count > 0 else "No winners",
                "detail_url": f"/history/{row['game_date_et'].isoformat()}",
            }
        )

    context = {
        "request": request,
        "page_title": "JILT GAME History",
        "symbol": symbol_code,
        "history_rows": history_rows,
    }
    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context=context,
    )


@app.get("/history/{game_date_et}", response_class=HTMLResponse)
def history_detail(request: Request, game_date_et: str) -> HTMLResponse:
    symbol_code = "GOLD"

    ingest_latest_result_file_to_db()

    try:
        parsed_game_date = date.fromisoformat(game_date_et)
    except ValueError:
        context = {
            "request": request,
            "page_title": "History Detail",
            "symbol": symbol_code,
            "result_row": None,
            "winning_guesses": [],
            "all_guesses": [],
            "error_message": "Invalid game date format.",
        }
        return templates.TemplateResponse(
            request=request,
            name="history_detail.html",
            context=context,
            status_code=404,
        )

    result_row = get_daily_result(
        symbol_code=symbol_code,
        game_date_et=parsed_game_date,
    )

    if result_row is None:
        context = {
            "request": request,
            "page_title": "History Detail",
            "symbol": symbol_code,
            "result_row": None,
            "winning_guesses": [],
            "all_guesses": [],
            "error_message": f"No resolved result found for {game_date_et}.",
        }
        return templates.TemplateResponse(
            request=request,
            name="history_detail.html",
            context=context,
            status_code=404,
        )

    winning_guesses_raw = list_winning_guesses(
        symbol_code=symbol_code,
        game_date_et=parsed_game_date,
        winning_bucket=result_row["winning_bucket"],
    )

    all_guesses_raw = list_daily_guesses(
        symbol_code=symbol_code,
        game_date_et=parsed_game_date,
    )

    winning_guesses = []
    for guess in winning_guesses_raw:
        winning_guesses.append(
            {
                "nickname": guess["nickname"],
                "bucket_choice": guess["bucket_choice"],
                "submitted_at_display": format_timestamp_et(guess["submitted_at"]),
            }
        )

    all_guesses = []
    for guess in all_guesses_raw:
        all_guesses.append(
            {
                "nickname": guess["nickname"],
                "bucket_choice": guess["bucket_choice"],
                "submitted_at_display": format_timestamp_et(guess["submitted_at"]),
            }
        )

    normalized_result_row = {
        "game_date_et": result_row["game_date_et"].isoformat(),
        "symbol": result_row["symbol"],
        "winning_bucket": result_row["winning_bucket"],
        "resolved_at_display": format_timestamp_et(result_row["resolved_at"]),
        "source_name": result_row["source_name"],
        "source_version": result_row["source_version"],
        "winner_count": len(winning_guesses),
        "has_winners": len(winning_guesses) > 0,
    }

    context = {
        "request": request,
        "page_title": f"History Detail {game_date_et}",
        "symbol": symbol_code,
        "result_row": normalized_result_row,
        "winning_guesses": winning_guesses,
        "all_guesses": all_guesses,
        "error_message": "",
    }
    return templates.TemplateResponse(
        request=request,
        name="history_detail.html",
        context=context,
    )


@app.get("/charts", response_class=HTMLResponse)
def charts(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "page_title": "JILT GAME Charts",
        "symbol": "GOLD",
        "charts": get_chart_definitions(),
    }
    return templates.TemplateResponse(
        request=request,
        name="charts.html",
        context=context,
    )


@app.post("/guess")
def submit_guess(
    nickname: str = Form(...),
    bucket_choice: str = Form(...),
):
    nickname = nickname.strip()
    valid_buckets = build_bucket_choices()
    symbol_code = "GOLD"
    game_date_et = current_et_date()

    if not nickname:
        return RedirectResponse(
            url=build_redirect_url(error_message="Nickname is required."),
            status_code=303,
        )

    if bucket_choice not in valid_buckets:
        return RedirectResponse(
            url=build_redirect_url(
                error_message="Invalid 5-minute bucket selection."
            ),
            status_code=303,
        )

    try:
        insert_daily_guess(
            symbol_code=symbol_code,
            game_date_et=game_date_et,
            nickname=nickname,
            bucket_choice=bucket_choice,
        )
    except UniqueViolation:
        return RedirectResponse(
            url=build_redirect_url(
                error_message=(
                    f"{nickname} already submitted a guess for "
                    f"{symbol_code} on {game_date_et.isoformat()} ET."
                )
            ),
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=build_redirect_url(error_message=str(exc)),
            status_code=303,
        )

    success_message = (
        f"Guess saved for {game_date_et.isoformat()} ET: "
        f"{nickname} selected {bucket_choice} for {symbol_code}."
    )

    return RedirectResponse(
        url=build_redirect_url(success_message=success_message),
        status_code=303,
    )
