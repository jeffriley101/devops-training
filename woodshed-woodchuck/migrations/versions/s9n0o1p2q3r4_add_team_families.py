"""Add persistent TeamFamily identity without inferring seasonal continuity.

Pause application writers for upgrade/downgrade: older processes cannot supply
the required family_id. SQLite parent-table batch recreation must run on the
dedicated FK-disabled migration connection (as provided by migrations/env.py),
never an application connection. Check all foreign keys after recreation.
"""
from alembic import op
import sqlalchemy as sa

revision = "s9n0o1p2q3r4"
down_revision = "r8m9n0o1p2q3"
branch_labels = depends_on = None


def _lock_teams(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE teams IN ACCESS EXCLUSIVE MODE"))
    elif connection.dialect.name == "sqlite":
        if connection.scalar(sa.text("PRAGMA foreign_keys")):
            raise RuntimeError(
                "TeamFamily migration requires a dedicated SQLite migration connection "
                "with foreign_keys OFF before its transaction; pause all writers."
            )
        # Starts a real SQLite write transaction before any DDL or safety check.
        connection.execute(sa.text("UPDATE teams SET id = id WHERE 0"))


def _check_sqlite_foreign_keys(connection):
    if connection.dialect.name == "sqlite":
        if connection.execute(sa.text("PRAGMA foreign_key_check")).first() is not None:
            raise RuntimeError("TeamFamily migration found foreign-key violations.")


def upgrade():
    connection = op.get_bind()
    _lock_teams(connection)
    families = op.create_table(
        "team_families",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("teams", sa.Column("family_id", sa.Integer(), nullable=True))
    teams = sa.table("teams", sa.column("id", sa.Integer()),
                     sa.column("created_at", sa.DateTime(timezone=True)),
                     sa.column("family_id", sa.Integer()))
    for team in connection.execute(sa.select(teams.c.id, teams.c.created_at).order_by(teams.c.id)).all():
        family_id = connection.execute(
            families.insert().values(created_at=team.created_at)
        ).inserted_primary_key[0]
        connection.execute(teams.update().where(teams.c.id == team.id).values(family_id=family_id))
    if connection.scalar(sa.select(sa.func.count()).select_from(teams).where(teams.c.family_id.is_(None))):
        raise RuntimeError("TeamFamily backfill left a Team without identity.")
    with op.batch_alter_table("teams") as batch:
        batch.alter_column("family_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key("fk_teams_family_id_team_families", "team_families",
                                 ["family_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_teams_family_id", ["family_id"])
        batch.create_unique_constraint("uq_team_season_family", ["season_id", "family_id"])
    _check_sqlite_foreign_keys(connection)


def downgrade():
    connection = op.get_bind()
    _lock_teams(connection)
    if connection.execute(sa.text(
        "SELECT family_id FROM teams GROUP BY family_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first() is not None:
        raise RuntimeError("Cannot downgrade: this would destroy cross-season TeamFamily continuity.")
    with op.batch_alter_table("teams") as batch:
        batch.drop_constraint("uq_team_season_family", type_="unique")
        batch.drop_index("ix_teams_family_id")
        batch.drop_constraint("fk_teams_family_id_team_families", type_="foreignkey")
        batch.drop_column("family_id")
    op.drop_table("team_families")
    _check_sqlite_foreign_keys(connection)
