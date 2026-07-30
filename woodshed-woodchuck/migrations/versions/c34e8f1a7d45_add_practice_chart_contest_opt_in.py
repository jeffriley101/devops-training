"""add practice chart contest opt in

Revision ID: c34e8f1a7d45
Revises: a21c4e7d9b32
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c34e8f1a7d45"
down_revision: Union[str, Sequence[str], None] = "a21c4e7d9b32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "practice_charts",
        sa.Column(
            "include_contests", sa.Boolean(), nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("practice_charts", "include_contests")
