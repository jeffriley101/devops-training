"""add practice chart submission keys

Revision ID: 4d9fb7211ac8
Revises: 8a899a61d621
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d9fb7211ac8"
down_revision: Union[str, Sequence[str], None] = "8a899a61d621"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("practice_charts") as batch_op:
        batch_op.add_column(
            sa.Column("submission_key", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_practice_chart_profile_submission",
            ["profile_id", "submission_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("practice_charts") as batch_op:
        batch_op.drop_constraint(
            "uq_practice_chart_profile_submission",
            type_="unique",
        )
        batch_op.drop_column("submission_key")
