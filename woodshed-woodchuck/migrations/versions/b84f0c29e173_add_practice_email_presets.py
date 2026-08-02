"""add practice email presets

Revision ID: b84f0c29e173
Revises: a27c8d41f6b2
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa


revision = "b84f0c29e173"
down_revision = "a27c8d41f6b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "practice_email_presets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["woodchuck_profiles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("profile_id", "email", name="uq_practice_email_preset_profile_email"),
    )
    op.create_index("ix_practice_email_presets_profile_id", "practice_email_presets", ["profile_id"])
    with op.batch_alter_table("practice_charts") as batch:
        batch.add_column(sa.Column("ordinary_email_preset_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("ordinary_email_attempted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("ordinary_email_sent_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("ordinary_email_error_code", sa.String(40), nullable=True))
        batch.create_foreign_key(
            "fk_practice_charts_ordinary_email_preset_id", "practice_email_presets",
            ["ordinary_email_preset_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_practice_charts_ordinary_email_preset_id", ["ordinary_email_preset_id"])


def downgrade() -> None:
    with op.batch_alter_table("practice_charts") as batch:
        batch.drop_index("ix_practice_charts_ordinary_email_preset_id")
        batch.drop_constraint("fk_practice_charts_ordinary_email_preset_id", type_="foreignkey")
        batch.drop_column("ordinary_email_error_code")
        batch.drop_column("ordinary_email_sent_at")
        batch.drop_column("ordinary_email_attempted_at")
        batch.drop_column("ordinary_email_preset_id")
    op.drop_table("practice_email_presets")
