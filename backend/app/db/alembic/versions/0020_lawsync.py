"""V2-E-03 同步队列类型（用户确认补齐迁移）。"""
from alembic import op

revision='0020'
down_revision='0019'
branch_labels=None
depends_on=None


def upgrade():
    op.drop_constraint(op.f('ck_jobs_type_valid'),'jobs',type_='check')
    op.create_check_constraint('type_valid','jobs',"type IN ('parse','review','backup','retention','law_import','law_embed','law_sync')")


def downgrade():
    op.execute("DELETE FROM jobs WHERE type='law_sync'")
    op.drop_constraint(op.f('ck_jobs_type_valid'),'jobs',type_='check')
    op.create_check_constraint('type_valid','jobs',"type IN ('parse','review','backup','retention','law_import','law_embed')")
