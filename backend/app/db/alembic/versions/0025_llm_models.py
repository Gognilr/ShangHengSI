"""V3-B-01：多模型配置与历史记录归属。"""
import base64
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("llm_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(40), nullable=False),
        sa.Column("description", sa.String(200)),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("api_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("temperature", sa.Numeric(3,2), nullable=False, server_default="0.1"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="32000"),
        sa.Column("timeout_s", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("json_mode", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("extra_params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("chunk_chars", sa.Integer(), nullable=False, server_default="60000"),
        sa.Column("price_in_per_m", sa.Numeric(12,4), nullable=False, server_default="0"),
        sa.Column("price_out_per_m", sa.Numeric(12,4), nullable=False, server_default="0"),
        sa.Column("monthly_call_cap", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("for_review", sa.Boolean(), nullable=False),
        sa.Column("for_assist", sa.Boolean(), nullable=False),
        sa.Column("structured_ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("structured_tested_at", sa.DateTime(timezone=True)),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("display_name"))
    for table in ("llm_calls", "review_runs", "assistant_messages"):
        op.add_column(table, sa.Column("model_id", sa.Integer()))
        if table != "llm_calls":
            op.create_foreign_key(op.f(f"fk_{table}_model_id_llm_models"), table, "llm_models", ["model_id"], ["id"])
            op.add_column(table, sa.Column("model_label", sa.String(40)))
    bind = op.get_bind()
    settings = sa.table("settings", sa.column("key",sa.String), sa.column("value",JSONB),
                        sa.column("updated_at",sa.DateTime(timezone=True)))
    values = dict(bind.execute(sa.select(settings.c.key, settings.c.value).where(settings.c.key.like("model.%"))).all())
    model_id = None
    if all(values.get(k) for k in ("model.base_url", "model.name", "model.api_key_enc")):
        # 原密文直接解码迁入；迁移不依赖主密钥。
        model = sa.table("llm_models", *[sa.column(k,t) for k,t in (
            ("id",sa.Integer), ("display_name",sa.String), ("model_name",sa.String), ("base_url",sa.String),
            ("api_key_enc",sa.LargeBinary), ("temperature",sa.Numeric), ("max_tokens",sa.Integer),
            ("timeout_s",sa.Integer), ("json_mode",sa.Boolean), ("extra_params",JSONB),
            ("chunk_chars",sa.Integer), ("price_in_per_m",sa.Numeric), ("price_out_per_m",sa.Numeric),
            ("monthly_call_cap",sa.Integer), ("enabled",sa.Boolean), ("for_review",sa.Boolean),
            ("for_assist",sa.Boolean), ("structured_ok",sa.Boolean),
            ("created_at",sa.DateTime(timezone=True)), ("updated_at",sa.DateTime(timezone=True)))])
        data = {key:values.get("model."+key,default) for key,default in (
            ("temperature",0.1),("max_tokens",32000),("timeout_s",180),("json_mode",True),
            ("extra_params",{}),("chunk_chars",60000),("price_in_per_m",0),("price_out_per_m",0),("monthly_call_cap",0))}
        data.update(display_name=values["model.name"][:40], model_name=values["model.name"],
                    base_url=values["model.base_url"], api_key_enc=base64.b64decode(values["model.api_key_enc"],validate=True),
                    enabled=True,for_review=True,for_assist=True,structured_ok=True,
                    created_at=sa.func.now(),updated_at=sa.func.now())
        model_id = bind.execute(model.insert().values(**data).returning(model.c.id)).scalar_one()
        for table in ("llm_calls", "review_runs"):
            bind.execute(sa.text(f"UPDATE {table} SET model_id=:id"), {"id":model_id})
        bind.execute(sa.text("UPDATE review_runs SET model_label=:label"), {"label":data["display_name"]})
    bind.execute(settings.delete().where(settings.c.key.like("model.%"),settings.c.key != "model.user_daily_cap"))
    for purpose in ("review", "assist"):
        bind.execute(settings.insert().values(key=f"model.default_{purpose}_id", value=model_id, updated_at=sa.func.now()))


def downgrade():
    # 多模型无法无损还原成单模型设置；不伪造旧连接配置。
    op.execute("DELETE FROM settings WHERE key IN ('model.default_review_id','model.default_assist_id')")
    for table in ("assistant_messages", "review_runs", "llm_calls"):
        if table != "llm_calls":
            op.drop_column(table, "model_label")
            op.drop_constraint(op.f(f"fk_{table}_model_id_llm_models"),table,type_="foreignkey")
        op.drop_column(table,"model_id")
    op.drop_table("llm_models")
