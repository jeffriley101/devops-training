"""Supervised, same-transaction replacement using the existing school workflow."""
from datetime import datetime
from getpass import getpass
import os
import sys
from types import SimpleNamespace
import warnings

from . import classroom, classroom_s2 as s2, classroom_school_authorization as school, site_admin
from .support_case_files import SupportDenied, _digest


def replace(db, row, expected, interaction):
    # Independent reviewers bind the exact replacement contact evidence to their preview.
    # Original Program, school, authorization scope, approver and year come from the locked receipt.
    if school.status(row, s2._now()) != "authorized":
        raise SupportDenied("disposition_replacement_requires_active_authority")
    evidence, admin, reviewer = interaction.verify(db, row)
    if set(evidence) != {"school_name", "authority_description", "contact_name", "contact_role", "contact_address", "contact_source",
                         "verification_method", "verification_notes", "contacted_at"} or _digest(evidence) != expected:
        raise SupportDenied("disposition_replacement_evidence_mismatch")
    # Keep the identity and delegation text only if independently resupplied and
    # approved as necessary. A different textual scope requires separate review.
    if (evidence["school_name"], evidence["authority_description"]) != (row.school_name, row.authority_description):
        raise SupportDenied("disposition_school_scope_equivalence_unverified")
    values = dict(evidence, contacted_at=datetime.fromisoformat(evidence["contacted_at"]))
    school.revoke(db, actor=reviewer, admin=admin, program_id=row.program_id, record_id=row.id,
                  previous=school.revision(row), reason="replaced")
    successor = school.verify_authority(db, admin=admin, reviewer=reviewer, program_id=row.program_id,
        approver_id=row.approver_id, authority_kind=row.authority_kind, year_start=row.year_start,
        previous=school.revision(row), independently_confirmed=True, **values)
    actor, explicit = interaction.accept(db, successor)
    school.approve(db, actor=actor, explicit=explicit, program_id=row.program_id, record_id=successor.id,
        previous=school.revision(successor), agreement_version=row.agreement_version,
        agreement_digest=row.agreement_digest)
    if (school.status(successor, s2._now()) != "authorized" or successor.expires_at != row.expires_at
            or not school.authorized_program(db, program_id=row.program_id)):
        raise SupportDenied("disposition_successor_unavailable")
    return successor


class TerminalParticipants:
    """Each present participant enters their own credentials on a private supervised terminal."""

    @staticmethod
    def _private(prompt):
        if (not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr))
                or len({os.ttyname(stream.fileno()) for stream in (sys.stdin, sys.stdout, sys.stderr)}) != 1):
            raise SupportDenied("disposition_private_terminal_required")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            return getpass(prompt + ": ")

    def _adult(self, db, label):
        return classroom.authenticate_adult(db, email=self._private(label + " email"), pin=self._private(label + " PIN"))

    def verify(self, db, row):
        request = SimpleNamespace(session={})
        site_admin.sign_in_site_admin(request, self._private("Site administrator credential"))
        reviewer = self._adult(db, "Independent school-authority reviewer")
        evidence = {key: self._private(key) for key in ("school_name", "authority_description", "contact_name", "contact_role", "contact_address",
            "contact_source", "verification_method", "verification_notes", "contacted_at")}
        if self._private(f"Independently verified unchanged scope for Program {row.program_id}. Enter VERIFIED") != "VERIFIED":
            raise SupportDenied("disposition_school_verification_required")
        return evidence, s2.site_admin_authentication(request), reviewer

    def accept(self, db, row):
        print(school.AGREEMENT)
        print(f"School {row.school_name}. Program {row.program_id}. Scope {row.authority_description}. "
              f"Expires {row.expires_at.isoformat()}. New receipt {row.id}.")
        actor = self._adult(db, "School approver")
        return actor, self._private(f"Accept receipt {row.id}. Enter ACCEPT {row.id}") == f"ACCEPT {row.id}"
