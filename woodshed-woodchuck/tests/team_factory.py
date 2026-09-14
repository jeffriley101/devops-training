"""Explicit TeamFamily setup for tests constructing seasonal Team rows."""
from app.models import Team, TeamFamily


def make_team(session, **fields):
    family = TeamFamily(**(
        {"created_at": fields["created_at"]} if "created_at" in fields else {}
    ))
    session.add(family)
    session.flush()
    return Team(family_id=family.id, **fields)
