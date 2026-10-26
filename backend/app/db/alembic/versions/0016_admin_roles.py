"""V2-C-06：系统与业务管理员拆分，兼任 admin 保持原值。"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_users_role_valid"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role_valid"), "users", "role IN ('user','sys_admin','biz_admin','admin')")


def downgrade():
    op.execute("UPDATE users SET role='admin' WHERE role IN ('sys_admin','biz_admin')")
    op.drop_constraint(op.f("ck_users_role_valid"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role_valid"), "users", "role IN ('user','admin')")
