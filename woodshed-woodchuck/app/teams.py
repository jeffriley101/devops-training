from __future__ import annotations
from .age_privacy import can_publish

from datetime import datetime, time, timedelta, timezone
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_routes import current_profile
from .contests import CENTRAL, central_week_boundaries, ensure_current_contest_data
from .db import SessionLocal
from .age_privacy import public_team_identity_allowed, sharing_allowed
from .models import (
    ProfileCapability,
    Season,
    Team,
    TeamFamily,
    TeamJoinRequest,
    TeamMembership,
    TeamMembershipTransition,
    TeamReport,
    WoodchuckProfile,
)
from .team_names import InvalidTeamName, normalized_team_name
from .team_continuity import lock_team_seasons
from .team_name_claims import claim_public_team_name, TEAM_NAME_TAKEN
from .team_authority import effective_membership, lock_authority, persistent_enabled
from .seasons import season_covering_date


EMOJI_EMBLEMS = {
    "emoji:lion": "🦁", "emoji:goat": "🐐", "emoji:bear": "🐻",
    "emoji:eagle": "🦅", "emoji:wolf": "🐺", "emoji:bee": "🐝",
    "emoji:dragon": "🐉", "emoji:cat": "🐱", "emoji:dog": "🐶",
    "emoji:star": "⭐", "emoji:fire": "🔥", "emoji:moon": "🌙",
    "emoji:lightning": "⚡",
}
LETTER_EMBLEMS = {f"letter:{letter}": letter for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
SHIELD_EMBLEMS = {f"shield:{color}": color.title() for color in (
    "blue", "red", "green", "gold", "purple", "orange", "black", "silver"
)}
APPROVED_EMBLEMS = {**EMOJI_EMBLEMS, **LETTER_EMBLEMS, **SHIELD_EMBLEMS}

router = APIRouter(prefix="/teams", tags=["teams"])


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    emblem_key: str = Field(min_length=1, max_length=50)


class TeamJoin(BaseModel):
    team_id: int = Field(gt=0)


class PrivateTeamJoin(BaseModel):
    join_code: str = Field(min_length=4, max_length=32)


class JoinRequestDecision(BaseModel):
    action: str = Field(min_length=1, max_length=20)


class TeamReportCreate(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    details: str = Field(default="", max_length=500)


REPORT_CATEGORIES = {
    "inappropriate_name": "Inappropriate name",
    "inappropriate_emblem": "Inappropriate emblem",
    "impersonation": "Impersonation",
    "other": "Other",
}


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def emblem_payload(key: str) -> dict[str, str]:
    kind, value = key.split(":", 1)
    return {"key": key, "kind": kind, "value": APPROVED_EMBLEMS[key]}


def public_team_identity(team: Team) -> tuple[str, dict[str, str]]:
    if team.moderation_status == "hidden":
        return "Hidden Team", emblem_payload("shield:silver")
    return team.display_name, emblem_payload(team.emblem_key if team.emblem_key in APPROVED_EMBLEMS else "shield:silver")


def team_payload(team: Team) -> dict[str, object]:
    name, emblem = public_team_identity(team)
    return {
        "id": team.id, "name": name, "emblem": emblem,
        "visibility": team.visibility,
        "director_led": team.director_led,
    }


def active_membership(session: Session, *, profile_id: int, season_id: int,
                      at: datetime | None = None) -> TeamMembership | None:
    moment = at or datetime.now(timezone.utc)
    if persistent_enabled(session, moment):
        return effective_membership(session, profile_id, moment)
    return session.scalar(select(TeamMembership).where(
        TeamMembership.profile_id == profile_id,
        TeamMembership.season_id == season_id,
        TeamMembership.ended_at.is_(None),
    ))


def has_band_director_capability(session: Session, *, profile_id: int) -> bool:
    return session.scalar(select(ProfileCapability.id).where(
        ProfileCapability.profile_id == profile_id,
        ProfileCapability.capability == "band_director",
    )) is not None


def membership_at(session: Session, *, profile_id: int, season_id: int, at: datetime) -> TeamMembership | None:
    moment = utc(at)
    if persistent_enabled(session, moment):
        return effective_membership(session, profile_id, moment)
    rows = session.scalars(select(TeamMembership).where(
        TeamMembership.profile_id == profile_id,
        TeamMembership.season_id == season_id,
        TeamMembership.started_at <= moment,
    ).order_by(TeamMembership.started_at.desc())).all()
    return next((row for row in rows if row.ended_at is None or utc(row.ended_at) > moment), None)


def _season_id(season: Season | None) -> int | None:
    return season.id if season is not None else None


def _team_is_current(session: Session, team: Team, season_id: int, now: datetime) -> bool:
    return bool(team.is_operating) if persistent_enabled(session, now) else team.season_id == season_id


def _lock_team_writer(session: Session, season_id: int | None):
    lock_authority(session)
    if season_id is not None:
        lock_team_seasons(session, season_id)


def _lock_profile(session: Session, profile: WoodchuckProfile):
    session.scalar(select(WoodchuckProfile).where(
        WoodchuckProfile.id == profile.id
    ).with_for_update().execution_options(populate_existing=True))
    if profile.status != "active":
        raise ValueError("This account cannot select a Team.")


def persistent_correction_state(session: Session, profile_id: int, now: datetime):
    """Count actual transitions; leaving never replenishes the same week's cap."""
    week_start, _, _, _ = central_week_boundaries(now)
    boundary = datetime.combine(week_start, time.min, CENTRAL).astimezone(timezone.utc)
    # The carried roster is the instant BEFORE student transitions at Monday.
    # A switch exactly at Monday must count against the carried membership;
    # an initial join exactly at Monday must remain an initial choice.
    carried = effective_membership(session, profile_id, boundary - timedelta(microseconds=1))
    used = session.scalar(select(func.count(TeamMembershipTransition.id)).where(
        TeamMembershipTransition.profile_id == profile_id,
        TeamMembershipTransition.week_start == week_start,
    )) or 0
    allowance = 1 if carried is not None else 2
    return week_start, used, allowance


def _persistent_transition(session: Session, *, profile: WoodchuckProfile,
                           season: Season | None, team: Team | None, now: datetime):
    _lock_profile(session, profile)
    current = effective_membership(session, profile.id, now)
    if current is not None and team is not None and current.team_id == team.id:
        return current, False
    if current is None and team is None:
        return None, False
    current_team = session.get(Team, current.team_id) if current else None
    week_start, used, allowance = persistent_correction_state(session, profile.id, now)
    if used >= allowance and (current_team is None or current_team.moderation_status != "hidden"):
        raise ValueError("Your team choice is locked until next contest week.")
    moment = utc(now)
    if current is not None:
        current.ended_at = moment
        session.flush()
    membership = None
    if team is not None:
        membership = TeamMembership(
            season_id=None, team_id=team.id, profile_id=profile.id,
            is_persistent=True, selected_week_start=week_start, started_at=moment,
        )
        session.add(membership)
        session.flush()
    session.add(TeamMembershipTransition(
        profile_id=profile.id, week_start=week_start, occurred_at=moment,
        action="leave" if team is None else ("switch" if current else "join"),
        from_membership_id=current.id if current else None,
        to_membership_id=membership.id if membership else None,
    ))
    session.flush()
    return membership, True


def leave_team(session: Session, *, profile: WoodchuckProfile, season: Season | None,
               now: datetime) -> bool:
    _lock_team_writer(session, _season_id(season))
    if not persistent_enabled(session, now):
        raise ValueError("Persistent Team membership is not active.")
    _, changed = _persistent_transition(session, profile=profile, season=season, team=None, now=now)
    return changed


def select_team(session: Session, *, profile: WoodchuckProfile, season: Season | None,
                team: Team, now: datetime,
                private_authorized: bool = False) -> tuple[TeamMembership, bool]:
    _lock_team_writer(session, _season_id(season))
    session.refresh(team)
    if not _team_is_current(session, team, _season_id(season), now):
        raise ValueError("That team is not available.")
    if team.moderation_status == "hidden":
        raise ValueError("That team is not available.")
    if team.visibility == "private" and not private_authorized:
        raise ValueError("That Class requires director approval.")
    if persistent_enabled(session, now):
        return _persistent_transition(session, profile=profile, season=season, team=team, now=now)
    week_start, _, _, _ = central_week_boundaries(now)
    current = active_membership(session, profile_id=profile.id, season_id=_season_id(season))
    if current and current.team_id == team.id:
        return current, False
    current_team = session.get(Team, current.team_id) if current else None
    week_membership_count = session.scalar(select(func.count(TeamMembership.id)).where(
        TeamMembership.profile_id == profile.id,
        TeamMembership.season_id == _season_id(season),
        TeamMembership.selected_week_start == week_start,
    )) or 0
    if (
        current and week_membership_count >= 2
        and (current_team is None or current_team.moderation_status != "hidden")
    ):
        raise ValueError("Your team choice is locked until next contest week.")
    now_utc = now.astimezone(timezone.utc)
    if current:
        current.ended_at = now_utc
        session.flush()
    membership = TeamMembership(
        season_id=_season_id(season), team_id=team.id, profile_id=profile.id,
        selected_week_start=week_start, started_at=now_utc,
    )
    session.add(membership)
    session.flush()
    return membership, True


def _create_team_with_new_family(session: Session, **team_fields: object) -> Team:
    """Create initial identity within the caller's transaction; never commit."""
    family = TeamFamily(**(
        {"created_at": team_fields["created_at"]} if "created_at" in team_fields else {}
    ))
    session.add(family)
    session.flush()
    if team_fields.get("visibility", "public") == "public":
        claim_public_team_name(session, normalized_name=team_fields["normalized_name"], family_id=family.id)
    team = Team(family_id=family.id, **team_fields)
    session.add(team)
    return team


def _creation_conflict_message(error: IntegrityError, *, classroom: bool = False) -> str:
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", "") or ""
    detail = str(error.orig)
    if constraint in {"uq_team_season_name", "uq_team_operating_name"} or "teams.season_id, teams.normalized_name" in detail or "teams.normalized_name" in detail:
        return "That Class name is already taken." if classroom else TEAM_NAME_TAKEN
    if constraint == "uq_team_season_emblem" or "teams.season_id, teams.emblem_key" in detail:
        return "That emblem is already in use this season."
    if constraint == "uq_team_operating_emblem" or "teams.emblem_key" in detail:
        return "That emblem is already in use."
    return "That Class could not be created. Please try again." if classroom else "That Team could not be created. Please try again."


def create_and_join_team(session: Session, *, profile: WoodchuckProfile,
                         season: Season | None, name: str, emblem_key: str,
                         now: datetime) -> tuple[Team, TeamMembership]:
    _lock_team_writer(session, _season_id(season))
    persistent = persistent_enabled(session, now)
    if persistent:
        _lock_profile(session, profile)
    if emblem_key not in APPROVED_EMBLEMS:
        raise ValueError("Choose an approved team emblem.")
    try:
        display, normalized = normalized_team_name(name)
    except InvalidTeamName as error:
        raise ValueError(str(error)) from error
    if session.scalar(select(Team.id).where(
        Team.is_operating.is_(True) if persistent else Team.season_id == _season_id(season),
        Team.creator_profile_id == profile.id,
        Team.visibility == "public",
    )):
        raise ValueError("You may create only one Team." if persistent else "You may create only one team per season.")
    try:
        team = _create_team_with_new_family(
            session, season_id=None if persistent else _season_id(season), display_name=display, normalized_name=normalized,
            emblem_key=emblem_key, creator_profile_id=profile.id,
            is_operating=persistent,
        )
        session.flush()
        membership, _ = select_team(session, profile=profile, season=season, team=team, now=now)
        session.commit(); session.refresh(team); session.refresh(membership)
    except IntegrityError as error:
        session.rollback()
        raise ValueError(_creation_conflict_message(error)) from error
    except Exception:
        session.rollback()
        raise
    return team, membership


def _new_join_code(session: Session) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if session.scalar(select(Team.id).where(Team.join_code == code)) is None:
            return code
    raise RuntimeError("A private team code could not be generated.")


def create_director_team(
    session: Session, *, profile: WoodchuckProfile, season: Season | None,
    name: str, emblem_key: str, now: datetime,
) -> Team:
    _lock_team_writer(session, _season_id(season))
    persistent = persistent_enabled(session, now)
    if persistent:
        _lock_profile(session, profile)
    if not has_band_director_capability(session, profile_id=profile.id):
        raise PermissionError("Band Director authorization is required.")
    if emblem_key not in APPROVED_EMBLEMS:
        raise ValueError("Choose an approved team emblem.")
    try:
        display, normalized = normalized_team_name(name)
    except InvalidTeamName as error:
        raise ValueError(str(error)) from error
    try:
        team = _create_team_with_new_family(
            session,
            season_id=None if persistent else _season_id(season),
            display_name=display,
            normalized_name=normalized,
            emblem_key=emblem_key,
            creator_profile_id=profile.id,
            visibility="private",
            director_led=True,
            is_operating=persistent,
            join_code=_new_join_code(session),
            created_at=now.astimezone(timezone.utc),
        )
        session.commit()
        session.refresh(team)
    except IntegrityError as error:
        session.rollback()
        raise ValueError(_creation_conflict_message(error, classroom=True)) from error
    except Exception:
        session.rollback()
        raise
    return team


def selection_payload(session: Session, *, profile: WoodchuckProfile, now: datetime) -> dict[str, object]:
    persistent = persistent_enabled(session, now)
    if persistent:
        season = season_covering_date(session, utc(now).astimezone(CENTRAL).date())
        week_start, week_end, _, _ = central_week_boundaries(now)
    else:
        season, _, week = ensure_current_contest_data(session, now=now)
        week_start, week_end = week.week_start, week.week_end
    membership = active_membership(session, profile_id=profile.id, season_id=_season_id(season), at=now)
    teams = session.scalars(select(Team).where(
        Team.is_operating.is_(True) if persistent else Team.season_id == _season_id(season),
        Team.moderation_status != "hidden",
        Team.visibility == "public",
    ).order_by(
        Team.display_name, Team.id
    )).all()
    current_team = session.get(Team, membership.team_id) if membership else None
    week_membership_count = session.scalar(select(func.count(TeamMembership.id)).where(
        TeamMembership.profile_id == profile.id,
        TeamMembership.season_id == _season_id(season),
        TeamMembership.selected_week_start == week_start,
    )) or 0
    locked = bool(
        membership and week_membership_count >= 2
        and (current_team is None or current_team.moderation_status != "hidden")
    )
    if persistent:
        _, used, allowance = persistent_correction_state(session, profile.id, now)
        locked = used >= allowance and (current_team is None or current_team.moderation_status != "hidden")
    next_at = datetime.combine(week_end, time.min, CENTRAL).astimezone(timezone.utc)
    return {
        "season": {"key": season.key, "name": season.name} if season else {"key": None, "name": "Current"},
        "teams": [
            {key: value for key, value in team_payload(team).items() if key in {"id", "name", "emblem"}}
            for team in teams if public_team_identity_allowed(team)
        ],
        "membership": {
            "team": team_payload(current_team) if current_team else None,
            "selected_week_start": membership.selected_week_start.isoformat() if membership else None,
            "locked": locked,
            "correction_available": bool(membership and not locked),
            "leave_available": bool(persistent and membership and not locked),
            "correction_message": (
                "Your team correction has been used for this week. You can choose again next Monday."
                if locked else "You have one team correction available this week."
            ) if membership or locked else "Choose a team to get started.",
            "next_change_at": next_at.isoformat() if locked else None,
        },
        "pending_private_request": _pending_request_payload(
            session, profile_id=profile.id, season_id=_season_id(season), now=now
        ),
        "band_director": has_band_director_capability(
            session, profile_id=profile.id
        ),
        "approved_emblems": [emblem_payload(key) for key in APPROVED_EMBLEMS],
    }


def _pending_request_payload(
    session: Session, *, profile_id: int, season_id: int, now: datetime | None = None
) -> dict[str, object] | None:
    request_row = session.scalar(select(TeamJoinRequest).where(
        TeamJoinRequest.profile_id == profile_id,
        TeamJoinRequest.is_persistent.is_(True) if persistent_enabled(session, now) else TeamJoinRequest.season_id == season_id,
        TeamJoinRequest.status == "pending",
    ))
    if request_row is None:
        return None
    team = session.get(Team, request_row.team_id)
    if team is None or team.moderation_status == "hidden":
        return None
    name, emblem = public_team_identity(team)
    return {
        "id": request_row.id,
        "status": "pending",
        "team": {"id": team.id, "name": name, "emblem": emblem},
    }


def _owned_director_team(
    session: Session, *, profile: WoodchuckProfile, team_id: int | None = None,
    now: datetime | None = None,
) -> Team:
    if not has_band_director_capability(session, profile_id=profile.id):
        raise PermissionError("Band Director authorization is required.")
    filters = [
        Team.creator_profile_id == profile.id,
        Team.director_led.is_(True),
        Team.visibility == "private",
    ]
    if persistent_enabled(session, now):
        filters.append(Team.is_operating.is_(True))
    if team_id is not None:
        filters.append(Team.id == team_id)
    team = session.scalar(select(Team).where(*filters))
    if team is None:
        raise LookupError("Director-led team was not found.")
    return team


def director_team_payload(
    session: Session, *, profile: WoodchuckProfile, season: Season | None,
    team_id: int | None = None, now: datetime | None = None,
) -> dict[str, object]:
    authorized = has_band_director_capability(session, profile_id=profile.id)
    if not authorized:
        raise PermissionError("Band Director authorization is required.")
    moment = now or datetime.now(timezone.utc)
    persistent = persistent_enabled(session, moment)
    teams = list(session.scalars(select(Team).where(
        Team.is_operating.is_(True) if persistent else Team.season_id == _season_id(season),
        Team.creator_profile_id == profile.id,
        Team.director_led.is_(True),
        Team.visibility == "private",
    ).order_by(Team.display_name, Team.id)).all())
    team = next((row for row in teams if row.id == team_id), None) if team_id else (
        teams[0] if teams else None
    )
    if team_id is not None and team is None:
        raise LookupError("Director-led team was not found.")
    if team is None:
        return {
            "authorized": True, "team": None, "teams": [],
            "approved_emblems": [emblem_payload(key) for key in APPROVED_EMBLEMS],
        }
    membership_rows = session.execute(
        select(TeamMembership, WoodchuckProfile)
        .join(WoodchuckProfile, WoodchuckProfile.id == TeamMembership.profile_id)
        .where(
            TeamMembership.team_id == team.id,
            (or_(TeamMembership.ended_at.is_(None), TeamMembership.ended_at > moment)
             if persistent else TeamMembership.ended_at.is_(None)),
            WoodchuckProfile.status == "active",
        ).order_by(WoodchuckProfile.display_name, WoodchuckProfile.id)
    ).all()
    if persistent:
        membership_rows = [(row, member) for row, member in membership_rows
                           if row.is_persistent and utc(row.started_at) <= utc(moment)]
    request_rows = session.execute(
        select(TeamJoinRequest, WoodchuckProfile)
        .join(WoodchuckProfile, WoodchuckProfile.id == TeamJoinRequest.profile_id)
        .where(
            TeamJoinRequest.team_id == team.id,
            TeamJoinRequest.status == "pending",
            WoodchuckProfile.status == "active",
        ).order_by(TeamJoinRequest.requested_at, TeamJoinRequest.id)
    ).all()
    if persistent:
        request_rows = [(row, member) for row, member in request_rows if row.is_persistent]
    name, emblem = public_team_identity(team)
    return {
        "authorized": True,
        "teams": [
            {
                "id": row.id,
                "name": public_team_name,
                "emblem": emblem_payload(row.emblem_key),
            }
            for row in teams
            for public_team_name in [public_team_identity(row)[0]]
        ],
        "team": {
            "id": team.id, "name": name, "emblem": emblem,
            "visibility": team.visibility, "director_led": True,
            "join_code": team.join_code,
            "director_is_playing_member": any(
                member.profile_id == profile.id for member, _ in membership_rows
            ),
            "members": [
                {"profile_id": member.profile_id, "display_name": member_profile.display_name}
                for member, member_profile in membership_rows if sharing_allowed(session, member_profile.id)
            ],
            "pending_requests": [
                {"id": join_request.id, "profile_id": join_request.profile_id,
                 "display_name": request_profile.display_name,
                 "requested_at": join_request.requested_at.isoformat()}
                for join_request, request_profile in request_rows if sharing_allowed(session, request_profile.id)
            ],
        },
        "approved_emblems": [emblem_payload(key) for key in APPROVED_EMBLEMS],
    }


def authenticated_context(request: Request, session: Session, now: datetime | None = None):
    profile = current_profile(request, session)
    if profile is None:
        raise HTTPException(status_code=401, detail="Student sign-in is required.")
    if request.method != "GET":
        # A mutation that waited for cutover must take its timestamp AFTER
        # acquiring the fence, otherwise it could select legacy writers using
        # a pre-cutover timestamp against newly promoted authority.
        lock_authority(session)
    moment = now or datetime.now(timezone.utc)
    if persistent_enabled(session, moment):
        season = season_covering_date(session, utc(moment).astimezone(CENTRAL).date())
    else:
        season, _, _ = ensure_current_contest_data(session, now=moment)
        if request.method != "GET":
            # Legacy calendar bootstrap may commit internally. Reestablish
            # the fence and authentication before choosing any mutation path.
            lock_authority(session)
            session.expire(profile)
            profile = current_profile(request, session)
            if profile is None:
                raise HTTPException(status_code=401, detail="Student sign-in is required.")
            moment = now or datetime.now(timezone.utc)
            if persistent_enabled(session, moment):
                season = season_covering_date(session, utc(moment).astimezone(CENTRAL).date())
    return profile, season, moment


@router.get("")
def list_teams(request: Request):
    with SessionLocal() as session:
        profile, _, now = authenticated_context(request, session)
        return selection_payload(session, profile=profile, now=now)


@router.post("", status_code=201)
def create_team(request: Request, submitted: TeamCreate):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        try:
            team, _ = create_and_join_team(
                session, profile=profile, season=season,
                name=submitted.name, emblem_key=submitted.emblem_key, now=now,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        payload = selection_payload(session, profile=profile, now=now)
        payload.update({"created": True, "team": next(row for row in payload["teams"] if row["id"] == team.id)})
        return payload


@router.post("/selection")
def join_team(request: Request, submitted: TeamJoin):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        team = session.get(Team, submitted.team_id)
        if (
            team is None or not _team_is_current(session, team, _season_id(season), now)
            or team.moderation_status == "hidden"
            or team.visibility != "public"
        ):
            raise HTTPException(status_code=404, detail="Team was not found.")
        try:
            membership, changed = select_team(session, profile=profile, season=season, team=team, now=now)
            session.commit(); session.refresh(membership)
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        payload = selection_payload(session, profile=profile, now=now)
        payload.update({"changed": changed})
        return payload


@router.delete("/selection")
def leave_selected_team(request: Request):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        try:
            changed = leave_team(session, profile=profile, season=season, now=now)
            session.commit()
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {**selection_payload(session, profile=profile, now=now), "changed": changed}


@router.post("/private-requests", status_code=201)
def request_private_team_membership(
    request: Request, submitted: PrivateTeamJoin
):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        _lock_team_writer(session, _season_id(season))
        persistent = persistent_enabled(session, now)
        if persistent:
            _lock_profile(session, profile)
        code = submitted.join_code.strip().upper().replace(" ", "")
        team = session.scalar(select(Team).where(
            Team.is_operating.is_(True) if persistent else Team.season_id == _season_id(season),
            Team.join_code == code,
            Team.visibility == "private",
            Team.director_led.is_(True),
            Team.moderation_status != "hidden",
        ))
        if team is None:
            raise HTTPException(status_code=404, detail="That private team code was not found.")
        if team.creator_profile_id == profile.id:
            raise HTTPException(status_code=409, detail="Manage your team from Director Team Management.")
        membership = active_membership(
            session, profile_id=profile.id, season_id=_season_id(season), at=now
        )
        if membership is not None and membership.team_id == team.id:
            return {"created": False, "status": "joined"}
        existing = session.scalar(select(TeamJoinRequest).where(
            TeamJoinRequest.profile_id == profile.id,
            TeamJoinRequest.is_persistent.is_(True) if persistent else TeamJoinRequest.season_id == _season_id(season),
            TeamJoinRequest.status == "pending",
        ))
        if existing is not None:
            if existing.team_id != team.id:
                raise HTTPException(
                    status_code=409,
                    detail="You already have a pending private-team request.",
                )
            return {"created": False, "status": "pending", "request_id": existing.id}
        join_request = TeamJoinRequest(
            season_id=None if persistent else _season_id(season), team_id=team.id, profile_id=profile.id,
            status="pending", requested_at=now.astimezone(timezone.utc),
            is_persistent=persistent,
        )
        session.add(join_request)
        try:
            session.commit()
            session.refresh(join_request)
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="You already have a pending private-team request.",
            ) from error
        return {"created": True, "status": "pending", "request_id": join_request.id}


@router.get("/director")
def get_director_team(request: Request, team_id: int | None = None):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        try:
            return director_team_payload(
                session, profile=profile, season=season, team_id=team_id, now=now
            )
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/director", status_code=201)
def create_private_director_team(request: Request, submitted: TeamCreate):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        try:
            team = create_director_team(
                session, profile=profile, season=season,
                name=submitted.name, emblem_key=submitted.emblem_key, now=now,
            )
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "created": True,
            **director_team_payload(
                session, profile=profile, season=season, team_id=team.id, now=now
            ),
        }


@router.post("/director/{team_id}/join-code")
def regenerate_private_team_code(team_id: int, request: Request):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        _lock_team_writer(session, _season_id(season))
        try:
            team = _owned_director_team(
                session, profile=profile, team_id=team_id, now=now
            )
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if not _team_is_current(session, team, _season_id(season), now):
            raise HTTPException(status_code=404, detail="Director-led team was not found.")
        team.join_code = _new_join_code(session)
        session.commit()
        return {"join_code": team.join_code}


@router.post("/director/{team_id}/playing-membership")
def join_owned_team_as_player(team_id: int, request: Request):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        _lock_team_writer(session, _season_id(season))
        try:
            team = _owned_director_team(session, profile=profile, team_id=team_id, now=now)
            membership, changed = select_team(
                session, profile=profile, season=season, team=team, now=now,
                private_authorized=True,
            )
            session.commit()
            session.refresh(membership)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"changed": changed, "team_id": team.id}


@router.post("/director/{team_id}/requests/{request_id}")
def resolve_private_team_request(
    team_id: int, request_id: int, request: Request,
    submitted: JoinRequestDecision,
):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        _lock_team_writer(session, _season_id(season))
        try:
            team = _owned_director_team(session, profile=profile, team_id=team_id, now=now)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if not _team_is_current(session, team, _season_id(season), now):
            raise HTTPException(status_code=404, detail="Director-led team was not found.")
        join_request = session.get(TeamJoinRequest, request_id)
        if (
            join_request is None or join_request.team_id != team.id
            or join_request.status != "pending"
            or (persistent_enabled(session, now) and not join_request.is_persistent)
        ):
            raise HTTPException(status_code=404, detail="Pending request was not found.")
        action = submitted.action.strip().casefold()
        if action not in {"approve", "reject"}:
            raise HTTPException(status_code=400, detail="Choose approve or reject.")
        if action == "approve":
            from .age_privacy import require_eligible
            require_eligible(session,join_request.profile_id)
            if not sharing_allowed(session,join_request.profile_id):
                raise HTTPException(403,"Team sharing is unavailable for this account.")
            student = session.get(WoodchuckProfile, join_request.profile_id)
            if student is None or student.status != "active":
                raise HTTPException(status_code=404, detail="Student was not found.")
            try:
                select_team(
                    session, profile=student, season=season, team=team, now=now,
                    private_authorized=True,
                )
            except ValueError as error:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(error)) from error
            join_request.status = "approved"
        else:
            join_request.status = "rejected"
        join_request.resolved_at = now.astimezone(timezone.utc)
        join_request.resolved_by_profile_id = profile.id
        session.commit()
        return {"status": join_request.status}


@router.delete("/director/{team_id}/members/{profile_id}")
def remove_private_team_member(team_id: int, profile_id: int, request: Request):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        _lock_team_writer(session, _season_id(season))
        try:
            team = _owned_director_team(session, profile=profile, team_id=team_id, now=now)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if not _team_is_current(session, team, _season_id(season), now):
            raise HTTPException(status_code=404, detail="Director-led team was not found.")
        member_profile = session.get(WoodchuckProfile, profile_id)
        if member_profile is not None and persistent_enabled(session, now):
            _lock_profile(session, member_profile)
        membership = active_membership(session, profile_id=profile_id, season_id=_season_id(season), at=now)
        if membership is not None and membership.team_id != team.id:
            membership = None
        if membership is None:
            raise HTTPException(status_code=404, detail="Active team member was not found.")
        membership.ended_at = now.astimezone(timezone.utc)
        session.commit()
        return {"removed": True}


@router.post("/{team_id}/reports", status_code=201)
def report_team(team_id: int, request: Request, submitted: TeamReportCreate):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        from .age_privacy import sharing_allowed
        if profile and request.method != "GET" and not sharing_allowed(session, profile.id):
            raise HTTPException(403, "This account cannot share identifying information with other users.")
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        team = session.get(Team, team_id)
        if team is None or team.moderation_status == "hidden":
            raise HTTPException(status_code=404, detail="Team was not found.")
        if submitted.category not in REPORT_CATEGORIES:
            raise HTTPException(status_code=400, detail="Choose a report category.")
        details = submitted.details.strip()
        existing = session.scalar(select(TeamReport).where(
            TeamReport.team_id == team.id,
            TeamReport.reporter_profile_id == profile.id,
            TeamReport.status == "unresolved",
        ))
        if existing is not None:
            return {"created": False, "report_id": existing.id, "status": existing.status}
        report = TeamReport(
            team_id=team.id, reporter_profile_id=profile.id,
            category=submitted.category, details=details or None,
        )
        session.add(report)
        try:
            session.commit(); session.refresh(report)
        except IntegrityError:
            session.rollback()
            existing = session.scalar(select(TeamReport).where(
                TeamReport.team_id == team.id,
                TeamReport.reporter_profile_id == profile.id,
                TeamReport.status == "unresolved",
            ))
            if existing is None:
                raise HTTPException(status_code=409, detail="The report could not be saved.")
            return {"created": False, "report_id": existing.id, "status": existing.status}
        return {"created": True, "report_id": report.id, "status": report.status}
