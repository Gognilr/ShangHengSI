"""V2Q-49：任务节点NER结果加密持久化。"""
import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ner_node_results',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('loc', sa.String(64), nullable=False),
        sa.Column('data_enc', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'loc'),
    )


def downgrade():
    op.drop_table('ner_node_results')
