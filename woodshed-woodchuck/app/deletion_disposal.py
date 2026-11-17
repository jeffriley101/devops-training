"""Dispose of approved associated data within the caller's account-deletion transaction."""
from functools import partial

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .deletion_age_history import clear_age_history, age_history_complete
from .deletion_private_activity import (
    clear_private_activity, private_activity_complete,
    clear_private_school_activity, private_school_activity_complete,
)
from .verifier_invitation_disposal import (
    clear_student_delivery_history, student_delivery_history_complete,
    clear_adult_invitation_contacts, adult_invitation_contacts_complete,
    unverified_adult_invitation_contact_count,
)


STUDENT_DISPOSALS = {
    "private_activity": (clear_private_activity, private_activity_complete),
    "practice_and_plans": (clear_private_school_activity, private_school_activity_complete),
    "age_history": (clear_age_history, age_history_complete),
    "delivery_history": (clear_student_delivery_history, student_delivery_history_complete),
}
ADULT_CONTACTS = "accepted_invitation_contacts"


class DeletionDataConflict(ValueError):
    pass


def valid_disposal_record(record, *, adult):
    names = {ADULT_CONTACTS} if adult else set(STUDENT_DISPOSALS)
    if type(record) is not dict or record.keys() != names:
        return False
    return all(status in ("verified", "access_required") or
               (name == ADULT_CONTACTS and status == "contact_review_required")
               for name, status in record.items())


def associated_disposal(session, subject_id, *, adult_key=None, deleted_at=None, recorded=None):
    """Apply and verify each scope, or recheck only the coverage of an existing receipt."""
    operations = STUDENT_DISPOSALS
    if adult_key is not None:
        operations = {ADULT_CONTACTS: (
            partial(clear_adult_invitation_contacts, original_key=adult_key, deleted_at=deleted_at),
            partial(adult_invitation_contacts_complete, original_key=adult_key, deleted_at=deleted_at),
        )}
    postgres = session.get_bind().dialect.name == "postgresql"
    coverage = {}
    for name, (remove, complete) in operations.items():
        if recorded is not None and recorded[name] == "access_required":
            coverage[name] = recorded[name]
            continue
        try:
            with session.begin_nested():
                if postgres:
                    # PostgreSQL rejects reads/writes subject to an RLS filter under
                    # this setting. It confers no right to see or change filtered rows.
                    prior_rls = session.scalar(text("SHOW row_security"))
                    session.execute(text("SET LOCAL row_security TO off"))
                if recorded is None:
                    remove(session, subject_id)
                    session.flush()
                if not complete(session, subject_id):
                    raise DeletionDataConflict("Associated data cleanup could not be verified.")
                status = "verified" if recorded is None else recorded[name]
                if name == ADULT_CONTACTS and unverified_adult_invitation_contact_count(session, subject_id):
                    if recorded is None:
                        status = "contact_review_required"
                    elif status == "verified":
                        raise DeletionDataConflict("Adult invitation contacts need review.")
                coverage[name] = status
                if postgres:
                    session.execute(text("SELECT set_config('row_security', :previous, true)"), {"previous": prior_rls})
        except DBAPIError as error:
            if not postgres or getattr(error.orig, "sqlstate", None) != "42501":
                raise
            if recorded is not None:
                raise DeletionDataConflict("Associated data readback requires additional access.") from error
            coverage[name] = "access_required"
    return coverage
