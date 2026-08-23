"""add durable Plunge Burrow personal best

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-08-22
"""

from alembic import op
import sqlalchemy as sa


revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("woodchuck_profiles") as batch:
        batch.add_column(
            sa.Column(
                "plunge_best_score",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "ck_woodchuck_profile_plunge_best_score",
            "plunge_best_score >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("woodchuck_profiles") as batch:
        batch.drop_constraint(
            "ck_woodchuck_profile_plunge_best_score",
            type_="check",
        )
        batch.drop_column("plunge_best_score")
