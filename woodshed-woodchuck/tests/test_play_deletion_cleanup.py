from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import account_routes, analytics, child_authorization, kws_verification, parent_access, practice_chart_routes, verifier_routes
from app.account_deletion import DeletionDataConflict, anonymize_woodchuck_account, ordinary_deletion_complete
from app.age_privacy import AgeScreenRequired, declare_age
from app.child_models import ConsentEvidence, DirectorPermission, ParentAccess, PendingConsent
from app.economy import lock_state
from app.email_service import DeliveryResult
from app.kws_models import KWSVerification
from app.main import app
from app.models import (
    AnalyticsEvent, LoginStreak, PracticeChart, PracticeChartVerification,
    PracticeEmailPreset, RewardGrant, StudentVerifierConnection, TrustedVerifier,
    TrustedVerifierInvitation, WoodchuckProfile, WoodchuckState,
)
from app.practice_chart_routes import record_verification_delivery
from app.security import hash_invitation_token
from app.verifier_routes import record_delivery
from app.verifiers import accept_trusted_verifier_invitation
from tests.test_account_deletion import NOW, profile, seed_history


def access_rows(session, student, label, *, verifier_id=None):
    version, _, digest = child_authorization.notice_policy('test')
    consent = ConsentEvidence(
        profile_id=student.id, parent_email=f'{label}@example.invalid',
        notice_version=version, notice_sha256=digest, approved_at=NOW,
        confirmed_at=NOW,
    )
    session.add(consent)
    session.flush()
    rule = declare_age(session, student.id, 'under13', at=NOW)
    rule.consent_id = consent.id
    parent = ParentAccess(consent_id=consent.id, verifier_id=verifier_id, requested_at=NOW)
    director = DirectorPermission(
        profile_id=student.id, consent_id=consent.id, email=f'{label}-director@example.invalid',
        director_name='Synthetic Director', authorized_at=NOW, requested_at=NOW,
    )
    for kind, row in [('parent', parent), ('director', director)]:
        row.link_hash = hash_invitation_token(f'{label}-{kind}-link')
        row.session_hash = hash_invitation_token(f'{label}-{kind}-session')
        row.link_expires_at = row.session_expires_at = NOW + timedelta(hours=1)
    session.add_all([parent, director])
    session.flush()
    return consent, parent, director


def invitation_fields(invitation):
    return {column.name: getattr(invitation, column.name) for column in invitation.__table__.columns}


def test_clears_ordinary_data_while_preserving_provenance_and_shared_records(play_pg, monkeypatch):
    monkeypatch.setattr(child_authorization, 'clock', lambda: NOW)
    monkeypatch.setattr(child_authorization, 'under13_available', lambda: True)
    monkeypatch.setattr(child_authorization, 'notice_policy',
                        lambda environment=None: ('synthetic-test-notice', '', 'a' * 64))
    pending_approval = 'synthetic-existing-student-approval'
    pending_activation = 'synthetic-existing-student-activation'
    with play_pg() as session:
        student, _, other, _, chart, verifier, invite, result = seed_history(session)
        student.first_choice_instrument, student.game_mode = 'Oboe', 'Challenge'
        student.display_name_changed_at = student.level_changed_at = NOW
        student.plunge_best_score = 27
        chart.ordinary_email_error_code = 'synthetic_delivery_error'
        verification = session.scalar(select(PracticeChartVerification).where(
            PracticeChartVerification.practice_chart_id == chart.id,
        ))
        verification.last_email_error_code = invite.last_email_error_code = 'synthetic_delivery_error'
        protected_invitations = [TrustedVerifierInvitation(
            profile_id=student.id, email=f'{status}@example.invalid', role='verifier', status=status,
            accepted_verifier_id=verifier.id if status == 'accepted' else None,
            accepted_at=NOW - timedelta(days=3) if status == 'accepted' else None,
            created_at=NOW - timedelta(days=9), expires_at=NOW - timedelta(days=2),
            updated_at=NOW - timedelta(days=2), token_hash=hash_invitation_token(f'{status}-historical'),
            last_email_attempt_at=NOW - timedelta(days=6), last_email_sent_at=NOW - timedelta(days=7),
            email_attempt_count=2, last_email_error_code='synthetic_error',
        ) for status in ('accepted', 'expired', 'revoked', 'legacy_closed')]
        other_invitation = TrustedVerifierInvitation(
            profile_id=other.id, email=protected_invitations[0].email, role='verifier', status='pending',
            expires_at=NOW + timedelta(days=3), token_hash=hash_invitation_token('other-account-pending'),
            last_email_error_code='other_error',
        )
        consent, parent, director = access_rows(session, student, 'target', verifier_id=verifier.id)
        _, other_parent, other_director = access_rows(session, other, 'other', verifier_id=verifier.id)
        pending = PendingConsent(
            profile_id=student.id, parent_email='existing-flow@example.invalid',
            director_email='', director_name='', notice_version=consent.notice_version,
            approve_hash=hash_invitation_token(pending_approval),
            activation_hash=hash_invitation_token(pending_activation), expires_at=NOW + timedelta(hours=1),
        )
        session.add(pending)
        session.flush()
        kws_reservation = KWSVerification(
            pending_id=pending.id, payload_hash=hash_invitation_token('synthetic-previous-kws-request'),
            environment='test', org_id='synthetic', binding_sha256='b' * 64,
            notice_sha256=consent.notice_sha256, profile_session_version=student.session_version,
            prior_consent_id=consent.id, guardian_attested_at=NOW, notice_accepted_at=NOW,
            account_allowed=True, director_allowed=False, created_at=NOW,
            expires_at=NOW + timedelta(hours=1), state='reserved',
        )
        other_preset = PracticeEmailPreset(profile_id=other.id, display_name='Keep', email=verifier.email)
        session.add_all([*protected_invitations, other_invitation, kws_reservation, other_preset])
        for current in (student, other):
            session.add_all([
                AnalyticsEvent(profile_id=current.id, event_type='arcade_entered', activity_date=NOW.date()),
                LoginStreak(profile_id=current.id, current_days=4, last_login_date=NOW.date(), updated_at=NOW),
            ])
        session.commit()

    expected_history = {
        row.id: {**invitation_fields(row), 'last_email_error_code': None} for row in protected_invitations
    }
    with play_pg() as stale:
        stale_invite = stale.get(TrustedVerifierInvitation, invite.id)
        stale_verification = stale.get(PracticeChartVerification, verification.id)
        with play_pg() as session, session.begin():
            anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id), now=NOW)
        record_delivery(stale, stale_invite, DeliveryResult(False, 'late_error'))
        record_verification_delivery(stale, stale_verification, DeliveryResult(False, 'late_error'))
        stale.commit()

    with play_pg() as session:
        deleted = session.get(WoodchuckProfile, student.id)
        assert deleted.status == 'deleted'
        assert ordinary_deletion_complete(session, deleted)
        assert (deleted.instrument, deleted.level, deleted.goal) == ('[deleted]',) * 3
        assert deleted.first_choice_instrument is deleted.game_mode is None
        assert deleted.display_name_changed_at is deleted.level_changed_at is None
        assert deleted.plunge_best_score == 27
        saved_chart = session.get(PracticeChart, chart.id)
        assert (saved_chart.ordinary_email_error_code, saved_chart.note, saved_chart.practice_details) == (None, None, [])
        saved_verification = session.get(PracticeChartVerification, verification.id)
        assert saved_verification.last_email_error_code is saved_verification.response_note is None
        assert saved_verification.status == 'revoked'
        target_invite = session.get(TrustedVerifierInvitation, invite.id)
        assert target_invite.status == 'revoked' and target_invite.token_hash != invite.token_hash
        assert target_invite.last_email_error_code is None
        assert target_invite.email == 'deleted@invalid.local' and target_invite.expires_at == target_invite.updated_at == NOW
        for invitation_id, expected in expected_history.items():
            assert invitation_fields(session.get(TrustedVerifierInvitation, invitation_id)) == expected
        assert invitation_fields(session.get(TrustedVerifierInvitation, other_invitation.id)) == invitation_fields(other_invitation)
        for token in ('pending-token', 'accepted-historical', 'expired-historical', 'revoked-historical'):
            with pytest.raises(ValueError, match='invitation is invalid'):
                accept_trusted_verifier_invitation(session, token=token, display_name='Adult', pin='1234')
        assert session.get(TrustedVerifier, verifier.id).email == verifier.email
        assert list(session.scalars(select(AnalyticsEvent.profile_id))) == [other.id]
        assert list(session.scalars(select(PracticeEmailPreset.id))) == [other_preset.id]
        streak = session.get(LoginStreak, student.id)
        assert (streak.current_days, streak.last_login_date, streak.updated_at) == (0, None, NOW)
        assert session.get(LoginStreak, other.id).last_login_date == NOW.date()
        assert session.scalar(select(RewardGrant).where(RewardGrant.contest_result_id == result.id)) is not None

        saved_parent = session.get(ParentAccess, parent.consent_id)
        saved_director = session.get(DirectorPermission, director.id)
        for row in (saved_parent, saved_director):
            assert (row.link_hash, row.link_expires_at, row.session_hash, row.session_expires_at) == (None,) * 4
            assert row.requested_at == NOW
        assert session.get(ParentAccess, other_parent.consent_id).session_hash == other_parent.session_hash
        assert session.get(DirectorPermission, other_director.id).link_hash == other_director.link_hash
        assert session.get(ConsentEvidence, consent.id).parent_email == consent.parent_email
        retained_pending = session.get(PendingConsent, pending.id)
        assert (retained_pending.parent_email, retained_pending.profile_id) == (pending.parent_email, student.id)
        for purpose, token in [('approve', pending_approval), ('activate', pending_activation)]:
            with pytest.raises(ValueError, match='Invalid, expired or superseded'):
                child_authorization.pending_from_token(session, token, purpose=purpose, lock=True)
        with pytest.raises(ValueError, match='Parent access is unavailable'):
            parent_access.valid_consent(session, consent.id)
        with pytest.raises(ValueError, match='Parent access is unavailable'):
            parent_access.authenticated(session, SimpleNamespace(session={
                'child_parent_consent': consent.id, 'child_parent_secret': 'target-parent-session',
            }))
        with pytest.raises(ValueError, match='permission is unavailable'):
            child_authorization.send_director_link(session, saved_director)
        with pytest.raises(ValueError, match='current emailed link'):
            child_authorization.consume_director_link(session, 'target-director-link', pin='1234', name='', accept='yes')
        with pytest.raises(ValueError, match='Invalid, expired or superseded'):
            child_authorization.activate(session, pending_activation, profile=deleted, fields={})
        def unexpected_provider_call(*_args):
            pytest.fail('No request for a deleted account may reach the provider')

        monkeypatch.setattr(kws_verification.client, 'send_email', unexpected_provider_call)
        with pytest.raises(ValueError, match='permission request is unavailable'):
            kws_verification.deliver(session, kws_reservation.id, 'synthetic-previous-kws-request')
        assert session.get(KWSVerification, kws_reservation.id).state == 'reserved'


@pytest.mark.parametrize('acceptance_field', ['accepted_at', 'accepted_verifier_id'])
def test_pending_verifier_invitation_with_acceptance_evidence_blocks_atomically(play_pg, acceptance_field):
    with play_pg() as session:
        student, _, _, _, chart, verifier, pending, _ = seed_history(session)
        malformed = TrustedVerifierInvitation(
            profile_id=student.id, email='malformed-pending@example.invalid', role='verifier', status='pending',
            expires_at=NOW + timedelta(days=1), token_hash=hash_invitation_token('malformed-pending'),
            last_email_error_code='synthetic_error',
        )
        setattr(malformed, acceptance_field, NOW if acceptance_field == 'accepted_at' else verifier.id)
        session.add(malformed)
        session.commit()

    with pytest.raises(DeletionDataConflict, match='pending verifier invitation has acceptance evidence'):
        with play_pg() as session, session.begin():
            anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id), now=NOW)

    with play_pg() as session:
        assert session.get(WoodchuckProfile, student.id).status == 'active'
        assert session.get(PracticeChart, chart.id).note == chart.note
        for row in (pending, malformed):
            assert invitation_fields(session.get(TrustedVerifierInvitation, row.id)) == invitation_fields(row)


def test_consent_withdrawal_link_still_works_after_account_deletion(play_pg, monkeypatch):
    withdrawn_at = NOW + timedelta(minutes=5)
    monkeypatch.setattr(child_authorization, 'clock', lambda: withdrawn_at)
    with play_pg() as session, session.begin():
        student = profile(session, 1)
        consent, _, _ = access_rows(session, student, 'target')
        approved_request = PendingConsent(
            profile_id=student.id, parent_email=consent.parent_email, director_email='', director_name='',
            approve_hash=hash_invitation_token('synthetic-original-approval'), notice_version=consent.notice_version,
            created_at=NOW - timedelta(days=2), approved_at=NOW, confirmed_at=NOW, expires_at=NOW,
        )
        session.add(approved_request)
        session.flush()
        token = child_authorization.derived_token(approved_request, 'withdraw')
        consent.withdrawal_hash = hash_invitation_token(token)
    with play_pg() as session, session.begin():
        anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id), now=NOW)

    client = TestClient(app)
    path = f'/family/withdraw/{token}'
    form = client.get(path)
    assert form.status_code == 200
    assert client.post(path, data={'csrf': form.context['csrf'], 'withdraw': 'yes'}).status_code == 200

    with play_pg() as session:
        deleted = session.get(WoodchuckProfile, student.id)
        assert deleted.status == 'deleted' and deleted.session_version == student.session_version + 1
        assert session.get(ConsentEvidence, consent.id).withdrawn_at == withdrawn_at


def test_inconsistent_preset_owner_blocks_atomically(play_pg):
    with play_pg() as session:
        student, other = profile(session, 1), profile(session, 2)
        preset = PracticeEmailPreset(profile_id=student.id, display_name='Target', email='preset@example.invalid')
        session.add(preset)
        session.flush()
        chart = PracticeChart(profile_id=other.id, practice_date=NOW.date(), minutes=10,
                              instrument='Flute', source='p-book', ordinary_email_preset_id=preset.id)
        session.add(chart)
        session.commit()
    with pytest.raises(DeletionDataConflict, match='another account.*chart'):
        with play_pg() as session, session.begin():
            anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id), now=NOW)
    with play_pg() as session:
        assert session.get(WoodchuckProfile, student.id).status == 'active'
        assert session.get(PracticeEmailPreset, preset.id).email == 'preset@example.invalid'
        assert session.get(PracticeChart, chart.id).ordinary_email_preset_id == preset.id


def test_inconsistent_director_consent_owner_blocks_atomically(play_pg):
    with play_pg() as session:
        student, other = profile(session, 1), profile(session, 2)
        consent, _, _ = access_rows(session, student, 'target')
        _, _, director = access_rows(session, other, 'other')
        director.consent_id = consent.id
        session.commit()
    with pytest.raises(DeletionDataConflict, match='another account.*consent'):
        with play_pg() as session, session.begin():
            anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id), now=NOW)
    with play_pg() as session:
        assert session.get(WoodchuckProfile, student.id).status == 'active'
        retained = session.get(DirectorPermission, director.id)
        assert (retained.profile_id, retained.consent_id, retained.link_hash) == (
            other.id, consent.id, director.link_hash,
        )


def test_stale_state_writer_cannot_overwrite_the_tombstone(play_pg):
    with play_pg() as session, session.begin():
        student = profile(session, 1)
        declare_age(session, student.id, 'adult', at=NOW)
    with play_pg() as stale:
        cached = stale.get(WoodchuckProfile, student.id)
        with play_pg() as session, session.begin():
            anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id), now=NOW)
        assert cached.status == 'active'
        with pytest.raises(AgeScreenRequired):
            lock_state(stale, student.id)
    with play_pg() as session:
        state = session.get(WoodchuckState, student.id)
        assert state.state_json == {'account': {'authenticated': False, 'deleted': True}}
        assert state.revision == 2


def test_state_read_rechecks_authorization_after_deletion_commits(play_pg, monkeypatch):
    with play_pg() as session, session.begin():
        student = profile(session, 1)
        declare_age(session, student.id, 'adult', at=NOW)

    client = TestClient(app)
    assert client.post('/account/login', data={'woodchuck_id': student.woodchuck_id, 'pin': '1234'}).status_code == 200
    assert client.get('/account/state').json()['authenticated'] is True
    authenticate = account_routes.current_profile
    scheduled = []

    def commit_deletion_after_first_authorization(request, session):
        authenticated = authenticate(request, session)
        if request.url.path == '/account/state' and authenticated is not None and not scheduled:
            with play_pg() as deletion, deletion.begin():
                anonymize_woodchuck_account(deletion, profile=deletion.get(WoodchuckProfile, student.id), now=NOW)
            scheduled.append(True)
        return authenticated

    monkeypatch.setattr(account_routes, 'current_profile', commit_deletion_after_first_authorization)
    response = client.get('/account/state')
    assert scheduled == [True] and response.status_code == 401
    assert 'state' not in response.json()


@pytest.mark.parametrize('delivery_kind', ['chart', 'invitation'])
def test_creation_does_not_send_after_deletion_commits(play_pg, monkeypatch, delivery_kind):
    with play_pg() as session, session.begin():
        student = profile(session, 1)
        declare_age(session, student.id, 'adult', at=NOW)
        verifier = TrustedVerifier(email='reviewer@example.invalid', display_name='Reviewer', pin_hash='synthetic')
        session.add(verifier)
        session.flush()
        session.add(StudentVerifierConnection(profile_id=student.id, verifier_id=verifier.id,
                                               status='accepted', role='verifier'))

    if delivery_kind == 'chart':
        module, create = practice_chart_routes, 'create_practice_chart_verification_request'
        path = '/practice-charts'
        request = {'json': {
            'verifier_id': verifier.id, 'minutes': 10, 'note': 'Synthetic private chart note',
            'practice_date': datetime.now(practice_chart_routes.CENTRAL).date().isoformat(),
            'submission_key': 'synthetic-delivery-race', 'include_contests': False, 'include_team_contests': False,
        }}
    else:
        module, create = verifier_routes, 'create_trusted_verifier_invitation'
        path = '/trusted-verifiers/invitations'
        request = {'data': {'email': 'new-reviewer@example.invalid', 'role': 'verifier'}}

    create_record = getattr(module, create)
    scheduled = []
    sent = []

    class CaptureMail:
        def send_practice_chart(self, **kwargs):
            sent.append(kwargs)
            return DeliveryResult(True, 'captured')

        send_invitation = send_practice_chart

    def commit_deletion_before_delivery(*args, **kwargs):
        created = create_record(*args, **kwargs)
        scheduled.append(True)
        with play_pg() as session, session.begin():
            anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id),
                                        now=datetime.now(timezone.utc))
        return created

    monkeypatch.setattr(module, create, commit_deletion_before_delivery)
    monkeypatch.setattr(module, 'EmailService', CaptureMail)
    client = TestClient(app)
    assert client.post('/account/login', data={'woodchuck_id': student.woodchuck_id, 'pin': '1234'}).status_code == 200
    assert client.post(path, **request).status_code == 401
    assert scheduled == [True] and sent == []


def test_delayed_analytics_cannot_recreate_deleted_activity(play_pg, monkeypatch):
    with play_pg() as session, session.begin():
        student = profile(session, 1)
        declare_age(session, student.id, 'adult', at=NOW - timedelta(days=1))
    eligible = Event()
    continue_write = Event()
    actual_lock = analytics.lock_active_profile_id

    def wait_for_deletion(session, profile_id):
        eligible.set()
        if continue_write.wait(5):
            return actual_lock(session, profile_id)
        raise TimeoutError('Test did not release the delayed observation')

    monkeypatch.setattr(analytics, 'lock_active_profile_id', wait_for_deletion)
    monkeypatch.setattr(analytics, '_retry_after', 0)
    monkeypatch.setenv('WOODSHED_ANALYTICS_ENABLED', '1')
    with ThreadPoolExecutor(max_workers=1) as executor:
        write = executor.submit(analytics.record_event, play_pg, profile_id=student.id,
                                event_type='arcade_entered', occurred_at=NOW)
        try:
            assert eligible.wait(5)
            with play_pg() as session, session.begin():
                anonymize_woodchuck_account(session, profile=session.get(WoodchuckProfile, student.id), now=NOW)
        finally:
            continue_write.set()
        write.result(timeout=5)
    with play_pg() as session:
        assert session.scalar(select(AnalyticsEvent).where(AnalyticsEvent.profile_id == student.id)) is None
