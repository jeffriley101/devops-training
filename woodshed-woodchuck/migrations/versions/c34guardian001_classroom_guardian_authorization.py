"""Retain explicit Class guardian choices; no prior consent or membership rewrite."""
from alembic import op
import sqlalchemy as sa

from app.operator_classroom_guardian_contract import C34

revision = 'c34guardian001'
down_revision = 'c33identity001'
branch_labels = None
depends_on = None


def upgrade():
    for name, table in C34.items():
        columns = []
        for column, (kind, length, nullable) in table['columns'].items():
            data_type = sa.String(length) if kind == 'String' else sa.DateTime(timezone=True) if kind == 'DateTime' else sa.Integer()
            columns.append(sa.Column(column, data_type, nullable=nullable))
        op.create_table(name, *columns,
            sa.PrimaryKeyConstraint(*table['primary_key']),
            *(sa.CheckConstraint(value, name=key) for key, value in table['checks'].items()),
            *(sa.UniqueConstraint(*value, name=key) for key, value in table['unique'].items()),
            *(sa.ForeignKeyConstraint(columns, [parent + '.' + column for column in referred], ondelete=action)
                for columns, parent, referred, action in table['foreign_keys']))
        for key, (columns, unique, predicate) in table['indexes'].items():
            op.create_index(key, name, columns, unique=unique,
                sqlite_where=sa.text(predicate), postgresql_where=sa.text(predicate))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if bind.get_isolation_level() != 'READ COMMITTED':
            raise RuntimeError('classroom_guardian_downgrade_requires_read_committed')
        bind.execute(sa.text('LOCK TABLE ' + ', '.join(C34) + ' IN ACCESS EXCLUSIVE MODE'))
    elif bind.dialect.name == 'sqlite':
        bind.execute(sa.text('UPDATE classroom_guardian_authorizations SET id=id WHERE 0'))
    else:
        raise RuntimeError('classroom_guardian_unsupported_database')
    if any(bind.scalar(sa.text('SELECT count(*) FROM ' + name)) for name in C34):
        raise RuntimeError('classroom_guardian_evidence_in_use: downgrade refused')
    for name in reversed(C34):
        op.drop_table(name)
