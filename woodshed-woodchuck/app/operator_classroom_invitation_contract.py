"""Frozen c26 storage contract, independent of ORM and runtime feature gates."""

CHECKS = {
    "ck_classroom_student_invitation_expiry": "expires_at > issued_at",
    "ck_classroom_student_invitation_acceptance":
        "(accepted_at IS NULL AND accepted_by_profile_id IS NULL) OR "
        "(accepted_at IS NOT NULL AND accepted_by_profile_id IS NOT NULL AND "
        "accepted_by_profile_id = profile_id AND accepted_at >= issued_at AND accepted_at < expires_at)",
    "ck_classroom_student_invitation_revocation":
        "(revoked_at IS NULL AND revoked_by_verifier_id IS NULL) OR "
        "(revoked_at IS NOT NULL AND revoked_by_verifier_id IS NOT NULL AND "
        "revoked_by_verifier_id = issued_by_verifier_id AND revoked_at >= issued_at AND "
        "(accepted_at IS NULL OR revoked_at >= accepted_at))",
}

C26 = {
    "classroom_student_invitations": {
        "exact": True,
        "columns": {
            "id": ("Integer", None, False), "program_id": ("Integer", None, False),
            "class_id": ("Integer", None, False), "assignment_id": ("Integer", None, False),
            "profile_id": ("Integer", None, False), "issued_by_verifier_id": ("Integer", None, False),
            "issued_at": ("DateTime", None, False), "expires_at": ("DateTime", None, False),
            "accepted_at": ("DateTime", None, True), "accepted_by_profile_id": ("Integer", None, True),
            "revoked_at": ("DateTime", None, True), "revoked_by_verifier_id": ("Integer", None, True),
        },
        "defaults": {"id": "serial"},
        "checks": CHECKS,
        # PostgreSQL removes redundant outer grouping; retain meaningful OR grouping.
        "pg_checks": {
            "ck_classroom_student_invitation_expiry": "expires_at > issued_at",
            "ck_classroom_student_invitation_acceptance":
                "accepted_at IS NULL AND accepted_by_profile_id IS NULL OR "
                "accepted_at IS NOT NULL AND accepted_by_profile_id IS NOT NULL AND "
                "accepted_by_profile_id = profile_id AND accepted_at >= issued_at AND accepted_at < expires_at",
            "ck_classroom_student_invitation_revocation":
                "revoked_at IS NULL AND revoked_by_verifier_id IS NULL OR "
                "revoked_at IS NOT NULL AND revoked_by_verifier_id IS NOT NULL AND "
                "revoked_by_verifier_id = issued_by_verifier_id AND revoked_at >= issued_at AND "
                "(accepted_at IS NULL OR revoked_at >= accepted_at)",
        },
        "primary_key": ("id",), "unique": {},
        "indexes": {"ix_classroom_student_invitation_recipient": (("profile_id", "assignment_id"), False, None)},
        "foreign_keys": [
            (("assignment_id", "class_id", "program_id"), "classroom_teaching_assignments",
             ("id", "class_id", "program_id"), "RESTRICT"),
            (("profile_id",), "woodchuck_profiles", ("id",), "RESTRICT"),
            (("issued_by_verifier_id",), "trusted_verifiers", ("id",), "RESTRICT"),
            (("accepted_by_profile_id",), "woodchuck_profiles", ("id",), "RESTRICT"),
            (("revoked_by_verifier_id",), "trusted_verifiers", ("id",), "RESTRICT"),
        ],
    },
}
