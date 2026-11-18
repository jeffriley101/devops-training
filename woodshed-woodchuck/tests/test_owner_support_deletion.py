"""Owner-shell cases against a new private PostgreSQL cluster and the application login."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import pty
import select
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import select as db_select, text

from app import parent_deletion_support as support, support_deletion_service as service
from app.age_privacy import declare_age
from app.child_models import ConsentEvidence
from app.contests import contest_results_payload, ensure_band_camp_data, finalize_contest_week, team_leaderboards
from app.models import (
    ArcadeHighScore, ContestWeek, PracticeChart, PracticeChartVerification, RewardGrant,
    Season, TrustedVerifier, WoodchuckProfile,
)
from app.teams import create_and_join_team, select_team
from tests.test_account_deletion import profile
from tests.test_mediated_support_deletion import APP_ROLE, TEST_SECRET, app_db, app_template
from tests.test_play_deletion_cleanup import access_rows
from tests.test_team_contests import FINAL_NOW, NOW, add_chart


REF = "owner-case-001"
TOKEN = "synthetic-owner-admin-credential"


def save_json(path, values):
    path.touch(mode=0o600, exist_ok=True)
    path.write_text(json.dumps(values))


@pytest.fixture(params=["guardian", "adult"])
def case(request, app_db, tmp_path, monkeypatch):
    adult = request.param == "adult"
    now = datetime.now(timezone.utc)
    with app_db.begin() as db:
        name, oid = db.execute(text("SELECT datname, oid FROM pg_database WHERE datname=current_database()")).one()
        child, other_child = profile(db, 1), profile(db, 2)
        child.created_at = other_child.created_at = NOW - timedelta(days=30)
        people = [TrustedVerifier(email=label+"@example.test", display_name=label, pin_hash=child.pin_hash,
                                  created_at=child.created_at) for label in ("owner-test", "other-test")]
        scores = [ArcadeHighScore(profile_id=row.id, game_key="thirds", best_score=47) for row in (child, other_child)]
        other_chart = PracticeChart(profile_id=other_child.id, practice_date=now.date(), minutes=10,
                                    instrument="Flute", include_contests=False, include_team_contests=False)
        db.add_all([*people, *scores, other_chart])
        db.flush()
        subject, other = people if adult else (child, other_child)
    key = "verifier_id" if adult else "profile_id"
    record = {
        "case": REF, "status": "approved", key: subject.id, "requester_ref": "requester-A",
        "identity_ref": "identity-A", "authority_ref": "authority-A", "verified_by": "woodshed-owner",
        "verified_at": (now-timedelta(minutes=6)).isoformat(), "gmail_thread_id": "18fafedcb001",
        "verification_method": "Account mailbox challenge" if adult else "Guardian record and trusted callback",
        "disputed": False, "reviewed_by": None, "confirmation": {
            "case": REF, key: subject.id, "requester_ref": "requester-A", "reference": "response-A",
            "action": support.ADULT_ACTION if adult else support.ACTION, "at": (now-timedelta(minutes=2)).isoformat(),
        },
    }
    if adult:
        record.update(email=subject.email, verifier_created_at=subject.created_at.isoformat())
    else:
        record.update(woodchuck_id=subject.woodchuck_id, profile_created_at=subject.created_at.isoformat())
    path = tmp_path / (REF + ".json")
    save_json(path, record)
    save_json(tmp_path / ".target.json", dict(database=name, database_oid=oid, role=APP_ROLE,
                                               session_secret_sha256=sha256(TEST_SECRET.encode()).hexdigest()))
    for name, val in {"RENDER": "true", "RENDER_SERVICE_TYPE": "web", "SITE_ADMIN_TOKEN": TOKEN,
                      "RENDER_EXTERNAL_HOSTNAME": "woodshed-woodchuck.onrender.com",
                      "SESSION_SECRET": TEST_SECRET}.items():
        monkeypatch.setenv(name, val)
    monkeypatch.setattr(service, "SessionLocal", app_db)
    monkeypatch.setattr(service.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(service.getpass, "getpass", lambda _: TOKEN)
    action = "execute-adult" if adult else "execute"
    return SimpleNamespace(adult=adult, key=key, record=record, path=path, child=child, other_child=other_child,
                           subject=subject, other=other, other_adult=people[1], score=scores[0].id,
                           other_score=scores[1].id, other_chart=other_chart.id,
                           action=action, run=lambda: service.execute_case(action, REF, owner_dir=tmp_path),
                           receipt=tmp_path/"receipts"/(REF+".result.json"))


def test_owner_shell_command_and_repeat(case, app_db):
    # Exercise the published CLI, including the real no-echo prompt with a disposable credential.
    directory = case.path.parent
    with app_db.begin() as db:
        consent, _, _ = access_rows(db, db.get(WoodchuckProfile, case.child.id), "owner-guardian")
        historical = PracticeChartVerification(practice_chart_id=case.other_chart, status="approved",
            verifier_id=case.subject.id if case.adult else case.other_adult.id,
            response_note="Synthetic historical review", responded_at=NOW)
        db.add(historical)
    env = {**os.environ, "DATABASE_URL": app_db.kw["bind"].url.render_as_string(hide_password=False)}
    master, terminal = pty.openpty()
    try:
        with subprocess.Popen([sys.executable, "-B", "-m", "app.support_deletion_service",
                               case.action, REF, "--owner-dir", str(directory)],
                              stdin=terminal, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=True, env=env) as proc:
            try:
                assert select.select([proc.stderr], [], [], 20)[0]
                assert b"site-admin credential" in os.read(proc.stderr.fileno(), 256)
                os.write(master, (TOKEN+"\n").encode())
                stdout, stderr = proc.communicate(timeout=30)
            finally:
                if proc.poll() is None:
                    proc.kill()
            assert proc.returncode == 0, stderr
    finally:
        os.close(terminal)
        os.close(master)
    result = json.loads(stdout)
    assert result["status"] == ("adult_deletion_applied" if case.adult else "deletion_applied")
    assert set(result["disposal"].values()) == {"verified"} and result["executed_by"] == "woodshed-owner"
    original = case.receipt.read_bytes()
    assert json.loads(original) == {**result, "intent_sha256": support._digest(json.loads(
        (case.receipt.parent / (REF+".intent.json")).read_bytes()))}
    assert case.run() == result
    assert service.execute_case(case.action.replace("execute", "status"), REF, owner_dir=directory) == result
    assert case.receipt.read_bytes() == original
    with app_db() as db:
        assert db.get(WoodchuckProfile, case.child.id).status == ("active" if case.adult else "deleted")
        if case.adult:
            retired = db.get(TrustedVerifier, case.subject.id)
            assert retired.email.endswith("@deleted.invalid") and retired.pin_hash != case.subject.pin_hash
        assert db.get(WoodchuckProfile, case.other_child.id).status == "active"
        assert db.get(TrustedVerifier, case.other_adult.id).email == case.other_adult.email
        retained = db.get(ConsentEvidence, consent.id)
        assert (retained.profile_id, retained.notice_sha256, retained.parent_email) == (
            consent.profile_id, consent.notice_sha256, consent.parent_email)
        review = db.get(PracticeChartVerification, historical.id)
        assert (review.verifier_id, review.status, review.response_note) == (
            historical.verifier_id, "approved", "Synthetic historical review")
        assert (db.get(ArcadeHighScore, case.score) is not None) == case.adult
        assert db.get(ArcadeHighScore, case.other_score) is not None
    case.record["gmail_thread_id"] = "18fafedcb002"
    save_json(case.path, case.record)
    with pytest.raises(support.SupportDenied, match="approval_changed"):
        case.run()


@pytest.mark.parametrize(("problem", "reason"), [
    ("wrong_account", "mismatch_or_unavailable"), ("missing_thread", "support_owner_verification_required"),
    ("missing_method", "support_owner_verification_required"), ("missing_authority", "support_case_invalid"),
    ("no_confirmation", "support_case_invalid"), ("wrong_requester", "support_confirmation_mismatch"),
    ("wrong_actor", "support_owner_verification_required"), ("expired_authority", "support_case_expired"),
    ("expired_confirmation", "support_case_expired"), ("disputed", "support_disputed_case"),
    ("changed_at_commit", "support_approval_changed"), ("admin_at_commit", "support_owner_admin_required"),
    ("case_only", "support_owner_verification_required"), ("wrong_action", "support_confirmation_mismatch"),
    ("revoked", "support_case_invalid"),
])
def test_owner_denial_preserves_accounts(case, app_db, monkeypatch, problem, reason):
    record = case.record
    if problem == "wrong_account":
        record[case.key] = record["confirmation"][case.key] = case.other.id
    elif problem in ("missing_thread", "missing_method", "missing_authority", "no_confirmation"):
        record.pop({"missing_thread": "gmail_thread_id", "missing_method": "verification_method",
                    "missing_authority": "authority_ref", "no_confirmation": "confirmation"}[problem])
    elif problem == "wrong_requester":
        record["confirmation"]["requester_ref"] = "someone-else"
    elif problem == "wrong_action":
        record["confirmation"]["action"] = "REVIEW THIS ACCOUNT"
    elif problem == "case_only":
        record = {"case": REF}
    elif problem == "revoked":
        record["status"] = "withdrawn"
    elif problem == "wrong_actor":
        record["verified_by"] = "another-person"
    elif problem == "expired_authority":
        record["verified_at"] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    elif problem == "expired_confirmation":
        old = datetime.now(timezone.utc)-timedelta(hours=1)
        record["verified_at"] = record["confirmation"]["at"] = old.isoformat()
    elif problem == "disputed":
        record.update(disputed=True, reviewed_by="another-reviewer")
    else:
        publish = support._write_new
        def intervene(path, value):
            publish(path, value)
            if path.name.endswith(".intent.json"):
                if problem == "changed_at_commit":
                    save_json(case.path, {**record, "status": "withdrawn"})
                else:
                    monkeypatch.setenv("SITE_ADMIN_TOKEN", "rotated-synthetic-credential")
        monkeypatch.setattr(support, "_write_new", intervene)
    save_json(case.path, record)
    with pytest.raises(support.SupportDenied, match=reason):
        case.run()
    assert not case.receipt.exists()
    with app_db() as db:
        assert db.get(WoodchuckProfile, case.child.id).status == "active"
        assert db.get(WoodchuckProfile, case.other_child.id).status == "active"
        if case.adult:
            assert db.get(TrustedVerifier, case.subject.id).email == case.subject.email
        assert db.get(ArcadeHighScore, case.score) and db.get(ArcadeHighScore, case.other_score)


def test_owner_requires_post_commit_readback_before_receipt(case, app_db, monkeypatch):
    check_target = support.ApplicationDeletionTarget.require
    def unavailable(target, db):
        check_target(target, db)
        if db.scalar(text("SHOW transaction_read_only")) == "on":
            assert db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            raise support.SupportDenied("synthetic_readback_unavailable")
    with monkeypatch.context() as temporary:
        temporary.setattr(support.ApplicationDeletionTarget, "require", unavailable)
        with pytest.raises(support.SupportDenied, match="synthetic_readback_unavailable"):
            case.run()
    assert not case.receipt.exists()
    with app_db() as db:
        if case.adult:
            assert db.get(TrustedVerifier, case.subject.id).email.endswith("@deleted.invalid")
        else:
            assert db.get(WoodchuckProfile, case.subject.id).status == "deleted"
    receipt = service.execute_case(case.action.replace("execute", "status"), REF, owner_dir=case.path.parent)
    assert case.receipt.exists() and receipt["executed_by"] == "woodshed-owner"
    saved = case.receipt.read_bytes()
    intent = case.receipt.with_name(REF + ".intent.json")
    intent.rename(case.path.parent / "archived-intent.json")
    with pytest.raises(support.SupportDenied, match="support_execution_unreconciled"):
        case.run()
    assert not intent.exists() and case.receipt.read_bytes() == saved


def test_owner_confirmation_expires_during_transaction(case, app_db, monkeypatch):
    start = datetime.fromisoformat(case.record["confirmation"]["at"])
    intent = case.receipt.with_name(REF+".intent.json")
    fulfill = service.fulfill
    def advance_at_commit(*args, **kwargs):
        return fulfill(*args, **kwargs, clock=lambda: start + timedelta(minutes=30) if intent.exists() else start)
    monkeypatch.setattr(service, "fulfill", advance_at_commit)
    with pytest.raises(support.SupportDenied, match="case_expired"):
        case.run()
    assert intent.exists() and not case.receipt.exists()
    with app_db() as db:
        assert db.get(WoodchuckProfile, case.child.id).status == "active"
        if case.adult:
            assert db.get(TrustedVerifier, case.subject.id).email == case.subject.email


@pytest.mark.parametrize("problem", ["no_terminal", "wrong_admin", "wrong_service"])
def test_owner_authentication_precedes_case_access(tmp_path, monkeypatch, problem):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_SERVICE_TYPE", "web")
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "wrong.onrender.com" if problem == "wrong_service"
                       else "woodshed-woodchuck.onrender.com")
    monkeypatch.setenv("SITE_ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(service.sys.stdin, "isatty", lambda: problem != "no_terminal")
    monkeypatch.setattr(service.getpass, "getpass", lambda _: "incorrect" if problem == "wrong_admin" else TOKEN)
    with pytest.raises(support.SupportDenied, match="support_(owner_admin|interactive_owner_shell)_required"):
        service.execute_case("execute", REF, owner_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_owner_preserves_standings_and_awards(case, app_db):
    with app_db() as db:
        season, _, week = ensure_band_camp_data(db, now=NOW)
        student, peer = (db.get(WoodchuckProfile, row.id) for row in (case.child, case.other_child))
        for member in (student, peer):
            declare_age(db, member.id, "13to17", at=member.created_at)
        team, _ = create_and_join_team(db, profile=student, season=season, name="Flute Team",
                                       emblem_key="shield:gold", now=NOW)
        select_team(db, profile=peer, season=season, team=team, now=NOW)
        add_chart(db, student, team.id, 45, created_at=NOW)
        add_chart(db, peer, team.id, 30, created_at=NOW)
        db.commit()
        finalize_contest_week(db, week_start=week.week_start, now=FINAL_NOW)
        db.commit()
    def standings():
        with app_db() as db:
            period = db.get(ContestWeek, week.id)
            results = [row for row in contest_results_payload(db, period)["results"]
                       if row["subject_type"] in ("team", "instrument")]
            board = team_leaderboards(db, season=db.get(Season, season.id), contest_week=period)
            awards = db.execute(db_select(RewardGrant.__table__).where(
                RewardGrant.profile_id == case.other_child.id).order_by(RewardGrant.id)).all()
            return results, board, awards
    original = standings()
    assert {row["subject_type"] for row in original[0]} == {"instrument", "team"} and original[2]
    assert case.run()["executed_by"] == "woodshed-owner"
    assert standings() == original
