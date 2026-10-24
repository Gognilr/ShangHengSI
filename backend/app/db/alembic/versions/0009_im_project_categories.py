"""V2-B-01：IM、PROJECT 类别；V2Q-31 用户确认由 0008 顺延到 0009。"""
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

_OLD = ['OUR', 'ORG', 'PERSON', 'PHONE', 'EMAIL', 'URL', 'ADDR', 'POSTCODE', 'ACCOUNT',
        'SWIFT', 'IDNO', 'REFNO', 'AMOUNT', 'PRICE', 'QTY', 'CUSTOM']
_NEW = [*_OLD, 'IM', 'PROJECT']
_TABLES = ['redaction_items', 'redaction_suspects']


def _replace_check(values):
    sql = "category IN (" + ", ".join(f"'{v}'" for v in values) + ")"
    for table in _TABLES:
        name = f'ck_{table}_category_valid'
        op.drop_constraint(op.f(name), table, type_='check')
        op.create_check_constraint(op.f(name), table, sql)


def upgrade():
    _replace_check(_NEW)


def downgrade():
    for table in _TABLES:
        op.execute(f"DELETE FROM {table} WHERE category IN ('IM', 'PROJECT')")
    _replace_check(_OLD)
