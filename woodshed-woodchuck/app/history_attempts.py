"""Server-owned, ordered answers for the existing five-question daily quiz.

Only the current attempt is retained in the existing state JSON. No answer key
is stored there (account state is readable by the student).
Callers hold the profile/state locks and commit answers and payout together.
"""
from copy import deepcopy
import hashlib
import hmac
import json

from .history_mystery import (
    HISTORY_MYSTERY_BANK_VERSION, HISTORY_MYSTERY_QUESTIONS,
    history_mystery_central_date, history_mystery_questions_for_date,
)
from .session_config import session_secret


HISTORY_STATE_KEY = "_history_mystery"


class HistoryAttemptError(ValueError):
    pass


def _signature(state, attempt):
    data = {key: value for key, value in attempt.items() if key != "signature"}
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(session_secret().encode(),
                    f"history-attempt-v1:{state.profile_id}:".encode() + encoded,
                    hashlib.sha256).hexdigest()


def _trusted(state, attempt):
    # Before this release, generic sync could store arbitrary unknown JSON keys.
    # Never promote such pre-existing browser data to trusted quiz progress.
    return (isinstance(attempt, dict) and isinstance(attempt.get("signature"), str)
            and attempt["signature"].isascii()
            and hmac.compare_digest(attempt["signature"], _signature(state, attempt)))


def _write(state, attempt):
    attempt["signature"] = _signature(state, attempt)
    payload = deepcopy(state.state_json or {})
    payload[HISTORY_STATE_KEY] = attempt
    state.state_json = payload
    state.revision += 1


def initialize_history(state, play):
    prior = (state.state_json or {}).get(HISTORY_STATE_KEY) or {}
    if _trusted(state, prior) and prior.get("play_id") == play.id:
        return
    _write(state, {
        "play_id": play.id, "version": HISTORY_MYSTERY_BANK_VERSION,
        "question_ids": [q["id"] for q in history_mystery_questions_for_date(play.daily_play_date)],
        "answers": [],
    })


def _read(state, play):
    attempt = (state.state_json or {}).get(HISTORY_STATE_KEY) or {}
    if (not _trusted(state, attempt) or attempt.get("play_id") != play.id
            or attempt.get("version") != HISTORY_MYSTERY_BANK_VERSION):
        raise HistoryAttemptError("Resume today's quiz before answering.")
    bank = {q["id"]: q for q in HISTORY_MYSTERY_QUESTIONS}
    questions = [bank[key] for key in attempt["question_ids"]]
    return attempt, questions


def history_score(state, play, *, require_finished=False):
    attempt, questions = _read(state, play)
    answers = attempt["answers"]
    if require_finished and len(answers) != len(questions):
        raise HistoryAttemptError("Answer all five questions before completing the quiz.")
    return sum(choice == question["answer"] for choice, question in zip(answers, questions))


def history_snapshot(state, play):
    attempt, questions = _read(state, play)
    count = len(attempt["answers"])
    question = questions[min(count, len(questions) - 1)]
    return {
        "question_index": min(count, len(questions) - 1),
        "finished": count == len(questions), "score": history_score(state, play),
        "question": {key: question[key] for key in ("id", "category", "prompt", "choices")},
        "play_date": play.daily_play_date.isoformat(),
    }


def accept_history_answer(state, play, *, question_index, choice, now):
    if play.daily_play_date != history_mystery_central_date(now):
        raise HistoryAttemptError("That daily quiz has expired. Start today's quiz.")
    attempt, questions = _read(state, play)
    if type(question_index) is not int or not 0 <= question_index < len(questions):
        raise HistoryAttemptError("Choose the current question.")
    question = questions[question_index]
    answers = attempt["answers"]
    if question_index < len(answers):
        if answers[question_index] != choice:
            raise HistoryAttemptError("That question already has an answer.")
    else:
        if play.completed_at is not None or question_index != len(answers):
            raise HistoryAttemptError("Choose the current question.")
        if choice not in question["choices"]:
            raise HistoryAttemptError("Choose one of the available answers.")
        updated = deepcopy(attempt)
        updated["answers"].append(choice)
        _write(state, updated)
    return {
        "history": history_snapshot(state, play),
        "answer_result": {"question_index": question_index, "correct": choice == question["answer"],
                          "answer": question["answer"], "fact": question["fact"]},
    }
