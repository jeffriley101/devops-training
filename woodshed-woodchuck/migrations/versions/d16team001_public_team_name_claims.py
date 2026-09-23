"""Reserve public Team names permanently for their families.

Pause older application writers during upgrade: they do not create claims.
No contest history or seasonal Team row is rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision = "d16team001"
down_revision = "c15arcade001"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE teams IN ACCESS EXCLUSIVE MODE"))
    elif connection.dialect.name == "sqlite":
        # Begin a real write transaction before preflight/DDL, including SQLite.
        connection.execute(sa.text("UPDATE teams SET id = id WHERE 0"))
    if connection.execute(sa.text(
        "SELECT normalized_name FROM teams WHERE visibility = 'public' "
        "GROUP BY normalized_name HAVING COUNT(DISTINCT family_id) > 1 LIMIT 1"
    )).first() is not None:
        raise RuntimeError("Public Team name ownership is ambiguous across families; resolve before upgrade.")
    op.create_table(
        "team_name_claims",
        sa.Column("normalized_name", sa.String(100), primary_key=True),
        sa.Column("family_id", sa.Integer(), sa.ForeignKey("team_families.id", ondelete="RESTRICT"), nullable=False),
    )
    op.create_index("ix_team_name_claims_family_id", "team_name_claims", ["family_id"])
    connection.execute(sa.text(
        "INSERT INTO team_name_claims (normalized_name, family_id) "
        "SELECT DISTINCT normalized_name, family_id FROM teams WHERE visibility = 'public'"
    ))


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM team_name_claims c WHERE NOT EXISTS "
        "(SELECT 1 FROM teams t WHERE t.visibility = 'public' "
        "AND t.normalized_name = c.normalized_name AND t.family_id = c.family_id) LIMIT 1"
    )).first() is not None:
        raise RuntimeError("Cannot downgrade: historical public Team name claims would be lost.")
    op.drop_index("ix_team_name_claims_family_id", table_name="team_name_claims")
    op.drop_table("team_name_claims")
