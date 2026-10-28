"""Bind an individual chart share to enrollment OR accepted invitation evidence."""
from alembic import op
import sqlalchemy as sa

revision = "c27class001"
down_revision = "c26class001"
branch_labels = None
depends_on = None

TABLE = "classroom_chart_director_shares"


def upgrade():
    # Existing shares retain their exact enrollment source. No charts are shared
    # or invitation permissions inferred by this migration.
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("invitation_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_classroom_chart_share_invitation",
            "classroom_student_invitations", ["invitation_id"], ["id"], ondelete="RESTRICT")
        batch.alter_column("membership_period_id", existing_type=sa.Integer(), nullable=True)
        batch.create_check_constraint("ck_classroom_chart_director_share_source",
            "(membership_period_id IS NOT NULL AND invitation_id IS NULL) OR "
            "(membership_period_id IS NULL AND invitation_id IS NOT NULL)")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text(f"UPDATE {TABLE} SET id = id WHERE 0"))
    else:
        raise RuntimeError("classroom_invited_sharing_unsupported_database")
    # Retain withdrawn evidence too. Never erase invitation-backed history.
    if bind.scalar(sa.text(f"SELECT count(*) FROM {TABLE} WHERE invitation_id IS NOT NULL")):
        raise RuntimeError("classroom_invited_sharing_in_use: downgrade refused")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint("ck_classroom_chart_director_share_source", type_="check")
        batch.drop_constraint("fk_classroom_chart_share_invitation", type_="foreignkey")
        batch.drop_column("invitation_id")
        batch.alter_column("membership_period_id", existing_type=sa.Integer(), nullable=False)
