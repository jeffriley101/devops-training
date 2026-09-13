"""Feature policy mechanism; no existing product features are gated in this phase.

Callers must first authorize the student context. Access never grants analytics
or verifier relationship permissions.
"""
from dataclasses import dataclass
from .memberships import student_has_full_access
from .models import WoodchuckProfile


@dataclass(frozen=True)
class Feature:
    enabled: bool
    required_access: str


FEATURES: dict[str, Feature] = {}


def can_use_feature(session, profile_id, feature_key):
    feature = FEATURES.get(feature_key)
    if feature is None or not feature.enabled or feature.required_access not in {"open", "full"}:
        return False
    profile = session.get(WoodchuckProfile, profile_id)
    if profile is None or profile.status != "active":
        return False
    return feature.required_access == "open" or student_has_full_access(session, profile_id)
