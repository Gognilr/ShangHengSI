"""V2-A-05：本地校验新增 L-09～L-11（V2Q-23 顺延编号）。"""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None
_OLD = ['L-01', 'L-02', 'L-03', 'L-04', 'L-05', 'L-06', 'L-07', 'L-08', 'L-INFO']
_NEW = ['L-09', 'L-10', 'L-11']


def _constraint(codes):
    name = op.f('ck_local_findings_check_code_valid')
    op.drop_constraint(name, 'local_findings', type_='check')
    op.create_check_constraint(name, 'local_findings', 'check_code IN (' + ', '.join(repr(c) for c in codes) + ')')


def upgrade():
    _constraint(_OLD + _NEW)


def downgrade():
    op.execute("DELETE FROM local_findings WHERE check_code IN ('L-09', 'L-10', 'L-11')")
    _constraint(_OLD)
