"""V31-U5F-07：角色与权限点、登录设备。

users.role 改为 roles + user_roles（一人可兼多个角色）；原角色按 permissions.LEGACY_ROLES 迁入，权限不变：
普通用户 → 普通用户；业务管理员 → 普通用户 + 业务管理员；系统管理员 → 普通用户 + 系统管理员；兼任 → 三个都有。
sessions 增加对外 id、浏览器标识与新 IP 提醒；users 增加最近登录 IP。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, JSONB

revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None

# 与 app/security/permissions.py 的 BUILTIN、LEGACY_ROLES 一致（迁移不引用应用代码，免得日后改动影响旧迁移）
_USE = ['use.review', 'use.assistant', 'use.meeting', 'use.translate', 'use.export']
_CONTENT = ['content.requirements', 'content.templates', 'content.kb', 'content.terms', 'content.laws',
            'redaction.names', 'redaction.words', 'redaction.parties']
_SYSTEM = ['sys.accounts', 'sys.models', 'sys.data', 'sys.lawsync']
_BUILTIN = [('user', '普通用户', _USE), ('biz_admin', '业务管理员', _CONTENT), ('sys_admin', '系统管理员', _SYSTEM)]
_LEGACY = {'user': ['user'], 'biz_admin': ['user', 'biz_admin'], 'sys_admin': ['user', 'sys_admin'],
           'admin': ['user', 'biz_admin', 'sys_admin']}


def upgrade():
    op.create_table(
        'roles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(30), nullable=False),
        sa.Column('builtin', sa.String(10)),
        sa.Column('permissions', JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("builtin IN ('user', 'biz_admin', 'sys_admin')", name=op.f('ck_roles_builtin_valid')),
        sa.UniqueConstraint('name', name=op.f('uq_roles_name')),
        sa.UniqueConstraint('builtin', name=op.f('uq_roles_builtin')),
    )
    op.create_table(
        'user_roles',
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE',
                  name=op.f('fk_user_roles_user_id_users')), primary_key=True),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', name=op.f('fk_user_roles_role_id_roles')),
                  primary_key=True),
    )
    op.create_index(op.f('ix_user_roles_role_id'), 'user_roles', ['role_id'])
    roles = sa.table('roles', sa.column('name'), sa.column('builtin'), sa.column('permissions', JSONB),
                     sa.column('created_at'), sa.column('updated_at'))
    for key, name, perms in _BUILTIN:
        op.execute(roles.insert().values(name=name, builtin=key, permissions=perms,
                                         created_at=sa.func.now(), updated_at=sa.func.now()))
    for legacy, keys in _LEGACY.items():
        op.execute(sa.text(
            "INSERT INTO user_roles (user_id, role_id) SELECT u.id, r.id FROM users u JOIN roles r "
            "ON r.builtin = ANY(CAST(:keys AS varchar[])) WHERE u.role = :legacy"
        ).bindparams(keys='{' + ','.join(keys) + '}', legacy=legacy))
    op.drop_constraint(op.f('ck_users_role_valid'), 'users', type_='check')
    op.drop_column('users', 'role')
    op.add_column('users', sa.Column('last_login_ip', INET()))

    op.add_column('sessions', sa.Column('id', sa.Uuid(), nullable=False, server_default=sa.text('gen_random_uuid()')))
    op.create_unique_constraint(op.f('uq_sessions_id'), 'sessions', ['id'])
    op.add_column('sessions', sa.Column('user_agent', sa.String(300)))
    op.add_column('sessions', sa.Column('notice_ip', INET()))
    op.add_column('sessions', sa.Column('notice_at', sa.DateTime(timezone=True)))


def downgrade():
    for col in ('notice_at', 'notice_ip', 'user_agent'):
        op.drop_column('sessions', col)
    op.drop_constraint(op.f('uq_sessions_id'), 'sessions', type_='unique')
    op.drop_column('sessions', 'id')
    op.drop_column('users', 'last_login_ip')
    op.add_column('users', sa.Column('role', sa.String(10), nullable=False, server_default='user'))
    # 按是否持有内置管理员角色还原为单一角色；自定义角色的权限无法表达，降级时丢弃
    op.execute("""
        UPDATE users u SET role = CASE
          WHEN b.biz AND b.sys THEN 'admin' WHEN b.sys THEN 'sys_admin' WHEN b.biz THEN 'biz_admin' ELSE 'user' END
        FROM (SELECT ur.user_id, bool_or(r.builtin = 'biz_admin') AS biz, bool_or(r.builtin = 'sys_admin') AS sys
              FROM user_roles ur JOIN roles r ON r.id = ur.role_id GROUP BY ur.user_id) b
        WHERE b.user_id = u.id
    """)
    op.alter_column('users', 'role', server_default=None)
    op.create_check_constraint(op.f('ck_users_role_valid'), 'users', "role IN ('user','sys_admin','biz_admin','admin')")
    op.drop_table('user_roles')
    op.drop_table('roles')

