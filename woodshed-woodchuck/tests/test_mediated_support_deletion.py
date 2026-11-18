"""Grant-free application deletion qualification on a private PostgreSQL socket and real OS UIDs."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import parent_deletion_support as support, support_deletion_service as service
from app.db import Base
from app.models import ArcadeHighScore, PracticeChart, TrustedVerifier, TrustedVerifierInvitation, WoodchuckProfile
from app.security import hash_pin
from app.support_case_files import _digest
from tests.play_pg import ROOT, pg_tool, private_postgres
from tests.test_account_deletion import profile
from tests.test_associated_disposal_support import private_rows
from tests.test_deletion_contact_disposal import invitation


APP_ROLE = "synthetic_deletion_app"
APPROVER, OPERATOR, OTHER = 21201, 21202, 21203
TEST_SECRET = "synthetic-independent-service-qualification-only"


@pytest.fixture(scope="module")
def app_template():
    with private_postgres() as admin:
        host = admin.query["host"]
        pg_tool("createuser", "-h", host, "-U", admin.username, "--login", APP_ROLE)
        pg_tool("createdb", "-h", host, "-U", admin.username, "-O", APP_ROLE, "deletion_app_template")
        template = admin.set(database="deletion_app_template", username=APP_ROLE)
        ddl = os.getenv("WW_PLAY_SUPPORT_TEST_DDL")
        if ddl:
            pg_tool("psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", host, "-U", APP_ROLE,
                    "-d", template.database, "-f", ddl)
        else:
            # Portable synthetic model fixture; the optional schema archive exercises the migrated revision.
            bootstrap = create_engine(template)
            with bootstrap.begin() as c:
                Base.metadata.create_all(c)
                c.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
                c.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": support.REVISION})
            bootstrap.dispose()
        yield admin


@pytest.fixture
def app_db(app_template):
    admin = app_template
    name = "support_" + uuid4().hex
    pg_tool("createdb", "-h", admin.query["host"], "-U", admin.username, "-T", "deletion_app_template", name)
    engine = create_engine(admin.set(database=name, username=APP_ROLE))
    try:
        yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()
        pg_tool("dropdb", "-h", admin.query["host"], "-U", admin.username, "--force", name)


def test_application_identity_remains_ineligible_for_direct_support(app_db, tmp_path):
    with app_db() as s:
        name, oid, user, effective, admin, owns_tables = s.execute(text("""
            SELECT current_database(), d.oid, session_user, current_user,
                   r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls,
                   pg_has_role(c.relowner, 'MEMBER')
            FROM pg_database d, pg_roles r, pg_class c
            WHERE d.datname=current_database() AND r.rolname=current_user AND c.oid='public.woodchuck_profiles'::regclass
        """)).one()
        assert (user, effective, admin, owns_tables) == (APP_ROLE, APP_ROLE, False, True)
        target = support.ApplicationDeletionTarget(name, oid, APP_ROLE)
        target.require(s)
        with pytest.raises(support.SupportDenied, match="nonadministrative"):
            support.SupportTarget(name, oid, APP_ROLE).require(s)
    with pytest.raises(support.SupportDenied):
        support.fulfill(app_db, target=target, approval_directory=tmp_path, register=tmp_path,
                        approval_uid=APPROVER, reference="no-operator")


@pytest.fixture(scope="module")
def os_custody(app_template):
    if os.environ.get("WW_PLAY_SUPPORT_MULTI_UID") != "1":
        pytest.skip("Set WW_PLAY_SUPPORT_MULTI_UID=1 for the isolated Docker multi-UID qualification")
    uid, gid = os.geteuid(), os.getegid()
    assert uid not in (0, APPROVER, OPERATOR, OTHER)
    socket_directory = app_template.query["host"]
    # The only writable host mount contains the synthetic case files for this test.
    with TemporaryDirectory(dir="/tmp", prefix="ww-case-custody-") as directory:
        root = Path(directory)
        container = subprocess.check_output([
            "docker", "run", "-d", "--pull=never", "--network=none", "--read-only",
            "--security-opt=no-new-privileges", "--cap-drop=ALL", "--cap-add=CHOWN",
            "--cap-add=DAC_OVERRIDE", "--cap-add=FOWNER",
            "--mount", f"type=bind,src={root},dst={root}",
            "--mount", f"type=bind,src={socket_directory},dst={socket_directory},readonly",
            "--mount", f"type=bind,src={ROOT / 'app'},dst=/work/app,readonly", "-w", "/work",
            "python:3.14.3-slim", "sleep", "3600",
        ], text=True).strip()

        def as_user(actor, code, *args, data=None, check=True):
            return subprocess.run(["docker", "exec", "-i", "-u", f"{actor}:{gid}", container,
                                   "python", "-B", "-c", code, *map(str, args)], text=True,
                                  input=json.dumps(data), capture_output=True, check=check)

        try:
            as_user(0, "import os, sys; os.chmod(sys.argv[1], 0o755); os.chown(sys.argv[1], 0, 0)", root)
            yield root, as_user
        finally:
            try:
                as_user(0, """import os, shutil, sys
from pathlib import Path
base = Path(sys.argv[1])
for path in base.iterdir():
    shutil.rmtree(path)
os.chown(base, int(sys.argv[2]), int(sys.argv[3]))
""", root, uid, gid)
            finally:
                subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=True)


PROVISION = """
import json, os, sys
from pathlib import Path
base = Path(sys.argv[1])
service, verifier, operator, other, gid = map(int, sys.argv[2:])
config = json.load(sys.stdin)
def directory(path, owner, mode):
    path.mkdir()
    path.chmod(mode)
    os.chown(path, owner, gid)
directory(base, 0, 0o755)
directory(base/'config', 0, 0o750)
directory(base/'approvals', verifier, 0o750)
directory(base/'register', service, 0o700)
directory(base/'requests', 0, 0o750)
for uid in (operator, other):
    lane = base/'requests'/str(uid)
    directory(lane, uid, 0o750)
    for kind in ('adult','parent'):
        directory(lane/kind, uid, 0o750)
    cfg = base/'config'/f'request-{uid}.json'
    cfg.write_text(json.dumps({k: config[k] for k in ('approval_directory','approval_uid','request_root')}))
    cfg.chmod(0o640)
path = base/'config'/'application.json'
path.write_text(json.dumps(config))
path.chmod(0o640)
"""

PUBLISH = """
import fcntl, json, os, sys
from pathlib import Path
pending = json.load(sys.stdin)
p = Path(sys.argv[1])
lock_path = p.with_suffix('.lock')
with open(lock_path, 'a') as lock:
    lock_path.chmod(0o640)
    fcntl.flock(lock, fcntl.LOCK_EX)
    tmp = p.with_suffix('.pending')
    tmp.write_text(json.dumps(pending))
    tmp.chmod(0o640)
    tmp.replace(p)
"""

REQUEST = """
import os, sys
from pathlib import Path
from app import support_deletion_request as entry
entry.CONFIG_DIR = entry.ADULT_CONFIG_DIR = Path(sys.argv[1])
capabilities = next(line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:'))
assert int(capabilities.split()[1], 16) == 0
assert not any(k in os.environ for k in ('SESSION_SECRET', 'DATABASE_URL'))
assert 'app.db' not in sys.modules and 'sqlalchemy' not in sys.modules
sys.exit(entry.main(sys.argv[2:]))
"""


@pytest.mark.parametrize("adult", [False, True], ids=["guardian", "adult"])
@pytest.mark.parametrize("scenario", [
    "approved", "wrong_subject", "wrong_operator", "pending", "revoked", "withdrawn_at_commit", "expired_at_commit",
])
def test_mediated_deletion(app_db, os_custody, monkeypatch, adult, scenario):
    root, as_user = os_custody
    reference = uuid4().hex
    base = root / reference
    approvals, register = base / "approvals", base / "register"
    action = "execute-adult" if adult else "execute"
    now = datetime.now(timezone.utc)
    with app_db.begin() as db:
        name, oid = db.execute(text("SELECT datname, oid FROM pg_database WHERE datname=current_database()")).one()
        child, neighbor = profile(db, 1), profile(db, 2)
        child.created_at = neighbor.created_at = now - timedelta(days=4)
        adults = [TrustedVerifier(email=f"{label}@example.test", display_name=label, pin_hash=hash_pin("1357"),
                                   created_at=now-timedelta(days=4)) for label in ("target", "colleague")]
        db.add_all(adults)
        db.flush()
        score, chart, _, _ = private_rows(db, child.id, "subject")
        other_score, other_chart, _, _ = private_rows(db, neighbor.id, "neighbor")
        invites = [invitation(student.id, row.id, row.email, now, row.email)
                   for student, row in zip((child, neighbor), adults, strict=True)]
        db.add_all(invites)
    person, other_person = adults if adult else (child, neighbor)
    key = "verifier_id" if adult else "profile_id"
    chosen = other_person if scenario == "wrong_subject" else person
    case = {
        "case": reference, "status": "pending" if scenario == "pending" else "approved",
        key: chosen.id, "executor_uid": OPERATOR,
        "requester_ref": "requester-01", "identity_ref": "identity-01", "authority_ref": "authority-01",
        "verified_by": "analyst-0001", "verified_at": (now - timedelta(minutes=10)).isoformat(),
        "disputed": True, "reviewed_by": "reviewer-0002",
        "confirmation": {"case": reference, key: chosen.id, "requester_ref": "requester-01",
                         "action": support.ADULT_ACTION if adult else support.ACTION,
                         "reference": "decision-01", "at": (now - timedelta(minutes=2)).isoformat()},
    }
    if adult:
        case.update(email=person.email, verifier_created_at=person.created_at.isoformat())
    else:
        case.update(woodchuck_id=person.woodchuck_id, profile_created_at=person.created_at.isoformat())
    service_config = dict(service_uid=os.geteuid(), approval_uid=APPROVER, approval_directory=str(approvals),
                          request_root=str(base/"requests"), register=str(register), database=name,
                          database_oid=oid, role=APP_ROLE, session_secret_sha256=sha256(TEST_SECRET.encode()).hexdigest())
    as_user(0, PROVISION, base, os.geteuid(), APPROVER, OPERATOR, OTHER, os.getegid(), data=service_config)
    as_user(APPROVER, PUBLISH, approvals / (reference + ".json"), data=case)
    monkeypatch.setattr(service, "CONFIG_DIR", base/"config")
    monkeypatch.setattr(service, "ADULT_CONFIG_DIR", base/"config")
    monkeypatch.setattr(service, "SessionLocal", app_db)
    monkeypatch.setenv("SESSION_SECRET", TEST_SECRET)
    if scenario in ("wrong_operator", "pending"):
        actor = OTHER if scenario == "wrong_operator" else OPERATOR
        attempt = as_user(actor, REQUEST, base/"config", action, reference, check=False)
        message = "support_operator_mismatch" if scenario == "wrong_operator" else "support_case_invalid"
        assert attempt.returncode == 1 and message in attempt.stderr
        with pytest.raises((FileNotFoundError, support.SupportDenied)):
            service.execute_case(action, reference)
    else:
        request = as_user(OPERATOR, REQUEST, base/"config", action, reference)
        assert json.loads(request.stdout)["status"] == "execution_requested"
        if scenario == "revoked":
            as_user(APPROVER, PUBLISH, approvals / (reference + ".json"), data={**case, "status": "revoked"})
        if scenario == "withdrawn_at_commit":
            path = base / "requests" / str(OPERATOR) / ("adult" if adult else "parent") / (reference + ".json")
            write_new = support._write_new
            def withdraw_after_intent(destination, value):
                write_new(destination, value)
                as_user(OPERATOR, "import os, sys; os.unlink(sys.argv[1])", path)
            monkeypatch.setattr(support, "_write_new", withdraw_after_intent)
        if scenario == "expired_at_commit":
            # Time advances only after mutation and durable intent, just before commit.
            original = service.fulfill
            def time_at_commit(*args, **kwargs):
                def clock():
                    return now + timedelta(minutes=31) if (register / (reference + ".intent.json")).exists() else now
                return original(*args, **kwargs, clock=clock)
            monkeypatch.setattr(service, "fulfill", time_at_commit)
        if scenario == "approved":
            result = service.execute_case(action, reference)
            assert result["executor_uid"] == OPERATOR and set(result["disposal"].values()) == {"verified"}
            intent = json.loads((register / (reference + ".intent.json")).read_text())
            receipt = (register / (reference + ".result.json")).read_bytes()
            assert intent[key] == person.id and intent["executor_uid"] == OPERATOR and intent["approval_sha256"] == _digest(case)
            assert json.loads(receipt)["intent_sha256"] == _digest(intent)
            assert service.execute_case(action, reference) == result
            assert service.execute_case(action.replace("execute", "status"), reference) == result
            assert (register / (reference + ".result.json")).read_bytes() == receipt
            socket_dir = app_db.kw["bind"].url.query["host"]
            assert os.access(Path(socket_dir)/".s.PGSQL.5432", os.R_OK)
            as_user(OPERATOR, """import os, socket, sys
assert 'SESSION_SECRET' not in os.environ and 'DATABASE_URL' not in os.environ
assert not os.access(sys.argv[1], os.R_OK) and not os.access(sys.argv[1], os.W_OK)
assert not os.access(sys.argv[2], os.W_OK)
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    try:
        connection.connect(sys.argv[3])
    except PermissionError:
        pass
    else:
        raise AssertionError("Operator reached the application's PostgreSQL socket")
""", register, approvals / (reference + ".json"), Path(socket_dir)/".s.PGSQL.5432")
        elif scenario == "withdrawn_at_commit":
            with pytest.raises((FileNotFoundError, support.SupportDenied)):
                service.execute_case(action, reference)
            assert (register / (reference + ".intent.json")).is_file()
        else:
            reason = {"wrong_subject": "mismatch_or_unavailable", "revoked": "request_mismatch",
                      "expired_at_commit": "case_expired"}[scenario]
            with pytest.raises(support.SupportDenied, match=reason):
                service.execute_case(action, reference)
    success = scenario == "approved"
    with app_db() as db:
        assert db.get(WoodchuckProfile, child.id).status == ("deleted" if success and not adult else "active")
        domain_email = db.get(TrustedVerifier, adults[0].id).email
        if success and adult:
            assert domain_email.endswith("@deleted.invalid")
        else:
            assert domain_email == adults[0].email
        assert (db.get(ArcadeHighScore, score) is None) == (success and not adult)
        assert (db.get(PracticeChart, chart) is None) == (success and not adult)
        original = db.get(TrustedVerifierInvitation, invites[0].id)
        assert original.email == ("deleted@invalid.local" if success else adults[0].email)
        assert original.accepted_verifier_id == adults[0].id and original.accepted_at == invites[0].accepted_at
        assert db.get(WoodchuckProfile, neighbor.id).status == "active"
        assert db.get(TrustedVerifier, adults[1].id).email == adults[1].email
        other_invite = db.get(TrustedVerifierInvitation, invites[1].id)
        assert (other_invite.profile_id, other_invite.email, other_invite.accepted_at) == (
            neighbor.id, adults[1].email, invites[1].accepted_at)
        assert db.get(ArcadeHighScore, other_score) and db.get(PracticeChart, other_chart)
    if not success:
        assert not (register / (reference + ".result.json")).exists()
