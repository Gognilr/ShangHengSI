"""V31-P：校验、日期与精确引用片段。"""
import sqlalchemy as sa
from alembic import op

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None

CATEGORIES = ['OUR', 'ORG', 'PERSON', 'PHONE', 'EMAIL', 'URL', 'ADDR', 'POSTCODE', 'ACCOUNT',
              'SWIFT', 'IDNO', 'REFNO', 'AMOUNT', 'PRICE', 'QTY', 'CUSTOM', 'IM', 'PROJECT', 'NAME', 'TERM']
DETECTORS = ['term', 'party', 'alias', 'regex', 'amount', 'person', 'address', 'user', 'derived', 'ner', 'uncertain']
REASONS = ['org_suffix', 'capitalized', 'long_digits', 'before_company', 'person_name', 'derived_name', 'person_like', 'ner']


def _check(table, column, values):
    name = op.f(f'ck_{table}_{column}_valid')
    op.drop_constraint(name, table, type_='check')
    op.create_check_constraint(name, table, column + ' IN (' + ','.join(repr(v) for v in values) + ')')


def upgrade():
    for table in ('redaction_items', 'redaction_suspects'):
        _check(table, 'category', CATEGORIES + ['DATE'])
    _check('redaction_items', 'detected_by', DETECTORS + ['date'])
    _check('redaction_suspects', 'reason', REASONS + ['checksum_fail'])
    op.add_column('findings', sa.Column('quote_orig_enc', sa.LargeBinary(), nullable=True))


def downgrade():
    op.execute("DELETE FROM redaction_items WHERE category='DATE'")
    op.execute("DELETE FROM redaction_suspects WHERE category='DATE' OR reason='checksum_fail'")
    for table in ('redaction_items', 'redaction_suspects'):
        _check(table, 'category', CATEGORIES)
    _check('redaction_items', 'detected_by', DETECTORS)
    _check('redaction_suspects', 'reason', REASONS)
    op.drop_column('findings', 'quote_orig_enc')
