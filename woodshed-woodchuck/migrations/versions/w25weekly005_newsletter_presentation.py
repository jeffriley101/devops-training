"""Reserve one automatic Newsletter attempt per student/edition; no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "w25weekly005"
down_revision = "w25weekly004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("weekly_newsletter_presentations",
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("profile_id", "week_start"))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE weekly_newsletter_presentations IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE weekly_newsletter_presentations SET profile_id = profile_id WHERE 0"))
    else:
        raise RuntimeError("newsletter_presentation_unsupported_database")
    if bind.scalar(sa.text("SELECT count(*) FROM weekly_newsletter_presentations")):
        raise RuntimeError("newsletter_presentations_in_use: downgrade refused")
    op.drop_table("weekly_newsletter_presentations")
