from __future__ import annotations

from datetime import datetime, time, timezone
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_routes import current_profile
from .contests import CENTRAL, central_week_boundaries, ensure_band_camp_data
from .db import SessionLocal
from .models import (
    ProfileCapability,
    Season,
    Team,
    TeamJoinRequest,
    TeamMembership,
    TeamReport,
    WoodchuckProfile,
)
from .team_names import InvalidTeamName, normalized_team_name


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
MAX_PUBLIC_TEAMS = 200

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
    return team.display_name, emblem_payload(team.emblem_key)


def team_payload(team: Team, captain: WoodchuckProfile | None) -> dict[str, object]:
    name, emblem = public_team_identity(team)
    captain_visible = (
        team.moderation_status != "hidden"
        and captain is not None
        and captain.status == "active"
    )
    return {
        "id": team.id, "name": name, "emblem": emblem,
        "visibility": team.visibility,
        "director_led": team.director_led,
        "captain": {
            "display_name": captain.display_name if captain_visible else None,
            "is_team_captain": captain_visible,
            "accessible_label": "Team Captain" if captain_visible else None,
        } if captain_visible else None,
    }


def active_membership(session: Session, *, profile_id: int, season_id: int) -> TeamMembership | None:
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
    rows = session.scalars(select(TeamMembership).where(
        TeamMembership.profile_id == profile_id,
        TeamMembership.season_id == season_id,
        TeamMembership.started_at <= moment,
    ).order_by(TeamMembership.started_at.desc())).all()
    return next((row for row in rows if row.ended_at is None or utc(row.ended_at) > moment), None)


def select_team(session: Session, *, profile: WoodchuckProfile, season: Season,
                team: Team, now: datetime,
                private_authorized: bool = False) -> tuple[TeamMembership, bool]:
    if team.season_id != season.id:
        raise ValueError("That team is not in the active season.")
    if team.moderation_status == "hidden":
        raise ValueError("That team is not available.")
    if team.visibility == "private" and not private_authorized:
        raise ValueError("That private team requires director approval.")
    week_start, _, _, _ = central_week_boundaries(now)
    current = active_membership(session, profile_id=profile.id, season_id=season.id)
    if current and current.team_id == team.id:
        return current, False
    current_team = session.get(Team, current.team_id) if current else None
    week_membership_count = session.scalar(select(func.count(TeamMembership.id)).where(
        TeamMembership.profile_id == profile.id,
        TeamMembership.season_id == season.id,
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
        season_id=season.id, team_id=team.id, profile_id=profile.id,
        selected_week_start=week_start, started_at=now_utc,
    )
    session.add(membership)
    session.flush()
    return membership, True


def create_and_join_team(session: Session, *, profile: WoodchuckProfile,
                         season: Season, name: str, emblem_key: str,
                         now: datetime) -> tuple[Team, TeamMembership]:
    if emblem_key not in APPROVED_EMBLEMS:
        raise ValueError("Choose an approved team emblem.")
    try:
        display, normalized = normalized_team_name(name)
    except InvalidTeamName as error:
        raise ValueError(str(error)) from error
    if session.scalar(select(Team.id).where(
        Team.season_id == season.id,
        Team.creator_profile_id == profile.id,
        Team.visibility == "public",
    )):
        raise ValueError("You may create only one team per season.")
    team = Team(
        season_id=season.id, display_name=display, normalized_name=normalized,
        emblem_key=emblem_key, creator_profile_id=profile.id,
    )
    session.add(team)
    try:
        session.flush()
        membership, _ = select_team(session, profile=profile, season=season, team=team, now=now)
        session.commit(); session.refresh(team); session.refresh(membership)
    except IntegrityError as error:
        session.rollback()
        raise ValueError("That team name or emblem is already in use.") from error
    return team, membership


def _new_join_code(session: Session) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if session.scalar(select(Team.id).where(Team.join_code == code)) is None:
            return code
    raise RuntimeError("A private team code could not be generated.")


def create_director_team(
    session: Session, *, profile: WoodchuckProfile, season: Season,
    name: str, emblem_key: str, now: datetime,
) -> Team:
    if not has_band_director_capability(session, profile_id=profile.id):
        raise PermissionError("Band Director authorization is required.")
    if emblem_key not in APPROVED_EMBLEMS:
        raise ValueError("Choose an approved team emblem.")
    try:
        display, normalized = normalized_team_name(name)
    except InvalidTeamName as error:
        raise ValueError(str(error)) from error
    team = Team(
        season_id=season.id,
        display_name=display,
        normalized_name=normalized,
        emblem_key=emblem_key,
        creator_profile_id=profile.id,
        visibility="private",
        director_led=True,
        join_code=_new_join_code(session),
        created_at=now.astimezone(timezone.utc),
    )
    session.add(team)
    try:
        session.commit()
        session.refresh(team)
    except IntegrityError as error:
        session.rollback()
        raise ValueError("That team name or emblem is already in use.") from error
    return team


def selection_payload(session: Session, *, profile: WoodchuckProfile, now: datetime) -> dict[str, object]:
    season, _, week = ensure_band_camp_data(session, now=now)
    membership = active_membership(session, profile_id=profile.id, season_id=season.id)
    teams = session.scalars(select(Team).where(
        Team.season_id == season.id,
        Team.moderation_status != "hidden",
        Team.visibility == "public",
    ).order_by(
        Team.display_name, Team.id
    ).limit(MAX_PUBLIC_TEAMS)).all()
    current_team = session.get(Team, membership.team_id) if membership else None
    visible_teams = list(teams)
    if current_team is not None and current_team.id not in {team.id for team in visible_teams}:
        visible_teams.append(current_team)
    captain_ids = {team.creator_profile_id for team in visible_teams if team.creator_profile_id}
    captains = {row.id: row for row in session.scalars(select(WoodchuckProfile).where(
        WoodchuckProfile.id.in_(captain_ids), WoodchuckProfile.status == "active"
    )).all()} if captain_ids else {}
    current_captain = captains.get(current_team.creator_profile_id) if current_team else None
    week_membership_count = session.scalar(select(func.count(TeamMembership.id)).where(
        TeamMembership.profile_id == profile.id,
        TeamMembership.season_id == season.id,
        TeamMembership.selected_week_start == week.week_start,
    )) or 0
    locked = bool(
        membership and week_membership_count >= 2
        and (current_team is None or current_team.moderation_status != "hidden")
    )
    next_at = datetime.combine(week.week_end, time.min, CENTRAL).astimezone(timezone.utc)
    return {
        "season": {"key": season.key, "name": season.name},
        "teams": [team_payload(team, captains.get(team.creator_profile_id)) for team in teams],
        "membership": {
            "team": team_payload(current_team, current_captain) if current_team else None,
            "selected_week_start": membership.selected_week_start.isoformat() if membership else None,
            "locked": locked,
            "correction_available": bool(membership and not locked),
            "correction_message": (
                "Your team correction has been used for this week. You can choose again next Monday."
                if locked else "You have one team correction available this week."
            ) if membership else "Choose a team to get started.",
            "next_change_at": next_at.isoformat() if locked else None,
        },
        "pending_private_request": _pending_request_payload(
            session, profile_id=profile.id, season_id=season.id
        ),
        "band_director": has_band_director_capability(
            session, profile_id=profile.id
        ),
        "approved_emblems": [emblem_payload(key) for key in APPROVED_EMBLEMS],
    }


def _pending_request_payload(
    session: Session, *, profile_id: int, season_id: int
) -> dict[str, object] | None:
    request_row = session.scalar(select(TeamJoinRequest).where(
        TeamJoinRequest.profile_id == profile_id,
        TeamJoinRequest.season_id == season_id,
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
    session: Session, *, profile: WoodchuckProfile, team_id: int | None = None
) -> Team:
    if not has_band_director_capability(session, profile_id=profile.id):
        raise PermissionError("Band Director authorization is required.")
    filters = [
        Team.creator_profile_id == profile.id,
        Team.director_led.is_(True),
        Team.visibility == "private",
    ]
    if team_id is not None:
        filters.append(Team.id == team_id)
    team = session.scalar(select(Team).where(*filters))
    if team is None:
        raise LookupError("Director-led team was not found.")
    return team


def director_team_payload(
    session: Session, *, profile: WoodchuckProfile, season: Season,
    team_id: int | None = None,
) -> dict[str, object]:
    authorized = has_band_director_capability(session, profile_id=profile.id)
    if not authorized:
        raise PermissionError("Band Director authorization is required.")
    teams = list(session.scalars(select(Team).where(
        Team.season_id == season.id,
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
            TeamMembership.ended_at.is_(None),
            WoodchuckProfile.status == "active",
        ).order_by(WoodchuckProfile.display_name, WoodchuckProfile.id)
    ).all()
    request_rows = session.execute(
        select(TeamJoinRequest, WoodchuckProfile)
        .join(WoodchuckProfile, WoodchuckProfile.id == TeamJoinRequest.profile_id)
        .where(
            TeamJoinRequest.team_id == team.id,
            TeamJoinRequest.status == "pending",
            WoodchuckProfile.status == "active",
        ).order_by(TeamJoinRequest.requested_at, TeamJoinRequest.id)
    ).all()
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
                for member, member_profile in membership_rows
            ],
            "pending_requests": [
                {"id": join_request.id, "profile_id": join_request.profile_id,
                 "display_name": request_profile.display_name,
                 "requested_at": join_request.requested_at.isoformat()}
                for join_request, request_profile in request_rows
            ],
        },
        "approved_emblems": [emblem_payload(key) for key in APPROVED_EMBLEMS],
    }


def authenticated_context(request: Request, session: Session, now: datetime | None = None):
    profile = current_profile(request, session)
    if profile is None:
        raise HTTPException(status_code=401, detail="Student sign-in is required.")
    moment = now or datetime.now(timezone.utc)
    season, _, _ = ensure_band_camp_data(session, now=moment)
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
            team is None or team.season_id != season.id
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


@router.post("/private-requests", status_code=201)
def request_private_team_membership(
    request: Request, submitted: PrivateTeamJoin
):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        code = submitted.join_code.strip().upper().replace(" ", "")
        team = session.scalar(select(Team).where(
            Team.season_id == season.id,
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
            session, profile_id=profile.id, season_id=season.id
        )
        if membership is not None and membership.team_id == team.id:
            return {"created": False, "status": "joined"}
        existing = session.scalar(select(TeamJoinRequest).where(
            TeamJoinRequest.profile_id == profile.id,
            TeamJoinRequest.season_id == season.id,
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
            season_id=season.id, team_id=team.id, profile_id=profile.id,
            status="pending", requested_at=now.astimezone(timezone.utc),
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
        profile, season, _ = authenticated_context(request, session)
        try:
            return director_team_payload(
                session, profile=profile, season=season, team_id=team_id
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
                session, profile=profile, season=season, team_id=team.id
            ),
        }


@router.post("/director/{team_id}/join-code")
def regenerate_private_team_code(team_id: int, request: Request):
    with SessionLocal() as session:
        profile, season, _ = authenticated_context(request, session)
        try:
            team = _owned_director_team(
                session, profile=profile, team_id=team_id
            )
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if team.season_id != season.id:
            raise HTTPException(status_code=404, detail="Director-led team was not found.")
        team.join_code = _new_join_code(session)
        session.commit()
        return {"join_code": team.join_code}


@router.post("/director/{team_id}/playing-membership")
def join_owned_team_as_player(team_id: int, request: Request):
    with SessionLocal() as session:
        profile, season, now = authenticated_context(request, session)
        try:
            team = _owned_director_team(session, profile=profile, team_id=team_id)
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
        try:
            team = _owned_director_team(session, profile=profile, team_id=team_id)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if team.season_id != season.id:
            raise HTTPException(status_code=404, detail="Director-led team was not found.")
        join_request = session.get(TeamJoinRequest, request_id)
        if (
            join_request is None or join_request.team_id != team.id
            or join_request.status != "pending"
        ):
            raise HTTPException(status_code=404, detail="Pending request was not found.")
        action = submitted.action.strip().casefold()
        if action not in {"approve", "reject"}:
            raise HTTPException(status_code=400, detail="Choose approve or reject.")
        if action == "approve":
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
        try:
            team = _owned_director_team(session, profile=profile, team_id=team_id)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        membership = session.scalar(select(TeamMembership).where(
            TeamMembership.team_id == team.id,
            TeamMembership.season_id == season.id,
            TeamMembership.profile_id == profile_id,
            TeamMembership.ended_at.is_(None),
        ))
        if membership is None:
            raise HTTPException(status_code=404, detail="Active team member was not found.")
        membership.ended_at = now.astimezone(timezone.utc)
        session.commit()
        return {"removed": True}


@router.post("/{team_id}/reports", status_code=201)
def report_team(team_id: int, request: Request, submitted: TeamReportCreate):
    with SessionLocal() as session:
        profile = current_profile(request, session)
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
