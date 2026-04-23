from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.errors import UniqueViolation

from app.buckets import (
    get_bucket_choices,
    get_bucket_definitions,
    get_buckets_grouped_by_hour,
)
from app.charts import get_chart_definitions
from app.config import JILT_CHARTS_DIR
from app.db import (
    get_daily_result,
    get_daily_result_outcome,
    get_latest_daily_result,
    insert_two_day_guess,
    list_closest_winners,
    list_daily_guesses,
    list_hall_of_famers,
    list_recent_daily_results,
    list_winning_guesses,
    count_bucket_guesses,
)
from app.result_ingest import ingest_latest_result_file_to_db

BASE_DIR = Path(__file__).resolve().parent.parent
ET_TZ = ZoneInfo("America/New_York")
MIN_NICKNAME_LENGTH = 2
MAX_NICKNAME_LENGTH = 20
MAX_USERS_PER_BUCKET = 10

app = FastAPI(title="JILT GAME")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/chart-files", StaticFiles(directory=JILT_CHARTS_DIR), name="chart-files")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def build_bucket_choices() -> list[str]:
    return get_bucket_choices()


def current_et_date() -> date:
    return datetime.now(ET_TZ).date()


def validate_nickname_length(nickname: str) -> str:
    if len(nickname) < MIN_NICKNAME_LENGTH or len(nickname) > MAX_NICKNAME_LENGTH:
        return (
            f"Nickname must be between {MIN_NICKNAME_LENGTH} "
            f"and {MAX_NICKNAME_LENGTH} characters."
        )
    return ""


BLOCKED_NICKNAMES = {
    "admin",
    "administrator",
    "moderator",
    "system",
    "support",
    "null",
    "undefined",
}


def validate_nickname_allowed(nickname: str) -> str:
    normalized = nickname.strip().lower()
    if normalized in BLOCKED_NICKNAMES:
        return "That nickname is not allowed."
    return ""


def format_timestamp_et(value: datetime) -> str:
    et_value = value.astimezone(ET_TZ)
    return et_value.strftime("%Y-%m-%d %I:%M:%S %p ET")


def build_redirect_url(
    *,
    error_message: str = "",
    success_message: str = "",
    green_button_error: str = "",
    green_button_success: str = "",
) -> str:
    params: dict[str, str] = {}

    if error_message:
        params["error"] = error_message

    if success_message:
        params["success"] = success_message

    if green_button_error:
        params["green_error"] = green_button_error

    if green_button_success:
        params["green_success"] = green_button_success

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


def build_buckets_by_hour_with_today_guesses(todays_guesses_raw: list[dict]) -> list[dict]:
    buckets_by_hour = get_buckets_grouped_by_hour()

    guesses_by_bucket: dict[str, list[str]] = {}
    for guess in todays_guesses_raw:
        bucket_choice = guess["bucket_choice"]
        nickname = guess["nickname"]
        guesses_by_bucket.setdefault(bucket_choice, []).append(nickname)

    for hour_group in buckets_by_hour:
        for bucket in hour_group["buckets"]:
            bucket["nicknames"] = guesses_by_bucket.get(bucket["bucket_time"], [])

    return buckets_by_hour


def render_homepage(
    request: Request,
    *,
    error_message: str = "",
    success_message: str = "",
    green_button_error: str = "",
    green_button_success: str = "",
) -> HTMLResponse:
    symbol_code = "GOLD"
    game_date_et = current_et_date()

    ingest_latest_result_file_to_db()

    latest_result_raw = get_latest_daily_result(
        symbol_code,
        before_game_date_et=game_date_et,
    )
    latest_result = normalize_latest_result(latest_result_raw)

    latest_result_outcome = None
    if latest_result_raw is not None:
        latest_result_outcome = get_daily_result_outcome(
            symbol_code=symbol_code,
            game_date_et=latest_result_raw["game_date_et"],
        )

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

    closest_winners_raw = list_closest_winners(
        symbol_code=symbol_code,
        limit=100,
    )
    closest_winners = []
    for row in closest_winners_raw:
        closest_winners.append(
            {
                "game_date_et": row["game_date_et"].isoformat(),
                "nickname": row["nickname"],
                "bucket_choice": row["bucket_choice"],
                "chaperone": row.get("chaperone", ""),
                "distance_buckets": row["distance_buckets"],
                "submitted_at_display": format_timestamp_et(row["submitted_at"]),
            }
        )

    hall_of_famers_raw = list_hall_of_famers(
        symbol_code=symbol_code,
        limit=100,
    )
    hall_of_famers = []
    for row in hall_of_famers_raw:
        hall_of_famers.append(
            {
                "game_date_et": row["game_date_et"].isoformat(),
                "nickname": row["nickname"],
                "bucket_choice": row["bucket_choice"],
                "submitted_at_display": format_timestamp_et(row["submitted_at"]),
            }
        )

    context = {
        "request": request,
        "page_title": "JILT GAME",
        "symbol": symbol_code,
        "today_et": game_date_et.isoformat(),
        "error_message": error_message,
        "success_message": success_message,
        "green_button_error": green_button_error,
        "green_button_success": green_button_success,
        "latest_result": latest_result,
        "latest_result_outcome": latest_result_outcome,
        "todays_guesses": todays_guesses,
        "bucket_choices": build_bucket_choices(),
        "bucket_definitions": get_bucket_definitions(),
        "buckets_by_hour": build_buckets_by_hour_with_today_guesses(todays_guesses_raw),
        "closest_winners": closest_winners,
        "hall_of_famers": hall_of_famers,
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
    green_button_error = request.query_params.get("green_error", "")
    green_button_success = request.query_params.get("green_success", "")

    return render_homepage(
        request,
        error_message=error_message,
        success_message=success_message,
        green_button_error=green_button_error,
        green_button_success=green_button_success,
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
    nickname_error = validate_nickname_length(nickname)
    if nickname_error:
        return RedirectResponse(
            url=build_redirect_url(error_message=nickname_error),
            status_code=303,
        )

    nickname_block_error = validate_nickname_allowed(nickname)
    if nickname_block_error:
        return RedirectResponse(
            url=build_redirect_url(error_message=nickname_block_error),
            status_code=303,
        )

    valid_buckets = build_bucket_choices()
    symbol_code = "GOLD"
    today_et = current_et_date()
    tomorrow_et = today_et + timedelta(days=1)

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

    today_bucket_guess_count = count_bucket_guesses(
        symbol_code=symbol_code,
        game_date_et=today_et,
        bucket_choice=bucket_choice,
    )
    if today_bucket_guess_count >= MAX_USERS_PER_BUCKET:
        return RedirectResponse(
            url=build_redirect_url(
                error_message="That bucket is full for today."
            ),
            status_code=303,
        )

    tomorrow_bucket_guess_count = count_bucket_guesses(
        symbol_code=symbol_code,
        game_date_et=tomorrow_et,
        bucket_choice=bucket_choice,
    )
    if tomorrow_bucket_guess_count >= MAX_USERS_PER_BUCKET:
        return RedirectResponse(
            url=build_redirect_url(
                error_message="That bucket is full for tomorrow."
            ),
            status_code=303,
        )

    try:
        insert_two_day_guess(
            symbol_code=symbol_code,
            game_date_et=today_et,
            nickname=nickname,
            bucket_choice=bucket_choice,
        )
    except UniqueViolation:
        return RedirectResponse(
            url=build_redirect_url(
                error_message=(
                    f"{nickname} already submitted a guess for "
                    f"{symbol_code} for today and tomorrow ET."
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
        f"Guess saved for {today_et.isoformat()} ET and "
        f"{tomorrow_et.isoformat()} ET: "
        f"{nickname} selected {bucket_choice} for {symbol_code}."
    )

    return RedirectResponse(
        url=build_redirect_url(success_message=success_message),
        status_code=303,
    )


@app.post("/guess/current")
def submit_current_bucket_guess(
    nickname: str = Form(...),
):
    nickname = nickname.strip()

    nickname_error = validate_nickname_length(nickname)
    if nickname_error:
        return RedirectResponse(
            url=build_redirect_url(green_button_error=nickname_error),
            status_code=303,
        )

    nickname_block_error = validate_nickname_allowed(nickname)
    if nickname_block_error:
        return RedirectResponse(
            url=build_redirect_url(green_button_error=nickname_block_error),
            status_code=303,
        )

    symbol_code = "GOLD"
    today_et = current_et_date()
    tomorrow_et = today_et + timedelta(days=1)
    current_time_et = datetime.now(ET_TZ)
    current_minute_bucket = (current_time_et.minute // 5) * 5
    bucket_choice = f"{current_time_et.hour:02d}:{current_minute_bucket:02d}"

    if not nickname:
        return RedirectResponse(
            url=build_redirect_url(green_button_error="Nickname is required."),
            status_code=303,
        )

    today_bucket_guess_count = count_bucket_guesses(
        symbol_code=symbol_code,
        game_date_et=today_et,
        bucket_choice=bucket_choice,
    )
    if today_bucket_guess_count >= MAX_USERS_PER_BUCKET:
        return RedirectResponse(
            url=build_redirect_url(
                green_button_error="That bucket is full for today."
            ),
            status_code=303,
        )

    tomorrow_bucket_guess_count = count_bucket_guesses(
        symbol_code=symbol_code,
        game_date_et=tomorrow_et,
        bucket_choice=bucket_choice,
    )
    if tomorrow_bucket_guess_count >= MAX_USERS_PER_BUCKET:
        return RedirectResponse(
            url=build_redirect_url(
                green_button_error="That bucket is full for tomorrow."
            ),
            status_code=303,
        )

    try:
        insert_two_day_guess(
            symbol_code=symbol_code,
            game_date_et=today_et,
            nickname=nickname,
            bucket_choice=bucket_choice,
        )
    except UniqueViolation:
        return RedirectResponse(
            url=build_redirect_url(
                green_button_error=(
                    f"{nickname} already submitted a guess for "
                    f"{symbol_code} for today and tomorrow ET."
                )
            ),
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=build_redirect_url(green_button_error=str(exc)),
            status_code=303,
        )

    success_message = (
        f"Current bucket locked in for {today_et.isoformat()} ET and "
        f"{tomorrow_et.isoformat()} ET: "
        f"{nickname} selected {bucket_choice} for {symbol_code}."
    )

    return RedirectResponse(
        url=build_redirect_url(green_button_success=success_message),
        status_code=303,
    )


