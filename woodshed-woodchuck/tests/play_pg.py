"""Opt-in PostgreSQL qualification. Only newly initialized private clusters are used."""
from contextlib import contextmanager
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]


def pg_tool(name, *args, **kwargs):
    bindir = subprocess.check_output(["pg_config", "--bindir"], text=True).strip()
    return subprocess.run(
        [str(Path(bindir) / name), *map(str, args)],
        env={"PATH": os.defpath, "LC_ALL": "C"},
        capture_output=True, text=True, check=True, **kwargs,
    )


@contextmanager
def private_postgres(*, quarantine=False):
    if os.getenv("WW_PLAY_DELETION_PG") != "1":
        pytest.skip("Set WW_PLAY_DELETION_PG=1 to create temporary PostgreSQL clusters")
    operator = "ww_restore_custodian" if quarantine else "play_test_admin"
    with TemporaryDirectory(prefix="ww-play-", dir="/tmp") as temporary:
        socket_dir = Path(temporary)
        data = socket_dir / "data"
        pg_tool("initdb", "-D", data, "-U", operator, "--no-locale",
                "--encoding=UTF8", "--auth-local=trust", "--auth-host=reject")
        if quarantine:
            shutil.copyfile(ROOT / "ops/restore-quarantine.pg_hba.conf", data / "pg_hba.conf")
            user = pwd.getpwuid(os.getuid()).pw_name
            assert re.fullmatch(r"[a-zA-Z0-9_-]+", user)
            (data / "pg_ident.conf").write_text(f"woodshed_restore {user} {operator}\n")
        pg_tool("pg_ctl", "-D", data, "-l", socket_dir / "server.log", "-w",
                "-o", f"-c listen_addresses='' -k {socket_dir} -c unix_socket_permissions=0700", "start")
        try:
            yield URL.create("postgresql+psycopg", username=operator, database="postgres",
                             query={"host": str(socket_dir)})
        finally:
            pg_tool("pg_ctl", "-D", data, "-w", "-m", "fast", "stop")


@pytest.fixture(scope="session")
def play_template():
    with private_postgres() as url:
        pg_tool("createdb", "-h", url.query["host"], "-U", url.username, "play_template")
        template = url.set(database="play_template")
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=ROOT,
                       env={"PATH": os.defpath, "DATABASE_URL": template.render_as_string(),
                            "SESSION_SECRET": "play-synthetic-template-only"})
        yield url


@pytest.fixture
def play_pg(play_template, monkeypatch):
    """Give each test an independent clone of the migrated synthetic schema."""
    name = "play_test_" + uuid4().hex
    pg_tool("createdb", "-h", play_template.query["host"], "-U", play_template.username,
            "-T", "play_template", name)
    url = play_template.set(database=name)
    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setenv("DATABASE_URL", url.render_as_string())
    for key, module in tuple(sys.modules.items()):
        if key.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()
        pg_tool("dropdb", "-h", url.query["host"], "-U", url.username, "--force", name)
