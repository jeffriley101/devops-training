"""add profile change cooldown timestamps

Revision ID: 6f4d8b0c2a11
Revises: 0c6d66da9ea3
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6f4d8b0c2a11"
down_revision: Union[str, Sequence[str], None] = "0c6d66da9ea3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "woodchuck_profiles",
        sa.Column("display_name_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "woodchuck_profiles",
        sa.Column("level_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("woodchuck_profiles", "level_changed_at")
    op.drop_column("woodchuck_profiles", "display_name_changed_at")
