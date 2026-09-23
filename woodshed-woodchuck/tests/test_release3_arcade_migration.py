from pathlib import Path
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
import pytest
from app.db import Base
import app.models
from tests.test_team_families import disposable_url


@pytest.mark.parametrize('backend', ['sqlite', 'postgresql'])
def test_upgrade_preserves_legacy_attempts_and_schema_matches(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    command.upgrade(config, 'b14tester001')
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO woodchuck_profiles (id,woodchuck_id,display_name,pin_hash,instrument,level,goal,status,session_version,deletion_failed_attempts,plunge_best_score,created_at,updated_at) VALUES (1,'WC-LEGACY','Legacy','synthetic','Flute','Beginner','Practice','active',0,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        c.execute(text("INSERT INTO arcade_play_sessions (id,profile_id,game_key,play_token,started_at,entry_cost) VALUES (1,1,'thirds','legacy-token',CURRENT_TIMESTAMP,1)"))
    command.upgrade(config, 'head')
    with engine.connect() as c:
        assert c.execute(text('SELECT entry_cost, pack_id, attempt_number FROM arcade_play_sessions')).one() == (1,None,None)
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
    command.downgrade(config, 'b14tester001')
    assert not inspect(engine).has_table('arcade_attempt_packs')
    command.upgrade(config, 'head')
    with engine.begin() as c:
        c.execute(text("INSERT INTO arcade_attempt_packs (profile_id,game_key,cost,attempts_used,created_at) VALUES (1,'thirds',100,0,CURRENT_TIMESTAMP)"))
    with pytest.raises(RuntimeError, match='forward fix'): command.downgrade(config, 'b14tester001')
    engine.dispose()


@pytest.mark.parametrize('backend', ['sqlite', 'postgresql'])
def test_clean_upgrade_can_downgrade(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    command.upgrade(config, 'head')
    command.downgrade(config, 'b14tester001')
    engine = create_engine(url)
    try:
        assert not inspect(engine).has_table('arcade_start_requests')
        assert not inspect(engine).has_table('arcade_attempt_packs')
        with engine.connect() as c:
            assert c.scalar(text('SELECT version_num FROM alembic_version')) == 'b14tester001'
    finally:
        engine.dispose()


@pytest.mark.parametrize('backend', ['sqlite', 'postgresql'])
@pytest.mark.parametrize('activity', ['request_only', 'completed_legacy_request', 'pack', 'free_play', 'paid_play'])
def test_downgrade_refuses_before_mutating_r3_history(tmp_path, monkeypatch, backend, activity):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    command.upgrade(config, 'b14tester001')
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO woodchuck_profiles (id,woodchuck_id,display_name,pin_hash,instrument,level,goal,status,session_version,deletion_failed_attempts,plunge_best_score,created_at,updated_at) VALUES (1,'WC-RETRY','Retry','synthetic','Flute','Beginner','Practice','active',0,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            c.execute(text("INSERT INTO arcade_play_sessions (id,profile_id,game_key,play_token,started_at,entry_cost) VALUES (1,1,'thirds','legacy-retry-token',CURRENT_TIMESTAMP,1)"))
        command.upgrade(config, 'head')
        with engine.begin() as c:
            if activity in ('request_only', 'completed_legacy_request'):
                c.execute(text("INSERT INTO arcade_start_requests (profile_id,request_id,play_id) VALUES (1,'durable-retry',1)"))
                if activity == 'completed_legacy_request':
                    c.execute(text('UPDATE arcade_play_sessions SET completed_at=CURRENT_TIMESTAMP, submitted_score=0, payout=0 WHERE id=1'))
                # The request mapping is the only R3 artifact: no packs or new costs.
                assert c.scalar(text('SELECT COUNT(*) FROM arcade_attempt_packs')) == 0
                assert c.scalar(text('SELECT COUNT(*) FROM arcade_play_sessions WHERE entry_cost <> 1')) == 0
            elif activity == 'pack':
                c.execute(text("INSERT INTO arcade_attempt_packs (profile_id,game_key,cost,attempts_used,created_at) VALUES (1,'thirds',100,0,CURRENT_TIMESTAMP)"))
            else:
                c.execute(text('UPDATE arcade_play_sessions SET entry_cost=:cost WHERE id=1'),
                          {'cost': 0 if activity == 'free_play' else 100})

        def snapshot():
            with engine.connect() as c:
                return {table: c.execute(text(f'SELECT * FROM {table}')).all() for table in
                        ('arcade_start_requests', 'arcade_attempt_packs', 'arcade_play_sessions', 'woodchuck_profiles', 'alembic_version')}

        before = snapshot()
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lstrip().split()[0].upper())
        event.listen(Engine, 'before_cursor_execute', capture)
        try:
            with pytest.raises(RuntimeError, match='forward fix'):
                command.downgrade(config, 'b14tester001')
        finally:
            event.remove(Engine, 'before_cursor_execute', capture)
        assert not set(statements) & {'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP', 'TRUNCATE'}
        assert snapshot() == before
        with engine.connect() as c:
            assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
    finally:
        engine.dispose()
