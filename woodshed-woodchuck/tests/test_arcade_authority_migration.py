from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
import pytest
from app.db import Base
from tests.test_team_families import disposable_url


@pytest.mark.parametrize('backend', ['sqlite', 'postgresql'])
def test_authority_upgrade_downgrade_reupgrade(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    command.upgrade(config, 'e18tester001')
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO woodchuck_profiles (id,woodchuck_id,display_name,pin_hash,instrument,level,goal,status,session_version,deletion_failed_attempts,plunge_best_score,created_at,updated_at) VALUES (1,'WC-LEGACY','Legacy','synthetic','Flute','Beginner','Practice','active',0,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        c.execute(text("INSERT INTO arcade_play_sessions (id,profile_id,game_key,play_token,started_at,completed_at,submitted_score,entry_cost) VALUES (1,1,'thirds','legacy-token',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,2147483647,0)"))
    command.upgrade(config, 'head')
    with engine.connect() as c:
        assert c.execute(text('SELECT authoritative_score, challenge_state FROM arcade_play_sessions')).one() == (None, None)
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
    command.downgrade(config, 'e18tester001')
    with engine.connect() as c:
        assert c.scalar(text('SELECT submitted_score FROM arcade_play_sessions')) == 2147483647
    command.upgrade(config, 'head')
    with engine.connect() as c:
        assert c.scalar(text('SELECT authoritative_score FROM arcade_play_sessions')) is None
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
    engine.dispose()
