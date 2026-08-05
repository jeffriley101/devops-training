from __future__ import annotations

from datetime import datetime, time, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_routes import current_profile
from .contests import CENTRAL, central_week_boundaries, ensure_band_camp_data
from .db import SessionLocal
from .models import Season, Team, TeamMembership, TeamReport, WoodchuckProfile
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


def membership_at(session: Session, *, profile_id: int, season_id: int, at: datetime) -> TeamMembership | None:
    moment = utc(at)
    rows = session.scalars(select(TeamMembership).where(
        TeamMembership.profile_id == profile_id,
        TeamMembership.season_id == season_id,
        TeamMembership.started_at <= moment,
    ).order_by(TeamMembership.started_at.desc())).all()
    return next((row for row in rows if row.ended_at is None or utc(row.ended_at) > moment), None)


def select_team(session: Session, *, profile: WoodchuckProfile, season: Season,
                team: Team, now: datetime) -> tuple[TeamMembership, bool]:
    if team.season_id != season.id:
        raise ValueError("That team is not in the active season.")
    if team.moderation_status == "hidden":
        raise ValueError("That team is not available.")
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
    if session.scalar(select(Team.id).where(Team.season_id == season.id, Team.creator_profile_id == profile.id)):
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


def selection_payload(session: Session, *, profile: WoodchuckProfile, now: datetime) -> dict[str, object]:
    season, _, week = ensure_band_camp_data(session, now=now)
    membership = active_membership(session, profile_id=profile.id, season_id=season.id)
    teams = session.scalars(select(Team).where(
        Team.season_id == season.id,
        Team.moderation_status != "hidden",
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
