"""Immutable PTA receipt issuance, with no inferred historical backfill."""
from alembic import op
import sqlalchemy as sa
from app import pta_receipt_schema as issuance

revision = "p31pta001"
down_revision = "c30class001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    metadata = sa.MetaData()
    sa.Table("persistent_team_control", metadata, sa.Column("id", sa.Integer, primary_key=True))
    issuance.table(metadata).create(bind)
    issuance.install(bind)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # A snapshot opened before waiting for ACTIVATE must not hide the
        # issuance that just committed. Check before acquiring the fence.
        if (bind.connection.driver_connection.autocommit
                or bind.get_isolation_level() != "READ COMMITTED"):
            raise RuntimeError("pta_receipt_downgrade_requires_read_committed")
        bind.execute(sa.text("SELECT id FROM persistent_team_control WHERE id=1 FOR UPDATE"))
        bind.execute(sa.text("LOCK TABLE persistent_team_receipt_issuances IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE persistent_team_control SET id=id WHERE 0"))
    else:
        raise RuntimeError("pta_receipt_unsupported_database")
    if bind.scalar(sa.text("SELECT count(*) FROM persistent_team_receipt_issuances")):
        raise RuntimeError("pta_receipt_evidence_in_use: downgrade refused")
    issuance.uninstall(bind)
    op.drop_table(issuance.NAME)
