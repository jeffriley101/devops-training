"""One canonical Game Mode; preserve historical musician Level unchanged.

Only exact server-persisted mode strings take precedence over legacy Beginner.
This application head is deliberately NOT C001 maintenance-approved.
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "w25weekly002"
down_revision = "w25weekly001"
branch_labels = None
depends_on = None

# Frozen migration contract, independent of future runtime catalogs.
MODES = ("Beginner", "Challenge", "Performance")
CHECK = "game_mode IS NULL OR game_mode IN ('Beginner', 'Challenge', 'Performance')"


def saved_mode(raw):
    # Decode exactly once. JSON strings containing an encoded object are not
    # preference objects. Non-text SQLite storage is unknown, not authority.
    if not isinstance(raw, str):
        return None
    try:
        raw = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    preferences = raw.get("_weekly_preferences") if isinstance(raw, dict) else None
    mode = preferences.get("game_mode") if isinstance(preferences, dict) else None
    return mode if isinstance(mode, str) and mode in MODES else None


def upgrade():
    # A column-local check permits additive SQLite ALTER without copying the
    # referenced profile table or disabling foreign keys.
    op.add_column("woodchuck_profiles", sa.Column(
        "game_mode", sa.String(20),
        sa.CheckConstraint(CHECK, name="ck_woodchuck_profile_game_mode"), nullable=True))
    connection = op.get_bind()
    # SQLite returns the stored text/unknown scalar untouched. PostgreSQL must
    # return serialized JSON, not driver-decoded strings that resemble objects.
    state_sql = "s.state_json" if connection.dialect.name == "sqlite" else "CAST(s.state_json AS TEXT)"
    rows = connection.execute(sa.text(
        f"SELECT p.id, p.level, {state_sql} FROM woodchuck_profiles p "
        "LEFT JOIN woodchuck_states s ON s.profile_id = p.id"))
    for profile_id, level, raw in rows:
        mode = saved_mode(raw)
        if mode is None and level == "Beginner":
            mode = "Beginner"
        if mode is not None:
            connection.execute(sa.text(
                "UPDATE woodchuck_profiles SET game_mode=:mode WHERE id=:id"),
                {"mode": mode, "id": profile_id})


def downgrade():
    # Refuse loss of canonical preferences, including backfilled values. Serialize
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
