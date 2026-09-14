"""Student feature policy; existing core product functionality stays Open.

Callers must first authorize the student context. Access never grants another
student's data or verifier relationship permissions.
"""
from dataclasses import dataclass
from .memberships import student_has_full_access
from .models import WoodchuckProfile


@dataclass(frozen=True)
class Feature:
    enabled: bool
    required_access: str


FEATURES: dict[str, Feature] = {
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
    return feature.required_access == "open" or student_has_full_access(session, profile_id)


def require_feature(session, profile_id, feature_key):
    """Call after authenticating the student; recheck entitlement on each request."""
    if not can_use_feature(session, profile_id, feature_key):
        from fastapi import HTTPException
        raise HTTPException(403, "This feature requires available Full Access.",
                            headers={"Cache-Control": "no-store"})
