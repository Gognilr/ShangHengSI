"""V35-D-02 多稿对比：稿次关系与“拒绝全部修订”原文。

* task_files.prev_file_id：同一会话里的上一稿，一稿最多一个下一稿（唯一），上一稿删除时置空。
* doc_nodes.text_before_enc：带修订的 DOCX 正文节点在“拒绝全部修订”下的原文（加密），随 text_enc 按保留期清理。
增量复审的列（review_runs.scope 等）由 D-03 另加。
"""
import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_files", sa.Column("prev_file_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_task_files_prev_file_id_task_files"), "task_files", "task_files",
                          ["prev_file_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint(op.f("uq_task_files_prev_file_id"), "task_files", ["prev_file_id"])
    op.add_column("doc_nodes", sa.Column("text_before_enc", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("doc_nodes", "text_before_enc")
    op.drop_constraint(op.f("uq_task_files_prev_file_id"), "task_files", type_="unique")
    op.drop_constraint(op.f("fk_task_files_prev_file_id_task_files"), "task_files", type_="foreignkey")
    op.drop_column("task_files", "prev_file_id")
