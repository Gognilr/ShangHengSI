"""V31-U5F-02：脱敏自检闭环补上的替换项（detected_by='recheck'）。"""
from alembic import op

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None

_BY = ['term', 'party', 'alias', 'regex', 'amount', 'person', 'address', 'user', 'derived', 'ner', 'uncertain', 'date']


def _replace(values):
    op.drop_constraint(op.f('ck_redaction_items_detected_by_valid'), 'redaction_items', type_='check')
    op.create_check_constraint(op.f('ck_redaction_items_detected_by_valid'), 'redaction_items',
                               'detected_by IN (' + ', '.join(repr(v) for v in values) + ')')


def upgrade():
    _replace(_BY + ['recheck'])


def downgrade():
    op.execute("DELETE FROM redaction_items WHERE detected_by = 'recheck'")
    _replace(_BY)
