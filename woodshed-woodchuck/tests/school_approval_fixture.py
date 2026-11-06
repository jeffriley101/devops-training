"""Real synthetic school receipts for tests of other authorization boundaries.

Only the legal tuple is simulated, using monkeypatch's per-test restoration.
Neither the production decision nor its locks/verification are replaced.
"""
from datetime import timedelta
from types import SimpleNamespace

from app import classroom as s1, classroom_s2 as s2, classroom_school_authorization as school, site_admin
from app.classroom_models import ClassroomProgram
from app.models import TrustedVerifier


def approve_programs(factory, monkeypatch, program_ids=(1, 2), *, reviewer_id=4, approver_id=1):
    monkeypatch.setenv('CLASSROOM_SCHOOL_AUTHORIZATION_ENABLED', '1')
    monkeypatch.setattr(school, 'LEGALLY_APPROVED_AGREEMENTS',
        frozenset({(school.AGREEMENT_VERSION, school.AGREEMENT_DIGEST)}))
    result = {}
    for program_id in program_ids:
        with factory() as session:
            if session.get(ClassroomProgram, program_id) is None:
                continue
            now = s2._now()
            row = school._latest(session, program_id, school.school_year(now))
            if row is None:
                request = SimpleNamespace(session={})
                site_admin.sign_in_site_admin(request, 'synthetic-classroom-s2-admin')
                reviewer = session.get(TrustedVerifier, reviewer_id)
                approver = session.get(TrustedVerifier, approver_id)
                row = school.verify_authority(session, admin=s2.site_admin_authentication(request),
                    reviewer=s1.authenticate_adult(session, email=reviewer.email, pin='2468'),
                    program_id=program_id, approver_id=approver_id, authority_kind='administrator',
                    school_name='Synthetic verified school', authority_description='Synthetic Program delegation',
                    contact_name='Independent official', contact_role='Principal', contact_address='+1 555 010 9999',
                    contact_source='https://school.example.test/directory', verification_method='phone',
                    verification_notes='Synthetic documented independent call; no real school approval.',
                    contacted_at=now-timedelta(minutes=1), year_start=school.school_year(now),
                    independently_confirmed=True, previous=(None, None, None, None))
                school.approve(session, actor=s1.authenticate_adult(session, email=approver.email, pin='2468'),
                    program_id=program_id, record_id=row.id, previous=school.revision(row),
                    agreement_version=school.AGREEMENT_VERSION, agreement_digest=school.AGREEMENT_DIGEST,
                    explicit=True)
                session.commit()
            result[program_id] = row.id
    return result
