"""V31-E：外发回执与本地校验字段。"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def _checks(last):
    name = op.f('ck_local_findings_check_code_valid')
    op.drop_constraint(name, 'local_findings', type_='check')
    codes = [f'L-{i:02}' for i in range(1, last + 1)] + ['L-INFO']
    op.create_check_constraint(name, 'local_findings', 'check_code IN (' + ','.join(repr(c) for c in codes) + ')')


def upgrade():
    op.add_column('review_runs', sa.Column('receipt', JSONB(), nullable=True))
    op.add_column('requirement_results', sa.Column('unable_reason', sa.String(10), nullable=True))
    op.create_check_constraint(op.f('ck_requirement_results_unable_reason_valid'), 'requirement_results',
                               "unable_reason IN ('redacted','absent','other')")
    op.add_column('doc_nodes', sa.Column('hidden_spans', JSONB(), nullable=True))
    op.add_column('doc_nodes', sa.Column('formula_enc', sa.LargeBinary(), nullable=True))
    _checks(22)


def downgrade():
    op.execute("DELETE FROM local_findings WHERE check_code IN (" +
               ','.join(repr(f'L-{i:02}') for i in range(13, 23)) + ')')
    _checks(12)
    op.drop_column('doc_nodes', 'formula_enc')
    op.drop_column('doc_nodes', 'hidden_spans')
    op.drop_constraint(op.f('ck_requirement_results_unable_reason_valid'), 'requirement_results', type_='check')
    op.drop_column('requirement_results', 'unable_reason')
    op.drop_column('review_runs', 'receipt')
