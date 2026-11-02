"""One annual school receipt and exact optional parent chart permission; no backfill."""
from alembic import op
import sqlalchemy as sa

from app.operator_classroom_school_contract import C35

revision = 'c35school001'
down_revision = 'c34guardian001'
branch_labels = None
depends_on = None


def upgrade():
    for name, table in C35.items():
        columns = []
        for column, (kind, length, nullable) in table['columns'].items():
            data_type = sa.String(length) if kind == 'String' else sa.DateTime(timezone=True) if kind == 'DateTime' else sa.Integer()
            columns.append(sa.Column(column, data_type, nullable=nullable))
        op.create_table(name, *columns, sa.PrimaryKeyConstraint(*table['primary_key']),
            *(sa.CheckConstraint(value, name=key) for key, value in table['checks'].items()),
            *(sa.ForeignKeyConstraint(columns, [parent+'.'+column for column in referred], ondelete=action)
                for columns, parent, referred, action in table['foreign_keys']))
        for key, (columns, unique, predicate) in table['indexes'].items():
            op.create_index(key, name, columns, unique=unique,
                sqlite_where=sa.text(predicate), postgresql_where=sa.text(predicate))
    with op.batch_alter_table('classroom_chart_director_shares') as batch:
        batch.add_column(sa.Column('guardian_permission_id', sa.Integer(), sa.ForeignKey(
            'child_director_permissions.id', ondelete='RESTRICT', name='fk_classroom_chart_guardian_permission')))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if bind.get_isolation_level() != 'READ COMMITTED':
            raise RuntimeError('classroom_school_downgrade_requires_read_committed')
        bind.execute(sa.text('LOCK TABLE classroom_school_authorizations, classroom_chart_director_shares IN ACCESS EXCLUSIVE MODE'))
    elif bind.dialect.name == 'sqlite':
        bind.execute(sa.text('UPDATE classroom_school_authorizations SET id=id WHERE 0'))
    else:
        raise RuntimeError('classroom_school_unsupported_database')
    if (bind.scalar(sa.text('SELECT count(*) FROM classroom_school_authorizations'))
            or bind.scalar(sa.text('SELECT count(*) FROM classroom_chart_director_shares WHERE guardian_permission_id IS NOT NULL'))):
        raise RuntimeError('classroom_school_evidence_in_use: downgrade refused')
    with op.batch_alter_table('classroom_chart_director_shares') as batch:
        batch.drop_constraint('fk_classroom_chart_guardian_permission', type_='foreignkey')
        batch.drop_column('guardian_permission_id')
    op.drop_table('classroom_school_authorizations')
