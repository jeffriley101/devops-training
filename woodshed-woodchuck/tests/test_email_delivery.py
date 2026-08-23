from __future__ import annotations

import json
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker

from app import account_routes, email_service, practice_chart_routes, verifier_routes
from app.db import Base
from app.email_service import EmailService, SMTPConfig, build_message
from app.main import app
from app.models import PracticeChart, PracticeChartVerification, TrustedVerifierInvitation


SMTP_ENV = {
    "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587",
    "SMTP_USERNAME": "woodshedwoodchuck@gmail.com", "SMTP_PASSWORD": "app-password-secret",
    "SMTP_FROM_EMAIL": "woodshedwoodchuck@gmail.com", "SMTP_FROM_NAME": "Woodshed Woodchuck",
    "SMTP_USE_TLS": "true", "PUBLIC_BASE_URL": "https://woodshed.example/app/",
}
ROOT = Path(__file__).resolve().parents[1]


class CapturingSMTP:
    messages = []
    logins = []
    tls = 0

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def ehlo(self): pass
    def starttls(self, context): type(self).tls += 1
    def login(self, username, password): type(self).logins.append((username, password))
    def send_message(self, message): type(self).messages.append(message)


@pytest.fixture()
def mail_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'mail.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (account_routes, verifier_routes, practice_chart_routes):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    for key, value in SMTP_ENV.items(): monkeypatch.setenv(key, value)
    CapturingSMTP.messages, CapturingSMTP.logins, CapturingSMTP.tls = [], [], 0
    monkeypatch.setattr(email_service.smtplib, "SMTP", CapturingSMTP)
    yield sessions
    Base.metadata.drop_all(engine); engine.dispose()


def create_student(client: TestClient) -> int:
    state = {"version": 4, "account": {}, "profile": {}, "progress": {"credits": 0}, "practiceLog": []}
    response = client.post("/account/create", data={
        "display_name": "Alex <Woodchuck>", "pin": "2468", "instrument": "Flute",
        "level": "Beginner", "goal": "Build daily consistency", "initial_state": json.dumps(state),
    })
    assert response.status_code == 200
    return response.json()["profile"]["id"]


def test_email_metadata_migration_is_additive(tmp_path, monkeypatch) -> None:
    database = tmp_path / "migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database}")
    for table in ("trusted_verifier_invitations", "practice_chart_verifications"):
        columns = {column["name"]: column for column in inspect(engine).get_columns(table)}
        assert {"last_email_attempt_at", "last_email_sent_at", "email_attempt_count", "last_email_error_code"} <= columns.keys()
        assert columns["email_attempt_count"]["nullable"] is False
    engine.dispose()


def test_complete_configuration_and_rfc_multipart_message(monkeypatch) -> None:
    for key, value in SMTP_ENV.items(): monkeypatch.setenv(key, value)
    config = SMTPConfig.from_environment()
    assert config and config.host == "smtp.gmail.com" and config.port == 587 and config.use_tls
    message = build_message(
        to_email="helper+band@example.test", subject="Café review",
        plain_text="Normal spaces stay normal. https://example.test/a+b",
        html_body="<p>Normal spaces stay normal.</p>", config=config,
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    assert message["From"] == "Woodshed Woodchuck <woodshedwoodchuck@gmail.com>"
    assert message["To"] == "helper+band@example.test"
    assert str(message["Subject"]) == "Café review"
    assert message["Date"] and message["Message-ID"] and message.is_multipart()
    assert "Normal spaces stay normal" in message.get_body(preferencelist=("plain",)).get_content()
    assert "https://example.test/a+b" in message.get_body(preferencelist=("plain",)).get_content()


@pytest.mark.parametrize("failure,code", [
    (smtplib.SMTPAuthenticationError(535, b"bad"), "authentication_failed"),
    (smtplib.SMTPRecipientsRefused({"x@example.test": (550, b"no")}), "recipient_rejected"),
    (TimeoutError(), "connection_failed"),
    (smtplib.SMTPException("secret failure"), "delivery_failed"),
])
def test_smtp_failures_are_safely_mapped(failure, code) -> None:
    config = SMTPConfig("smtp.gmail.com", 587, "user", "do-not-log", "from@example.test", "Sender", True)
    class BrokenSMTP:
        def __init__(self, *_args, **_kwargs): raise failure
    assert EmailService(config, BrokenSMTP).send(build_message(
        to_email="to@example.test", subject="Subject", plain_text="Body", html_body="<p>Body</p>", config=config,
    )).code == code


def test_missing_configuration_is_controlled(monkeypatch) -> None:
    for key in SMTP_ENV: monkeypatch.delenv(key, raising=False)
    assert SMTPConfig.from_environment() is None
    result = EmailService().send_invitation(
        recipient="adult@example.test", student_name="Student", role="teacher",
        acceptance_url="https://example.test/accept/token",
    )
    assert result.sent is False and result.code == "not_configured"


def test_invitation_without_smtp_persists_and_local_link_accepts(
    mail_database, monkeypatch,
) -> None:
    for key in SMTP_ENV:
        monkeypatch.delenv(key, raising=False)
    with TestClient(app) as student:
        profile_id = create_student(student)
        response = student.post("/trusted-verifiers/invitations", data={
            "email": "local-adult@example.test", "role": "parent",
        })
        assert response.status_code == 200
        payload = response.json()
        assert payload["created"] is True
        assert payload["email_delivery"]["code"] == "not_configured"
        assert payload["email_delivery"]["sent"] is False
        assert payload["accept_url"].startswith("http://testserver/")
        token = payload["invitation_token"]
        with mail_database() as session:
            row = session.get(TrustedVerifierInvitation, payload["invitation"]["id"])
            assert row is not None and row.status == "pending" and row.token_hash
        with TestClient(app) as verifier:
            accepted = verifier.post(
                f"/trusted-verifiers/invitations/{token}/accept",
                data={"display_name": "Local Parent", "pin": "1357"},
            )
            assert accepted.status_code == 200
            assert accepted.json()["connection"]["status"] == "accepted"
            assert verifier.post(
                f"/trusted-verifiers/invitations/{token}/accept",
                data={"display_name": "Local Parent", "pin": "1357"},
            ).status_code == 400
        connections = student.get("/trusted-verifiers/invitations").json()["connections"]
        assert len(connections) == 1
        assert connections[0]["verifier"]["display_name"] == "Local Parent"
        assert profile_id > 0


def test_invitation_and_pchart_send_after_persistence_and_resend_without_duplicates(mail_database, monkeypatch) -> None:
    with TestClient(app) as student:
        profile_id = create_student(student)
        invitation_response = student.post("/trusted-verifiers/invitations", data={"email": "adult+music@example.test", "role": "band_director"})
        assert invitation_response.status_code == 200
        invitation_payload = invitation_response.json()
        assert invitation_payload["email_delivery"]["sent"] is True
        assert invitation_payload["accept_url"].startswith("https://woodshed.example/")
        invitation_id = invitation_payload["invitation"]["id"]
        assert student.post(f"/trusted-verifiers/invitations/{invitation_id}/resend-email").status_code == 429
        with TestClient(app) as other_student:
            create_student(other_student)
            assert other_student.post(f"/trusted-verifiers/invitations/{invitation_id}/resend-email").status_code == 404

        with mail_database() as session:
            invitation = session.get(TrustedVerifierInvitation, invitation_id)
            invitation.last_email_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=61)
            session.commit()
        resend = student.post(f"/trusted-verifiers/invitations/{invitation_id}/resend-email")
        assert resend.status_code == 200 and resend.json()["email_delivery"]["sent"] is True
        with mail_database() as session:
            assert session.scalar(select(func.count()).select_from(TrustedVerifierInvitation)) == 1
            invitation = session.get(TrustedVerifierInvitation, invitation_id)
            assert invitation.last_email_sent_at and invitation.email_attempt_count == 2

        with TestClient(app) as verifier:
            accepted = verifier.post(
                f"/trusted-verifiers/invitations/{resend.json()['invitation_token']}/accept",
                data={"display_name": "Director", "pin": "1357"},
            )
            assert accepted.status_code == 200
        verifier_id = student.get("/trusted-verifiers/invitations").json()["connections"][0]["verifier"]["id"]
        chart = student.post("/practice-charts", json={
            "verifier_id": verifier_id, "practice_date": "2026-07-30", "minutes": 25,
            "note": "Long tones", "practice_details": ["Tone"], "submission_key": "smtp-chart-1",
        })
        assert chart.status_code == 201 and chart.json()["email_delivery"]["sent"] is True
        assert chart.json()["review_url"].startswith("https://woodshed.example/")
        verification_id = chart.json()["chart"]["verification"]["id"]
        assert student.post(f"/practice-charts/verifications/{verification_id}/resend-email").status_code == 429
        with mail_database() as session:
            verification = session.get(PracticeChartVerification, verification_id)
            verification.last_email_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=61)
            session.commit()
        resent_chart = student.post(f"/practice-charts/verifications/{verification_id}/resend-email")
        assert resent_chart.status_code == 200 and resent_chart.json()["email_delivery"]["sent"] is True
        duplicate = student.post("/practice-charts", json={
            "verifier_id": verifier_id, "practice_date": "2026-07-30", "minutes": 25,
            "note": "Long tones", "practice_details": ["Tone"], "submission_key": "smtp-chart-1",
        })
        assert duplicate.status_code == 201 and duplicate.json()["created"] is False and duplicate.json()["email_delivery"] is None
        with mail_database() as session:
            assert session.scalar(select(func.count()).select_from(PracticeChart)) == 1
            assert session.scalar(select(func.count()).select_from(PracticeChartVerification)) == 1

        class FailingSMTP:
            def __init__(self, *_args, **_kwargs): raise TimeoutError()
        monkeypatch.setattr(email_service.smtplib, "SMTP", FailingSMTP)
        failed_chart = student.post("/practice-charts", json={
            "verifier_id": verifier_id, "practice_date": "2026-07-31", "minutes": 10,
            "note": "Scales", "practice_details": [], "submission_key": "smtp-chart-2",
        })
        assert failed_chart.status_code == 201
        assert failed_chart.json()["email_delivery"] == {"sent": False, "code": "connection_failed", "message": "Saved, but email could not be sent"}
        with mail_database() as session:
            assert session.scalar(select(func.count()).select_from(PracticeChart)) == 2
            assert session.scalar(select(func.count()).select_from(PracticeChartVerification)) == 2

    assert len(CapturingSMTP.messages) == 4
    combined = "\n".join(message.as_string() for message in CapturingSMTP.messages)
    human_visible = "\n".join(
        [
            str(message.get(header, ""))
            for message in CapturingSMTP.messages
            for header in ("From", "To", "Subject")
        ]
        + [
            part.get_content()
            for message in CapturingSMTP.messages
            for part in message.walk()
            if part.get_content_type() in {"text/plain", "text/html"}
        ]
    )
    assert "2468" not in human_visible and "1357" not in human_visible
    assert "Alex &lt;Woodchuck&gt;" in combined
    assert CapturingSMTP.tls == 4


def test_password_and_token_are_absent_from_failure_logs(mail_database, monkeypatch, caplog) -> None:
    class FailingSMTP:
        def __init__(self, *_args, **_kwargs): raise smtplib.SMTPAuthenticationError(535, b"app-password-secret")
    monkeypatch.setattr(email_service.smtplib, "SMTP", FailingSMTP)
    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        create_student(client)
        response = client.post("/trusted-verifiers/invitations", data={"email": "adult@example.test", "role": "parent"})
    assert response.status_code == 200
    assert response.json()["email_delivery"] == {"sent": False, "code": "authentication_failed", "message": "Saved, but email could not be sent"}
    logs = caplog.text
    assert "app-password-secret" not in logs
    assert response.json()["invitation_token"] not in logs


def test_ordinary_practice_book_email_uses_owned_preset_and_is_idempotent(mail_database) -> None:
    with TestClient(app) as client:
        profile_id = create_student(client)
        preset_response = client.post("/practice-charts/email-presets", json={
            "display_name": "Band Teacher", "email": "teacher+music@example.test",
        })
        assert preset_response.status_code == 201
        preset_id = preset_response.json()["preset"]["id"]
        assert client.get("/practice-charts/email-presets").json()["presets"] == [{
            "id": preset_id, "display_name": "Band Teacher", "email": "teacher+music@example.test",
        }]
        submitted = {
            "practice_date": "2026-07-30", "minutes": 20,
            "note": "Air & tone", "practice_details": ["Long Tones"],
            "submission_key": "ordinary-email-chart", "ordinary_email_preset_id": preset_id,
        }
        first = client.post("/practice-charts", json=submitted)
        duplicate = client.post("/practice-charts", json=submitted)
        assert first.status_code == 201 and first.json()["ordinary_email_delivery"]["sent"] is True
        assert first.json()["ordinary_email"]["code"] == "sent"
        assert first.json()["verification_email"]["code"] == "not_requested"
        assert first.json()["chart"]["verification"] is None
        assert duplicate.status_code == 201 and duplicate.json()["created"] is False
        assert duplicate.json()["ordinary_email_delivery"] is None
        with mail_database() as session:
            chart = session.scalar(select(PracticeChart).where(PracticeChart.profile_id == profile_id))
            assert chart.ordinary_email_preset_id == preset_id
            assert chart.ordinary_email_sent_at is not None
            assert session.scalar(select(func.count()).select_from(PracticeChartVerification)) == 0
        assert len(CapturingSMTP.messages) == 1
        message = CapturingSMTP.messages[0]
        assert message["To"] == "teacher+music@example.test"
        assert "not a verification request" in message.get_body(preferencelist=("plain",)).get_content().lower()


def test_owned_preset_deletion_is_idempotent_and_preserves_chart(mail_database) -> None:
    with TestClient(app) as owner:
        create_student(owner)
        preset = owner.post("/practice-charts/email-presets", json={
            "display_name": "Teacher", "email": "delete-me@example.test",
        }).json()["preset"]
        chart = owner.post("/practice-charts", json={
            "practice_date": "2026-07-30", "minutes": 12,
            "submission_key": "delete-preset-chart",
            "ordinary_email_preset_id": preset["id"],
        })
        assert chart.status_code == 201
        with TestClient(app) as stranger:
            create_student(stranger)
            denied = stranger.delete(f"/practice-charts/email-presets/{preset['id']}")
            assert denied.status_code == 200 and denied.json()["deleted"] is False
        deleted = owner.delete(f"/practice-charts/email-presets/{preset['id']}")
        repeated = owner.delete(f"/practice-charts/email-presets/{preset['id']}")
        assert deleted.json()["deleted"] is True
        assert repeated.json()["deleted"] is False
        assert owner.get("/practice-charts/email-presets").json()["presets"] == []
        with mail_database() as session:
            persisted = session.scalar(select(PracticeChart).where(
                PracticeChart.submission_key == "delete-preset-chart"
            ))
            assert persisted is not None
            assert persisted.ordinary_email_attempted_at is not None


def test_book_draft_return_and_separate_delivery_status_contracts() -> None:
    template = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    trusted = (ROOT / "templates/trusted_verifiers.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    verifier_js = (ROOT / "static/js/trusted-verifiers.js").read_text(encoding="utf-8")
    routes = (ROOT / "app/practice_chart_routes.py").read_text(encoding="utf-8")
    assert 'href="/trusted-verifiers?return_to=p-book"' in template
    assert 'id="trusted-verifier-return-link"' in trusted
    assert "/p-book?restore_draft=1" in trusted + verifier_js
    assert 'P_BOOK_DRAFT_KEY = "woodshed:p-book:verifier-draft:v1"' in app_js
    for field in (
        "minutes", "practiceDate", "note", "practiceDetails", "includeContests",
        "includeTeam", "emailCopy", "requestValidation", "emailPresetId", "verifierId",
    ):
        assert field in app_js
    assert "P_BOOK_DRAFT_MAX_AGE_MS = 30 * 60 * 1000" in app_js
    assert "sessionStorage.removeItem(P_BOOK_DRAFT_KEY)" in app_js
    assert '"ordinary_email"' in routes and '"verification_email"' in routes
    assert '"not_requested"' in routes
    assert "ordinaryStatus.code === \"sent\"" in app_js
    assert "verificationStatus.code === \"sent\"" in app_js


def test_confirmation_ui_is_status_only_and_original_controls_remain() -> None:
    pbook = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    invitations = (ROOT / "templates/trusted_verifiers.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    verifier_js = (ROOT / "static/js/trusted-verifiers.js").read_text(encoding="utf-8")
    chart_routes = (ROOT / "app/practice_chart_routes.py").read_text(encoding="utf-8")
    invitation_routes = (ROOT / "app/verifier_routes.py").read_text(encoding="utf-8")

    assert 'id="p-book-email-delivery-status"' in pbook
    for redundant in (
        "p-book-review-link", "p-book-copy-review-link",
        "p-book-open-review-email", "p-book-resend-review-email",
    ):
        assert redundant not in pbook + app_js
    assert "Review link" not in pbook
    assert "Copy Review Link" not in pbook
    assert "Open in my email app" not in pbook
    assert "Resend Email" not in pbook
    assert 'id="trusted-verifier-open-email"' not in invitations
    assert 'id="trusted-verifier-resend-email"' not in invitations

    assert 'type="submit">Submit P-Chart</button>' in pbook
    assert 'id="email-p-chart-btn"' not in pbook
    assert 'id="trusted-verifier-invite-form"' in invitations
    assert 'id="trusted-verifier-copy-link"' in invitations
    assert "deliveryMessages" in app_js
    assert "email_delivery?.message" in app_js + verifier_js
    assert '@router.post("/verifications/{verification_id}/resend-email")' in chart_routes
    assert '@router.post("/invitations/{invitation_id}/resend-email")' in invitation_routes
    assert "respond_to_practice_chart_verification" in invitation_routes
