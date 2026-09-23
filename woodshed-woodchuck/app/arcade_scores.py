from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ArcadeHighScore, ArcadePlaySession, WoodchuckProfile


def publishable_attempt_bests(session, game_key):
    """Public best can be lower than a lifetime best earned while private."""
    from .age_privacy import can_publish
    from .age_models import AccountPrivacy
    rows = session.execute(select(ArcadePlaySession.profile_id, func.max(ArcadePlaySession.submitted_score))
        .join(AccountPrivacy, AccountPrivacy.profile_id == ArcadePlaySession.profile_id)
        .where(ArcadePlaySession.game_key == game_key, ArcadePlaySession.submitted_score > 0,
               AccountPrivacy.age_band.in_(['13to17', 'adult']),
               ArcadePlaySession.started_at >= AccountPrivacy.public_from,
               ArcadePlaySession.completed_at >= AccountPrivacy.public_from)
        .group_by(ArcadePlaySession.profile_id))
    # Reuse the shared current-consent gate as well as both historical boundaries.
    return {pid: score for pid, score in rows if can_publish(session, pid)}


ARCADE_GAME_KEYS = frozenset({
    "blue", "radio-tuner", "wheel-of-woodchuck", "scale-keyboard", "thirds",
    "dressed-to-the-nines",
    "interval-basic-training",
    "history-mystery",
})
MAX_ARCADE_SCORE = 2_147_483_647


def validate_game_key(game_key: str) -> str:
    if game_key not in ARCADE_GAME_KEYS:
        raise ValueError("That Arcade game is unavailable.")
    return game_key


def record_arcade_high_score(
    session: Session,
    *,
    profile_id: int,
    game_key: str,
    score: int,
) -> tuple[int, bool]:
    key = validate_game_key(game_key)
    if type(score) is not int or not 0 <= score <= MAX_ARCADE_SCORE:
        raise ValueError("A valid Arcade score is required.")

    locked_profile = session.scalar(
        select(WoodchuckProfile.id)
        .where(
            WoodchuckProfile.id == profile_id,
            WoodchuckProfile.status == "active",
        )
        .with_for_update()
    )
    if locked_profile is None:
        raise ValueError("The signed-in Woodchuck profile is unavailable.")

    row = session.scalar(
        select(ArcadeHighScore)
        .where(
            ArcadeHighScore.profile_id == profile_id,
            ArcadeHighScore.game_key == key,
        )
        .with_for_update()
    )
    updated = False
    if row is None:
        row = ArcadeHighScore(
            profile_id=profile_id,
            game_key=key,
            best_score=score,
        )
        session.add(row)
        updated = True
    elif score > row.best_score:
        row.best_score = score
        updated = True
    session.flush()
    return int(row.best_score), updated


def arcade_score_payload(
    session: Session,
    *,
    profile_id: int,
    game_key: str,
) -> dict[str, object]:
    key = validate_game_key(game_key)
    current_profile = session.scalar(
        select(WoodchuckProfile.id).where(
            WoodchuckProfile.id == profile_id,
            WoodchuckProfile.status == "active",
        )
    )
    if current_profile is None:
        raise ValueError("The signed-in Woodchuck profile is unavailable.")

    current_score = session.scalar(
        select(ArcadeHighScore.best_score).where(
            ArcadeHighScore.profile_id == profile_id,
            ArcadeHighScore.game_key == key,
        )
    ) or 0
    rows = session.execute(
        select(ArcadeHighScore, WoodchuckProfile)
        .join(
            WoodchuckProfile,
            WoodchuckProfile.id == ArcadeHighScore.profile_id,
        )
        .where(
            ArcadeHighScore.game_key == key,
            ArcadeHighScore.best_score > 0,
            WoodchuckProfile.status == "active",
        )
        .order_by(
            ArcadeHighScore.best_score.desc(),
            func.lower(WoodchuckProfile.display_name),
            WoodchuckProfile.display_name,
            WoodchuckProfile.id,
        )
    ).all()

    public_bests = publishable_attempt_bests(session, key)
    visible = []
    for score, profile in rows:
        # Aggregate timestamps do not establish when the scoring attempt began.
        value = (score.best_score if profile.id == profile_id
                 else public_bests.get(profile.id, 0))
        if value > 0:
            visible.append((value, profile))
    rows = sorted(visible, key=lambda row: (-row[0], row[1].display_name.lower(), row[1].display_name, row[1].id))
    leaderboard: list[dict[str, object]] = []
    prior_score: int | None = None
    rank = 0
    for position, (score_value, profile) in enumerate(rows, start=1):
        if score_value != prior_score:
            rank = position
            prior_score = score_value
        leaderboard.append({
            "rank": rank,
            "display_name": " ".join(profile.display_name.split()) or "Woodchuck",
            "score": score_value,
            "is_current_user": profile.id == profile_id,
        })

    return {
        "game_key": key,
        "best_score": int(current_score),
        "leaderboard": leaderboard[:5],
    }
