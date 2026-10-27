"""V2-D-03：助手独立队列与外发用途（V2Q-131）。"""
from alembic import op

revision = "0023a"
down_revision = "0023"
branch_labels = None
depends_on = None
_JOBS = "'parse','review','backup','retention','law_import','law_embed','law_sync','template_embed'"
_PURPOSES = "'review','chat','test'"


def _checks(assist):
    for table, column, values in (("jobs", "type", _JOBS), ("llm_calls", "purpose", _PURPOSES)):
        op.drop_constraint(op.f(f"ck_{table}_{column}_valid"), table, type_="check")
        op.create_check_constraint(f"{column}_valid", table, f"{column} IN ({values}{assist})")


def upgrade():
    _checks(",'assist'")


def downgrade():
    # 回退后不可继续生成，但保留会话消息和对照表供查看。
    op.execute("UPDATE assistant_messages SET status='failed', error_code='ASSIST_INTERRUPTED', updated_at=now() "
               "WHERE status IN ('queued','streaming')")
    op.execute("DELETE FROM jobs WHERE type='assist'")
    op.execute("DELETE FROM llm_calls WHERE purpose='assist'")
    _checks("")
