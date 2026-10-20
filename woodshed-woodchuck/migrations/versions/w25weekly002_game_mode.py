"""One canonical Game Mode; preserve historical musician Level unchanged.

Game Mode requires explicit selection; Level and legacy JSON never choose it.
This application head is deliberately NOT C001 maintenance-approved.
"""

from alembic import op
import sqlalchemy as sa

revision = "w25weekly002"
down_revision = "w25weekly001"
branch_labels = None
depends_on = None

# Frozen migration contract, independent of future runtime catalogs.
CHECK = "game_mode IS NULL OR game_mode IN ('Beginner', 'Challenge', 'Performance')"


def upgrade():
    # A column-local check permits additive SQLite ALTER without copying the
    # referenced profile table or disabling foreign keys.
    op.add_column("woodchuck_profiles", sa.Column(
        "game_mode", sa.String(20),
        sa.CheckConstraint(CHECK, name="ck_woodchuck_profile_game_mode"), nullable=True))
    # Existing rows remain NULL until the student explicitly selects a mode.


def downgrade():
    # Refuse loss of canonical preferences. Serialize
    # the refusal with writers; do not reconstruct old JSON or historical Level.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE woodchuck_profiles IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE woodchuck_profiles SET id = id WHERE 0"))
    else:
        raise RuntimeError("game_mode_unsupported_database")
    if bind.scalar(sa.text("SELECT count(*) FROM woodchuck_profiles WHERE game_mode IS NOT NULL")):
        raise RuntimeError("game_mode_in_use: downgrade refused")
    op.drop_column("woodchuck_profiles", "game_mode")
