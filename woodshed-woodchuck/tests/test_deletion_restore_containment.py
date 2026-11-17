"""Real pre-deletion archive, actual student request, isolated restore and denied reads."""
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from app.age_privacy import declare_age
from app.main import app
from app.models import WoodchuckProfile, WoodchuckState
from tests.play_pg import pg_tool, private_postgres
from tests.test_account_deletion import profile


def test_predeletion_backup_cannot_serve_runtime_or_worker(play_pg, tmp_path):
    with play_pg() as session:
        student = profile(session, 101)
        declare_age(session, student.id, "adult")
        login_id = student.woodchuck_id
        student_id = student.id
        session.commit()
    source = play_pg.kw["bind"].url
    archive = tmp_path / "predeletion.dump"
    pg_tool("pg_dump", "-Fc", "-f", archive, "-h", source.query["host"],
            "-U", source.username, "-d", source.database)

    student_client = TestClient(app)
    assert student_client.post("/account/login", data={"woodchuck_id": login_id, "pin": "1234"}).status_code == 200
    old_session = student_client.cookies.get("session")
    assert student_client.post("/account/delete", data={
        "woodchuck_id": login_id, "pin": "1234", "confirmation": "DELETE",
    }, follow_redirects=False).status_code == 303
    previous_browser = TestClient(app)
    previous_browser.cookies.set("session", old_session)
    assert previous_browser.get("/account/state").status_code == 401
    with play_pg() as session:
        assert session.get(WoodchuckProfile, student_id).status == "deleted"
        assert session.get(WoodchuckState, student_id).state_json == {"account": {"authenticated": False, "deleted": True}}

    with private_postgres(quarantine=True) as target:
        host = target.query["host"]
        pg_tool("createdb", "-h", host, "-U", target.username, "ww_quarantine")
        target = target.set(database="ww_quarantine")
        # Even real LOGIN roles with database access cannot pass the quarantine HBA.
        for role in ("play_web", "play_worker"):
            pg_tool("createuser", "-h", host, "-U", target.username, "--login", role)
        def check_denied():
            for role in ("play_web", "play_worker"):
                with pytest.raises(subprocess.CalledProcessError) as exc:
                    pg_tool("psql", "-X", "-h", host, "-U", role, "-d", target.database, "-c", "SELECT 1")
                assert "pg_hba.conf rejects connection" in exc.value.stderr

        check_denied()
        pg_tool("pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "-h", host,
                "-U", target.username, "-d", target.database, archive)
        restored = create_engine(target)
        try:
            with restored.connect() as c:
                assert c.exec_driver_sql("SHOW listen_addresses").scalar_one() == ""
                assert c.exec_driver_sql("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL").scalar_one() == 0
                assert c.execute(select(WoodchuckProfile.status).where(
                    WoodchuckProfile.woodchuck_id == login_id)).scalar_one() == "active"
                assert c.execute(select(WoodchuckState.state_json).where(
                    WoodchuckState.profile_id == student_id)).scalar_one()["account"]["woodchuckId"] == login_id
        finally:
            restored.dispose()
        check_denied()
