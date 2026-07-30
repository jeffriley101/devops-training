"""refine student level names

Revision ID: a21c4e7d9b32
Revises: 6f4d8b0c2a11
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a21c4e7d9b32"
down_revision: Union[str, Sequence[str], None] = "6f4d8b0c2a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE woodchuck_profiles
        SET level = CASE
            WHEN level = 'College' THEN 'Honors'
            WHEN level = 'Conservatory' THEN 'College'
            ELSE level
        END
        WHERE level IN ('College', 'Conservatory')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE woodchuck_profiles
        SET level = CASE
            WHEN level = 'Honors' THEN 'College'
            WHEN level = 'College' THEN 'Conservatory'
            ELSE level
        END
        WHERE level IN ('Honors', 'College')
        """
    )
