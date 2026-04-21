# JILT GAME

JILT GAME is a browser-based web application where users predict which 5-minute bucket will contain a symbol's daily low.

## Current scope
- GOLD only for MVP
- browser-based web app
- FastAPI backend
- PostgreSQL storage
- JILT remains the source of truth for official winning buckets
- JILT GAME accepts guesses, stores them, and displays winners, history, and charts

## MVP goals
- homepage for daily bucket guessing
- nickname-based submission
- one guess per nickname per symbol per ET date
- result display after official JILT result is available
- historical winners/results page
- charts page showing JILT chart artifacts

## Project boundary
JILT GAME does not calculate the official winning bucket.
It only consumes JILT-derived official result artifacts.
