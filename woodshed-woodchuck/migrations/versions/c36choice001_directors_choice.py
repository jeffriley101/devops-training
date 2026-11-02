"""Saved Class assignments and retained versions. No historical student backfill."""
from alembic import op
import sqlalchemy as sa
from app.operator_classroom_choice_contract import C36
from app.classroom_choice_guards import install

revision = 'c36choice001'
down_revision = 'c35school001'
branch_labels = None
depends_on = None


def upgrade():
    for name, table in C36.items():
        columns = []
        for column, (kind, length, nullable) in table['columns'].items():
            kind_type = {'Integer': sa.Integer, 'Date': sa.Date, 'JSON': sa.JSON}
            data_type = sa.String(length) if kind == 'String' else sa.DateTime(timezone=True) if kind == 'DateTime' else kind_type[kind]()
            columns.append(sa.Column(column, data_type, nullable=nullable))
        op.create_table(name, *columns, sa.PrimaryKeyConstraint(*table['primary_key']),
            *(sa.CheckConstraint(value, name=key) for key, value in table['checks'].items()),
            *(sa.UniqueConstraint(*columns, name=key) for key, columns in table['unique'].items()),
            *(sa.ForeignKeyConstraint(columns, [parent+'.'+column for column in referred], ondelete=action)
              for columns, parent, referred, action in table['foreign_keys']))
        for key, (columns, unique, _) in table['indexes'].items():
            op.create_index(key, name, columns, unique=unique)
    install(op.get_bind())


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if bind.get_isolation_level() != 'READ COMMITTED':
            raise RuntimeError('classroom_choice_downgrade_requires_read_committed')
        bind.exec_driver_sql('LOCK TABLE classroom_choice_assignments, classroom_choice_versions IN ACCESS EXCLUSIVE MODE')
    elif bind.dialect.name == 'sqlite':
        bind.exec_driver_sql('UPDATE classroom_choice_assignments SET id=id WHERE 0')
    else:
        raise RuntimeError('classroom_choice_unsupported_database')
    if any(bind.scalar(sa.text('SELECT count(*) FROM ' + name)) for name in C36):
        raise RuntimeError('classroom_choice_history_in_use: downgrade refused')
    op.drop_table('classroom_choice_versions')
    op.drop_table('classroom_choice_assignments')
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('DROP FUNCTION enforce_classroom_choice_immutable()')
