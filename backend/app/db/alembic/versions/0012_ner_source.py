"""V2-B-06：本地实体识别来源与疑似原因。"""
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None
_SOURCES = ['term', 'party', 'alias', 'regex', 'amount', 'person', 'address', 'user', 'derived']
_REASONS = ['org_suffix', 'capitalized', 'long_digits', 'before_company', 'person_name', 'derived_name', 'person_like']


def _replace(table, column, values):
    name = op.f(f'ck_{table}_{column}_valid')
    op.drop_constraint(name, table, type_='check')
    op.create_check_constraint(name, table, column + ' IN (' + ', '.join(f"'{v}'" for v in values) + ')')


def upgrade():
    _replace('redaction_items', 'detected_by', [*_SOURCES, 'ner'])
    _replace('redaction_suspects', 'reason', [*_REASONS, 'ner'])


def downgrade():
    op.execute("DELETE FROM redaction_items WHERE detected_by = 'ner'")
    op.execute("DELETE FROM redaction_suspects WHERE reason = 'ner'")
    _replace('redaction_items', 'detected_by', _SOURCES)
    _replace('redaction_suspects', 'reason', _REASONS)
