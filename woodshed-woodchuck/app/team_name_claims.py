"""Atomic public name claims; callers own the surrounding transaction."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import Team, TeamNameClaim


TEAM_NAME_TAKEN = "That Team name is already taken."


def claim_public_team_name(session, *, normalized_name: str, family_id: int) -> None:
    session.flush()
    owner = session.scalar(select(TeamNameClaim.family_id).where(
        TeamNameClaim.normalized_name == normalized_name
    ))
    if owner is not None:
        if owner != family_id:
            raise ValueError(TEAM_NAME_TAKEN)
        return
    # Old application instances can create Teams after the migration backfill
    # and before cutover. Recover their ownership instead of taking their name.
    existing_owners = list(session.scalars(select(Team.family_id).where(
        Team.normalized_name == normalized_name,
        Team.visibility == "public",
    ).distinct().limit(2)))
    if len(existing_owners) > 1:
        raise ValueError(TEAM_NAME_TAKEN)
    rightful_owner = existing_owners[0] if existing_owners else family_id
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    session.execute(insert(TeamNameClaim).values(
        normalized_name=normalized_name, family_id=rightful_owner
    ).on_conflict_do_nothing(index_elements=["normalized_name"]))
    owner = session.scalar(select(TeamNameClaim.family_id).where(
        TeamNameClaim.normalized_name == normalized_name
    ))
    if owner != family_id:
        raise ValueError(TEAM_NAME_TAKEN)
