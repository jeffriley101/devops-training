"""The proposed SQL permissions must be evaluated independently of the case filter."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app import account_routes
from app.age_models import AccountPrivacy
from app.main import app
from app.models import ArcadeHighScore, PracticeChart, TrustedVerifierInvitation
from app.verifier_invitation_disposal import REMOVED_INVITATION_EMAIL
from tests.test_account_deletion import profile
from tests.test_adult_deletion_support import adult_case
from tests.test_associated_disposal_support import apply_disposal_acl, private_rows
from tests.test_deletion_contact_disposal import invitation
from tests.test_parent_deletion_support import support_case


def denied(connection, statement, **parameters):
    with pytest.raises(DBAPIError) as error, connection.begin_nested():
        connection.execute(text(statement), parameters)
    assert error.value.orig.sqlstate == "42501"


def grant_after_rollback_check(play_pg, kind, role):
    # Compare all public table and column ACLs, including the original grants.
    acls = text("""
        SELECT c.relname, '', c.relacl::text FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relacl IS NOT NULL
        UNION ALL
        SELECT c.relname, a.attname, a.attacl::text FROM pg_class c
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE c.relnamespace = 'public'::regnamespace AND a.attacl IS NOT NULL
        ORDER BY 1, 2
    """)
    with play_pg() as administrator:
        before = administrator.execute(acls).all()
        apply_disposal_acl(administrator, kind, role)
        assert administrator.execute(acls).all() != before
        administrator.rollback()
        assert administrator.execute(acls).all() == before
    with play_pg.begin() as administrator:
        apply_disposal_acl(administrator, kind, role)


def assert_restricted_login(connection, role):
    assert connection.execute(text("""
        SELECT session_user::text, current_user::text, rolsuper, rolbypassrls, rolcreaterole,
               rolcreatedb, rolreplication FROM pg_roles WHERE rolname = current_user
    """)).one() == (role, role, False, False, False, False, False)
    denied(connection, "SELECT contact_address FROM public.classroom_school_authorizations LIMIT 0")
    denied(connection, "SELECT parent_email FROM public.child_consent_evidence LIMIT 0")
    denied(connection, "SELECT actor_fingerprint FROM public.account_age_corrections LIMIT 0")
    denied(connection, "SELECT id FROM public.membership_audit_events LIMIT 0")


def test_guardian_grants_enable_cleanup_and_expose_other_accounts_at_sql_level(play_pg, support_case):
    run, _, case, _, other_id, engine = support_case
    subject = case["profile_id"]
    role = engine.url.username
    with play_pg.begin() as session:
        score, chart, sent, _ = private_rows(session, subject, "subject-acl")
        other_score, shared_chart, other_sent, _ = private_rows(session, other_id, "other-acl")
        session.get(PracticeChart, shared_chart).include_contests = True

    # Planning without ANALYZE checks SQL permissions without executing the write.
    plan_chart_delete = "EXPLAIN DELETE FROM public.practice_charts WHERE id = :chart"
    other_game = "SELECT profile_id FROM public.arcade_high_scores WHERE profile_id = :profile"
    with engine.connect() as connection:
        assert_restricted_login(connection, role)
        denied(connection, plan_chart_delete, chart=shared_chart)
        denied(connection, other_game, profile=other_id)
    grant_after_rollback_check(play_pg, "guardian", role)
    with engine.connect() as connection:
        assert_restricted_login(connection, role)
        assert connection.scalar(text(other_game), {"profile": other_id}) == other_id
        assert not connection.scalar(text("SELECT row_security_active('public.practice_charts')"))
        assert connection.execute(text(plan_chart_delete), {"chart": shared_chart}).all()
        denied(connection, "SELECT best_score FROM public.arcade_high_scores LIMIT 0")
        denied(connection, "EXPLAIN DELETE FROM public.camp_point_awards WHERE FALSE")

    result = run()
    assert result["disposal"] == dict.fromkeys(
        ("private_activity", "practice_and_plans", "age_history", "delivery_history"), "verified")
    assert run(status_only=True) == result
    with play_pg() as session:
        assert session.get(ArcadeHighScore, score) is None
        assert session.get(PracticeChart, chart) is None
        assert session.get(TrustedVerifierInvitation, sent).email_attempt_count == 0
        assert session.get(AccountPrivacy, subject) is None
        assert session.get(ArcadeHighScore, other_score).best_score == 47
        assert session.get(PracticeChart, shared_chart).include_contests
        assert session.get(TrustedVerifierInvitation, other_sent).email_attempt_count == 2
        assert session.get(AccountPrivacy, other_id).age_band == "13to17"


def test_adult_grant_is_column_limited_but_applies_to_unrelated_invitations(play_pg, adult_case):
    case = adult_case
    with play_pg.begin() as session:
        student = profile(session, 11)
        subject = invitation(student.id, case.adult.id, case.adult.email, case.now, "subject-acl")
        other = invitation(student.id, case.colleague.id, case.colleague.email, case.now, "other-acl")
        session.add_all([subject, other])
    plan_email_update = "EXPLAIN UPDATE public.trusted_verifier_invitations SET email = email WHERE id = :id"
    with case.engine.connect() as connection:
        assert_restricted_login(connection, case.target.role)
        denied(connection, plan_email_update, id=other.id)
    grant_after_rollback_check(play_pg, "adult", case.target.role)
    with case.engine.connect() as connection:
        assert_restricted_login(connection, case.target.role)
        query = "SELECT accepted_verifier_id FROM public.trusted_verifier_invitations WHERE id = :id"
        assert connection.scalar(text(query), {"id": other.id}) == case.colleague.id
        assert not connection.scalar(text("SELECT row_security_active('public.trusted_verifier_invitations')"))
        assert connection.execute(text(plan_email_update), {"id": other.id}).all()
        denied(connection, "EXPLAIN UPDATE public.trusted_verifier_invitations SET accepted_at = NULL WHERE FALSE")
    result = case.run()
    assert result["disposal"] == {"accepted_invitation_contacts": "verified"}
    assert case.run(status_only=True) == result
    with play_pg() as session:
        saved = session.get(TrustedVerifierInvitation, subject.id)
        assert (saved.email, saved.accepted_verifier_id, saved.accepted_at) == (
            REMOVED_INVITATION_EMAIL, case.adult.id, subject.accepted_at)
        assert session.get(TrustedVerifierInvitation, other.id).email == case.colleague.email


def test_student_web_handler_with_qualified_restricted_fixture_login(play_pg, support_case, monkeypatch):
    _, _, case, _, other_id, engine = support_case
    # The HTTP handler normally uses its own deployment's SessionLocal.
    with play_pg.begin() as administrator:
        apply_disposal_acl(administrator, "guardian", engine.url.username)
        score, chart, _, _ = private_rows(administrator, case["profile_id"], "web-subject")
        other_score, _, _, _ = private_rows(administrator, other_id, "web-other")
    credentials = {"woodchuck_id": case["woodchuck_id"], "pin": "1234"}
    with TestClient(app) as client:
        assert client.post("/account/login", data=credentials).status_code == 200
        monkeypatch.setattr(account_routes, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
        response = client.post("/account/delete", data={**credentials, "confirmation": "DELETE"})
        assert response.status_code == 200 and "Your Woodchuck sign-in is disabled" in response.text
        assert "Some private data still needs an authorized cleanup" not in response.text
        assert client.get("/account/me").json()["authenticated"] is False
    with play_pg() as session:
        assert session.get(ArcadeHighScore, score) is None and session.get(PracticeChart, chart) is None
        assert session.get(AccountPrivacy, case["profile_id"]) is None
        assert session.get(ArcadeHighScore, other_score).best_score == 47
