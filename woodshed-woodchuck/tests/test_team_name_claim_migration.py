"""Both database engines backfill only unambiguous public name ownership."""
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Season, Team, TeamFamily
from tests.test_team_families import disposable_url

OLD, NEW = 'c15arcade001', 'd16team001'


@pytest.mark.parametrize('backend', ['sqlite', 'postgresql'])
@pytest.mark.parametrize('collision', [False, True])
def test_claim_migration_backfill_preflight_and_roundtrip(tmp_path, monkeypatch, backend, collision):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    command.upgrade(config, OLD)
    engine = create_engine(url)
    with Session(engine) as s:
        first = Season(key='old', name='Old', starts_on=date(2026, 7, 27), status='closed')
        later = Season(key='later', name='Later', starts_on=date(2026, 9, 14), status='active')
        family, other = TeamFamily(), TeamFamily()
        s.add_all([first, later, family, other]); s.flush()
        s.add_all([
            Team(season_id=first.id, family_id=family.id, display_name='Union', normalized_name='union', emblem_key='emoji:cat'),
            Team(season_id=later.id, family_id=other.id if collision else family.id,
                 display_name='Union', normalized_name='union', emblem_key='emoji:cat'),
            Team(season_id=first.id, family_id=other.id, display_name='Class One', normalized_name='class one',
                 emblem_key='emoji:dog', visibility='private', director_led=True, join_code='CLASSCODE'),
        ])
        s.commit()
        owner = family.id
    with engine.connect() as c:
        before = [tuple(r) for r in c.execute(text('SELECT * FROM teams ORDER BY id'))]
    if collision:
        with pytest.raises(RuntimeError, match='ambiguous across families'):
            command.upgrade(config, NEW)
        assert 'team_name_claims' not in inspect(engine).get_table_names()
        with engine.connect() as c:
            assert c.scalar(text('SELECT version_num FROM alembic_version')) == OLD
    else:
        for _ in range(2):
            command.upgrade(config, NEW)
            with engine.connect() as c:
                assert list(c.execute(text('SELECT normalized_name, family_id FROM team_name_claims'))) == [('union', owner)]
                ctx = MigrationContext.configure(c, opts={'include_object':
                    lambda obj, name, type_, reflected, compare_to:
                        name == 'team_name_claims' if type_ == 'table' else True})
                assert compare_metadata(ctx, Base.metadata) == []
            with pytest.raises(IntegrityError), engine.begin() as c:
                c.execute(text("INSERT INTO team_name_claims VALUES ('union', :owner)"), {'owner': owner})
            command.downgrade(config, OLD)
            assert 'team_name_claims' not in inspect(engine).get_table_names()
        command.upgrade(config, NEW)
        with engine.begin() as c:
            c.execute(text("INSERT INTO team_name_claims VALUES ('retired name', :owner)"), {'owner': owner})
        with pytest.raises(RuntimeError, match='historical public Team name claims would be lost'):
            command.downgrade(config, OLD)
    with engine.connect() as c:
        assert [tuple(r) for r in c.execute(text('SELECT * FROM teams ORDER BY id'))] == before
    engine.dispose()
