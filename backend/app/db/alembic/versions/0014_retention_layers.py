"""V2-C-02：活动时间与升级安全的分层保留设置（V2Q-62）。"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

NEW_KEYS = ("retention.result_days", "retention.outbound_days", "retention.audit_days",
            "retention.unfinished_days", "retention.review_pending")


def upgrade():
    op.add_column("tasks", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE tasks SET last_activity_at=coalesce(last_completed_at,updated_at,created_at)")
    op.alter_column("tasks", "last_activity_at", nullable=False)
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM users)")):
        # 不覆盖管理员已保存的任何值；旧版的非档位原件天数也原样保留。
        for key in ("retention.original_days", *NEW_KEYS[:-1]):
            bind.execute(sa.text("INSERT INTO settings (key,value,updated_at) VALUES (:key,'0'::jsonb,now()) "
                                 "ON CONFLICT (key) DO NOTHING"), {"key": key})
        bind.execute(sa.text("INSERT INTO settings (key,value,updated_at) "
                             "VALUES ('retention.review_pending','true'::jsonb,now()) "
                             "ON CONFLICT (key) DO UPDATE SET value='true'::jsonb,updated_at=now()"))


def downgrade():
    bind = op.get_bind()
    for key in NEW_KEYS:
        bind.execute(sa.text("DELETE FROM settings WHERE key=:key"), {"key": key})
    op.drop_column("tasks", "last_activity_at")
