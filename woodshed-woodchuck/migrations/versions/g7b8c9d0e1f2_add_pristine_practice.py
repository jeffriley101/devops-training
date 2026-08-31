"""add Pristine practice records and contest division

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa


revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("practice_charts") as batch:
        batch.add_column(
            sa.Column("detected_playing_seconds", sa.Integer(), nullable=True)
        )

    # A downgrade intentionally keeps the ordinary P-Chart history. Rebuild
    # exact whole-minute duration if that database is upgraded again.
    op.execute(
        "UPDATE practice_charts "
        "SET detected_playing_seconds = "
        "CASE WHEN minutes > 0 THEN minutes * 60 ELSE 1 END "
        "WHERE source = 'pristine' AND detected_playing_seconds IS NULL"
    )
    with op.batch_alter_table("practice_charts") as batch:
        batch.create_check_constraint(
            "ck_practice_chart_detected_seconds_positive",
            "detected_playing_seconds IS NULL OR detected_playing_seconds > 0",
        )
        batch.create_check_constraint(
            "ck_practice_chart_pristine_source_duration",
            "(source = 'pristine' AND detected_playing_seconds IS NOT NULL) OR "
            "(source != 'pristine' AND detected_playing_seconds IS NULL)",
        )

    with op.batch_alter_table("contest_results") as batch:
        batch.drop_constraint("ck_contest_result_division", type_="check")
        batch.create_check_constraint(
            "ck_contest_result_division",
            "division IN ('open', 'verified', 'pristine')",
        )


def downgrade() -> None:
    # Older application versions cannot represent Pristine finalized results.
    op.execute("DELETE FROM contest_results WHERE division = 'pristine'")
    with op.batch_alter_table("contest_results") as batch:
        batch.drop_constraint("ck_contest_result_division", type_="check")
        batch.create_check_constraint(
            "ck_contest_result_division",
            "division IN ('open', 'verified')",
        )

    with op.batch_alter_table("practice_charts") as batch:
        batch.drop_constraint(
            "ck_practice_chart_pristine_source_duration", type_="check"
        )
        batch.drop_constraint(
            "ck_practice_chart_detected_seconds_positive", type_="check"
        )
        batch.drop_column("detected_playing_seconds")
