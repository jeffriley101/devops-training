"""Student feature policy; existing core product functionality stays Open.

Callers must first authorize the student context. Access never grants another
student's data or verifier relationship permissions.
"""
from dataclasses import dataclass
import os
from .memberships import student_has_full_access
from .models import WoodchuckProfile


@dataclass(frozen=True)
class Feature:
    enabled: bool
    required_access: str


# Read once at process startup. Only the exact documented value opts in.
WEEKLY_LOOP2_ENABLED = os.getenv("WOODSHED_WEEKLY_LOOP2_ENABLED") == "true"


FEATURES: dict[str, Feature] = {
    "weekly_newsletter": Feature(WEEKLY_LOOP2_ENABLED, "open"),
    "weekly_plans": Feature(WEEKLY_LOOP2_ENABLED, "open"),
    "weekly_pantry": Feature(WEEKLY_LOOP2_ENABLED, "open"),
    "weekly_wheel": Feature(WEEKLY_LOOP2_ENABLED, "open"),
    "weekly_monday_bonus": Feature(WEEKLY_LOOP2_ENABLED, "open"),
    "weekly_pants": Feature(WEEKLY_LOOP2_ENABLED, "open"),  # Historical first-practice-streak gate.
    "practice_insights": Feature(True, "full"),
    **{key: Feature(False, "full") for key in (
        "advanced_practice_analytics", "practice_history_tools",
        "customization_collections", "bonus_game_content",
        "advanced_exercises", "seasonal_side_activities",
    )},
}


def can_use_feature(session, profile_id, feature_key):
    feature = FEATURES.get(feature_key)
    if feature is None or not feature.enabled or feature.required_access not in {"open", "full"}:
        return False
    profile = session.get(WoodchuckProfile, profile_id)
    if profile is None or profile.status != "active":
        return False
    if feature_key == "practice_insights":
        # The existing summary also has a narrow Classroom grant. This does
        # not confer Full Access on other features or adult rating access.
        from . import memberships
        from .classroom_capabilities import capability_decision
        return capability_decision(session, profile_id, feature_key,
                                   at=memberships.clock()).allowed
    return feature.required_access == "open" or student_has_full_access(session, profile_id)


def require_feature(session, profile_id, feature_key):
    """Call after authenticating the student; recheck entitlement on each request."""
    if not can_use_feature(session, profile_id, feature_key):
        from fastapi import HTTPException
        detail = ("Practice Insights requires Full Access or eligible Classroom enrollment."
                  if feature_key == "practice_insights" else "This feature requires available Full Access.")
        raise HTTPException(403, detail,
                            headers={"Cache-Control": "no-store"})
