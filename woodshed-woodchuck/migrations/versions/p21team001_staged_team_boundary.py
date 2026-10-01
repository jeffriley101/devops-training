"""Stage approval without authority; freeze the closing legacy roster at cutover."""
from alembic import op
import sqlalchemy as sa

revision = "p21team001"
down_revision = "p20team001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("persistent_team_control") as batch:
        batch.add_column(sa.Column("staged_for", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("staged_plan", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("staged_plan_sha256", sa.String(64), nullable=True))
        batch.create_check_constraint("ck_persistent_team_control_staging",
            "(staged_for IS NULL AND staged_plan IS NULL AND staged_plan_sha256 IS NULL) OR "
            "(staged_for IS NOT NULL AND staged_plan IS NOT NULL AND staged_plan_sha256 IS NOT NULL)")
    op.add_column("contest_weeks", sa.Column("team_roster_frozen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    bind = op.get_bind()
    if (bind.scalar(sa.text("SELECT count(*) FROM persistent_team_control WHERE staged_for IS NOT NULL OR activated_at IS NOT NULL"))
            or bind.scalar(sa.text("SELECT count(*) FROM contest_weeks WHERE team_roster_frozen_at IS NOT NULL"))):
        raise RuntimeError("persistent_team_boundary_in_use: downgrade refused")
    with op.batch_alter_table("contest_weeks") as batch:
        batch.drop_column("team_roster_frozen_at")
    with op.batch_alter_table("persistent_team_control") as batch:
        batch.drop_constraint("ck_persistent_team_control_staging", type_="check")
        batch.drop_column("staged_plan_sha256")
        batch.drop_column("staged_plan")
        batch.drop_column("staged_for")
