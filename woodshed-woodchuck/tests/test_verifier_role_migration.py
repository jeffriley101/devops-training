from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models import (TrustedVerifier, TrustedVerifierInvitation, PracticeChart,
                        PracticeChartVerification)
from test_band_director_roster import add_student


def test_role_migration_preserves_all_other_data(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'roles.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "l2g3h4i5j6k7")
    engine = create_engine(url)
    factory = sessionmaker(engine)
    roles = ("guardian", "private_teacher", "coach", "other_trusted_adult", "parent", "band_director", "mentor")
    with factory() as session:
        session.add(TrustedVerifier(id=1, email="adult@example.com", display_name="Adult", pin_hash="unchanged"))
        session.commit()
    for role in roles:
        for status in ("pending", "accepted", "rejected", "disconnected"):
            student = add_student(factory, f"{role}-{status}", role=role, status=status)
            with factory() as session:
                session.add(TrustedVerifierInvitation(profile_id=student, role=role,
                    email="adult@example.com", status=status, token_hash=f"token-{student}",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
                chart = PracticeChart(profile_id=student, practice_date=datetime.now().date(),
                                      minutes=15, instrument="Trumpet")
                session.add(chart)
                session.flush()
                session.add(PracticeChartVerification(practice_chart_id=chart.id, verifier_id=1,
                                                     status="approved", response_note="Keep history"))
                session.commit()
    tables = ("trusted_verifier_invitations", "student_verifier_connections",
              "trusted_verifiers", "practice_charts", "practice_chart_verifications")
    def snapshot():
        with engine.connect() as connection:
            return {table: [dict(row) for row in connection.execute(text(
                f"SELECT * FROM {table} ORDER BY id")).mappings()] for table in tables}
    before = snapshot()
    command.upgrade(config, "head")
    after = snapshot()
    for table in tables:
        expected = [dict(row) for row in before[table]]
        for row in expected:
            if row.get("role") in roles[:4]:
                row["role"] = "mentor"
        assert after[table] == expected
    command.upgrade(config, "head")
    assert snapshot() == after
    command.downgrade(config, "l2g3h4i5j6k7")
    downgraded = snapshot()
    for table in tables:
        expected = [dict(row) for row in after[table]]
        for row in expected:
            if row.get("role") == "mentor":
                row["role"] = "other_trusted_adult"
        assert downgraded[table] == expected
    command.upgrade(config, "head")
    assert snapshot() == after
    engine.dispose()
