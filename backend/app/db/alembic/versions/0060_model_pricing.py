"""V351Q-21：llm_calls 记命中缓存的 token 与当场费用，按内置价格表补算已有记录；删除手填单价。"""
from alembic import op
import sqlalchemy as sa

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_calls", sa.Column("tokens_cached", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("llm_calls", sa.Column("cost", sa.Numeric(12, 6), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT c.id, m.base_url, c.model_name, c.tokens_in, c.tokens_out, c.created_at "
        "FROM llm_calls c LEFT JOIN llm_models m ON m.id = c.model_id WHERE c.status = 'sent'")).fetchall()
    if rows:
        # 只在有旧记录时导入计价代码：新装库的迁移不依赖今后的计价实现。
        # 旧记录没有缓存信息，一律按未命中计；DeepSeek 仍按调用时刻区分高峰与空闲。
        from app.llm import pricing
        params = []
        for cid, base_url, name, tokens_in, tokens_out, created_at in rows:
            cost = pricing.call_cost(base_url, name, pricing.Usage(tokens_in, tokens_out), created_at)
            if cost is not None:
                params.append({"id": cid, "cost": cost})
        if params:
            bind.execute(sa.text("UPDATE llm_calls SET cost = :cost WHERE id = :id"), params)
    op.drop_column("llm_models", "price_in_per_m")
    op.drop_column("llm_models", "price_out_per_m")


def downgrade():
    op.add_column("llm_models", sa.Column("price_out_per_m", sa.Numeric(12, 4), server_default=sa.text("0"), nullable=False))
    op.add_column("llm_models", sa.Column("price_in_per_m", sa.Numeric(12, 4), server_default=sa.text("0"), nullable=False))
    op.drop_column("llm_calls", "cost")
    op.drop_column("llm_calls", "tokens_cached")
