"""V31-S-02：不确定项默认替换类别。"""
from alembic import op

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None
_OLD = ['OUR', 'ORG', 'PERSON', 'PHONE', 'EMAIL', 'URL', 'ADDR', 'POSTCODE', 'ACCOUNT',
        'SWIFT', 'IDNO', 'REFNO', 'AMOUNT', 'PRICE', 'QTY', 'CUSTOM', 'IM', 'PROJECT']
_BY = ['term', 'party', 'alias', 'regex', 'amount', 'person', 'address', 'user', 'derived', 'ner']


def replace(table, column, values):
    op.drop_constraint(op.f(f'ck_{table}_{column}_valid'), table, type_='check')
    choices = ','.join(repr(v) for v in values)
    op.create_check_constraint(f'{column}_valid', table, f'{column} IN ({choices})')


def upgrade():
    for table in ('redaction_items', 'redaction_suspects'):
        replace(table, 'category', _OLD + ['NAME', 'TERM'])
    replace('redaction_items', 'detected_by', _BY + ['uncertain'])


def downgrade():
    op.execute("UPDATE redaction_items SET category='CUSTOM', detected_by='user' WHERE detected_by='uncertain' OR category IN ('NAME','TERM')")
    op.execute("UPDATE redaction_suspects SET category='CUSTOM' WHERE category IN ('NAME','TERM')")
    for table in ('redaction_items', 'redaction_suspects'):
        replace(table, 'category', _OLD)
    replace('redaction_items', 'detected_by', _BY)
