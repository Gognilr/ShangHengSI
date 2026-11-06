"""权限点与角色（V31-U5F 第七节，V31Q-106）。

- 权限点在代码里固定，不能新增；角色可增删改，一个人可兼多个角色，权限取并集。
- 内置三个角色（普通用户、业务管理员、系统管理员）可改名、调整权限，不能删除；
  “系统管理员”必须保留“账号与角色”权限，且至少有一个启用中的账号持有它。
- 任何权限都不能查看他人的任务和会话内容：任务类接口仍按 owner_id 校验，非本人 404（deps.load_owned_task）。
"""
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from app.db.base import utcnow
from app.db.models import Role, User, UserRole
from app.db.session import get_db
from app.security.errors import AppError
from app.security.sessions import AuthContext, require_auth

# (代码, 名称, 组)；顺序即界面顺序
PERMISSIONS: list[tuple[str, str, str]] = [
    ("use.review", "智能审核", "使用"),
    ("use.assistant", "智能助手", "使用"),
    ("use.meeting", "会议纪要", "使用"),
    ("use.translate", "翻译（云端）", "使用"),
    ("use.export", "导出报告", "使用"),
    ("content.requirements", "审核要求", "内容维护"),
    ("content.templates", "公司范本", "内容维护"),
    ("content.kb", "知识库（公共库）", "内容维护"),
    ("content.terms", "术语表", "内容维护"),
    ("content.laws", "法规库（含确认同步变更）", "内容维护"),
    ("redaction.names", "已记住的名称", "脱敏维护"),
    ("redaction.words", "自定义敏感词与识别设置", "脱敏维护"),
    ("redaction.parties", "我方主体", "脱敏维护"),
    ("sys.accounts", "账号与角色", "系统"),
    ("sys.models", "模型", "系统"),
    ("sys.data", "数据（备份、保留期、系统状态）", "系统"),
    ("sys.lawsync", "法规同步开关", "系统"),
]
CODES = [code for code, _, _ in PERMISSIONS]
USE = [c for c in CODES if c.startswith("use.")]
CONTENT = [c for c in CODES if c.startswith("content.") or c.startswith("redaction.")]
SYSTEM = [c for c in CODES if c.startswith("sys.")]

ROLE_USER, ROLE_BIZ, ROLE_SYS = "user", "biz_admin", "sys_admin"
# 内置角色的默认值（第七节分工表）：业务管理员维护内容与脱敏，系统管理员管账号、模型、数据与法规同步开关
BUILTIN: dict[str, tuple[str, list[str]]] = {
    ROLE_USER: ("普通用户", USE),
    ROLE_BIZ: ("业务管理员", CONTENT),
    ROLE_SYS: ("系统管理员", SYSTEM),
}
# V31-U5F-07 之前的单一角色 → 现在兼任的内置角色（迁移 0038 与测试工具共用）
LEGACY_ROLES: dict[str, list[str]] = {
    "user": [ROLE_USER],
    "biz_admin": [ROLE_USER, ROLE_BIZ],
    "sys_admin": [ROLE_USER, ROLE_SYS],
    "admin": [ROLE_USER, ROLE_BIZ, ROLE_SYS],
}


def ensure_builtin_roles(db: DbSession) -> dict[str, Role]:
    """内置角色缺失时按默认值补建（迁移已建；清空的测试库与新装库在这里补），返回 builtin → Role。"""
    now = utcnow()
    for key, (name, perms) in BUILTIN.items():
        db.execute(insert(Role).values(builtin=key, name=name, permissions=perms, created_at=now, updated_at=now)
                   .on_conflict_do_nothing())
    return {r.builtin: r for r in db.scalars(select(Role).where(Role.builtin.is_not(None)))}


def assign_legacy_role(db: DbSession, user: User, legacy: str) -> None:
    roles = ensure_builtin_roles(db)
    for key in LEGACY_ROLES[legacy]:
        db.add(UserRole(user_id=user.id, role_id=roles[key].id))


def user_permissions(db: DbSession, user_id) -> set[str]:
    rows = db.scalars(select(Role.permissions).join(UserRole, UserRole.role_id == Role.id)
                      .where(UserRole.user_id == user_id)).all()
    return {p for perms in rows for p in perms if p in CODES}


def user_roles(db: DbSession, user_id) -> list[Role]:
    return list(db.scalars(select(Role).join(UserRole, UserRole.role_id == Role.id)
                           .where(UserRole.user_id == user_id).order_by(Role.id)))


def permissions_of(request: Request, db: DbSession, ctx: AuthContext) -> set[str]:
    """当前请求用户的权限，同一请求只查一次。"""
    cached = getattr(request.state, "permissions", None)
    if cached is None:
        cached = request.state.permissions = user_permissions(db, ctx.user.id)
    return cached


def require(*codes: str):
    """依赖：持有 codes 中任一权限点，否则 403。返回当前用户。"""
    def _dep(request: Request, ctx: AuthContext = Depends(require_auth), db: DbSession = Depends(get_db)) -> User:
        if not permissions_of(request, db, ctx) & set(codes):
            raise AppError(403, "FORBIDDEN", "没有此项权限，请联系系统管理员")
        return ctx.user
    return _dep


# 助手会话用途 → 发往云端所需的权限点
PURPOSE_PERM = {"chat": "use.assistant", "meeting": "use.meeting", "translate": "use.translate"}


def ensure(db: DbSession, user: User, code: str) -> None:
    """服务层（无 request）判断权限。"""
    if code not in user_permissions(db, user.id):
        raise AppError(403, "FORBIDDEN", "没有此项权限，请联系系统管理员")


def ensure_cloud_send(db: DbSession, user: User, purpose: str, template: str | None) -> None:
    """助手类会话发往云端：按用途校验；任何会话里用云端翻译都要“翻译（云端）”。本地翻译不受限。"""
    ensure(db, user, "use.translate" if template == "translate" else PURPOSE_PERM[purpose])


def check(request: Request, db: DbSession, ctx_or_user, code: str) -> None:
    """服务层按对象判断权限（如按会话用途、敏感词种类）时用。"""
    perms = getattr(request.state, "permissions", None)
    if perms is None:
        user = ctx_or_user.user if isinstance(ctx_or_user, AuthContext) else ctx_or_user
        perms = request.state.permissions = user_permissions(db, user.id)
    if code not in perms:
        raise AppError(403, "FORBIDDEN", "没有此项权限，请联系系统管理员")
