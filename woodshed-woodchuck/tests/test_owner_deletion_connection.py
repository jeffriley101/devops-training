"""Qualify the owner command against real loopback PostgreSQL with a self-signed TLS certificate."""
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app import parent_deletion_support as support, support_deletion_service as service
from tests.play_pg import pg_tool
from tests.test_mediated_support_deletion import APP_ROLE, app_db, app_template
from tests.test_owner_support_deletion import REF, case, save_json


HOST = "dpg-synthetic-support-a"


@pytest.fixture(scope="module")
def listener(app_template):
    # The existing private cluster owns its socket, schema and synthetic login.
    directory = Path(app_template.query["host"])
    data = directory / "data"
    address = socket.inet_ntoa(bytes([127, 81, *os.urandom(2)]))
    password = secrets.token_hex(24)
    pg_tool("psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", directory, "-U", app_template.username,
            "-d", "postgres", input=f"ALTER ROLE {APP_ROLE} PASSWORD '{password}'")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=synthetic-database", "-keyout", "server.key", "-out", "server.crt"],
                   cwd=data, check=True, capture_output=True)
    (data / "server.key").chmod(0o600)
    hba = data / "pg_hba.conf"
    hba.write_text(f"host all {APP_ROLE} 127.0.0.0/8 scram-sha-256\n" + hba.read_text())
    pg_tool("pg_ctl", "-D", data, "-l", directory / "server.log", "-w", "-m", "fast",
            "-o", f"-c ssl=on -c listen_addresses={address} -k {directory} -c unix_socket_permissions=0700",
            "restart")
    return address, password


@pytest.fixture
def tcp(listener, app_db, monkeypatch):
    address, password = listener
    resolve = socket.getaddrinfo
    def local_dns(host, port, *args, **kwargs):
        assert host == HOST
        return resolve(address, port, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", local_dns)
    url = URL.create("postgresql+psycopg", APP_ROLE, password, HOST, database=app_db.kw["bind"].url.database,
                     query={"sslmode": "require"})
    engine = create_engine(url, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


def test_owner_internal_deletion_and_off_instance_receipt(case, tcp, tmp_path_factory, monkeypatch, capsys):
    target_path = case.path.parent / ".target.json"
    pins = {**json.loads(target_path.read_bytes()), "render_internal_host": HOST}
    factory = sessionmaker(tcp, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(service, "SessionLocal", factory)
    with factory() as db:
        target = service._application_target(pins)
        target.require(db)
        assert db.scalar(text("SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid=pg_backend_pid()")) is True
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
    archive = tmp_path_factory.mktemp("private-support-case")
    receipt_name, intent_name = REF + ".result.json", REF + ".intent.json"
    # Use tmpfs for the simulated Render instance where available. The private case survives its loss.
    scratch = "/dev/shm" if Path("/dev/shm").is_dir() else None
    with TemporaryDirectory(prefix="woodshed-instance-", dir=scratch) as temporary:
        instance = Path(temporary)
        shutil.copy2(case.path, instance)
        save_json(instance / ".target.json", {**pins, "database_oid": pins["database_oid"] + 1})
        assert service.main([case.action, REF, "--owner-dir", str(instance)]) == 1
        output = capsys.readouterr()
        assert output.out == "" and output.err.strip() == "support_target_mismatch"
        assert not (instance / "receipts" / intent_name).exists()
        save_json(instance / ".target.json", pins)
        result = service.execute_case(case.action, REF, owner_dir=instance)
        assert result["status"] == ("adult_deletion_applied" if case.adult else "deletion_applied")
        assert set(result["disposal"].values()) == {"verified"}
        for source in (instance / case.path.name, instance / "receipts" / receipt_name, instance / "receipts" / intent_name):
            saved = Path(shutil.copy2(source, archive))
            assert stat.S_IMODE(saved.stat().st_mode) == 0o600 and saved.read_bytes() == source.read_bytes()
    assert not instance.exists()
    assert stat.S_IMODE(archive.stat().st_mode) == 0o700
    digest = json.loads((archive / receipt_name).read_bytes())["intent_sha256"]
    assert digest == support._digest(json.loads((archive / intent_name).read_bytes()))

    fresh = tmp_path_factory.mktemp("replacement-service-shell")
    (fresh / "receipts").mkdir(mode=0o700)
    save_json(fresh / ".target.json", pins)
    shutil.copy2(archive / case.path.name, fresh)
    for name in (receipt_name, intent_name):
        shutil.copy2(archive / name, fresh / "receipts")
    assert service.execute_case(case.action.replace("execute", "status"), REF, owner_dir=fresh) == result


def test_actual_transport_and_database_identity(tcp):
    factory = sessionmaker(tcp)
    with factory() as db:
        oid = db.scalar(text("SELECT oid FROM pg_database WHERE datname=current_database()"))
        for database, expected_oid, role, host in (
            ("another_database", oid, APP_ROLE, HOST), (tcp.url.database, oid + 1, APP_ROLE, HOST),
            (tcp.url.database, oid, "another_role", HOST), (tcp.url.database, oid, APP_ROLE, "dpg-other-a"),
        ):
            with pytest.raises(support.SupportDenied, match="support_(target_mismatch|connection_unqualified)"):
                support.RenderInternalDeletionTarget(database, expected_oid, role, host).require(db)
    # Even a mismatched pool that really hands back plaintext cannot pass the runtime check.
    plain = create_engine(tcp.url.update_query_dict({"sslmode": "disable"}), hide_parameters=True)
    try:
        with sessionmaker(plain)() as db:
            assert db.scalar(text("SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid=pg_backend_pid()")) is False
            with pytest.raises(support.SupportDenied, match="support_connection_unqualified"):
                support.RenderInternalDeletionTarget(tcp.url.database, oid, APP_ROLE, HOST).require(db)
    finally:
        plain.dispose()


@pytest.mark.parametrize("change", [
    {"host": "dpg-other-a"}, {"host": HOST + ".oregon-postgres.render.com"},
    {"host": "127.0.0.1"}, {"port": 5433}, {"database": "another_db"}, {"username": "another_role"},
    {"drivername": "postgresql"}, {"query": {}}, {"query": {"sslmode": "disable"}},
    {"query": {"sslmode": "allow"}}, {"query": {"sslmode": "prefer"}},
    {"query": {"sslmode": ("require", "disable")}}, {"query": {"sslmode": "require", "host": HOST}},
    {"query": {"sslmode": "require", "hostaddr": "127.0.0.1"}},
    {"query": {"sslmode": "require", "port": "5433"}}, {"query": {"sslmode": "require", "service": "other"}},
])
def test_rejects_unqualified_internal_url(change):
    url = URL.create("postgresql+psycopg", APP_ROLE, host=HOST, database="case_db", query={"sslmode": "require"})
    target = support.RenderInternalDeletionTarget("case_db", 100, APP_ROLE, HOST)
    support._require_connection(url.set(port=5432), target)
    with pytest.raises(support.SupportDenied, match="support_connection_unqualified"):
        support._require_connection(url.set(**change), target)


@pytest.mark.parametrize("host", [None, "", "localhost", "127.0.0.1", HOST + ".example.test",
                                  HOST + ",other", HOST + ":5432", "/tmp/pg", "dpg-" + "a" * 60])
def test_rejects_invalid_owner_host_pin(host):
    with pytest.raises(support.SupportDenied, match="support_application_configuration_invalid"):
        support.RenderInternalDeletionTarget("case_db", 100, APP_ROLE, host)


def test_legacy_transport_guards():
    url = URL.create("postgresql+psycopg", APP_ROLE, host=HOST, database="case_db", query={"sslmode": "require"})
    for target_type in (support.ApplicationDeletionTarget, support.SupportTarget):
        target = target_type("case_db", 100, APP_ROLE)
        with pytest.raises(support.SupportDenied, match="support_connection_unqualified"):
            support._require_connection(url, target)
        external = url.set(host="external.example.test", query={"sslmode": "verify-full", "sslrootcert": "/ca.pem"})
        support._require_connection(external, target)
        mixed = URL.create("postgresql+psycopg", APP_ROLE, database="case_db",
                           query={"host": "/tmp/synthetic,external.example.test", "sslmode": "require"})
        with pytest.raises(support.SupportDenied, match="support_connection_unqualified"):
            support._require_connection(mixed, target)


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGPORT", "PGSERVICE", "HOSTALIASES", "LOCALDOMAIN"])
def test_internal_connection_rejects_overrides(monkeypatch, name):
    url = URL.create("postgresql+psycopg", APP_ROLE, host=HOST, database="case_db", query={"sslmode": "require"})
    monkeypatch.setenv(name, "synthetic-override")
    with pytest.raises(support.SupportDenied, match="support_connection_unqualified"):
        support._require_connection(url, support.RenderInternalDeletionTarget("case_db", 100, APP_ROLE, HOST))
