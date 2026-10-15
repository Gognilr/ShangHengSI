"""审计日志（SPEC 17.7、18.24）。

记录登录与登录失败、设置修改、账号变更、任务创建与删除、报告导出、备份执行。
detail 只放类别、原因代码、标识等非敏感信息：不得包含密码、令牌、原文、真实名称、金额、API 密钥，
登录失败时也不记录尝试的用户名（用户可能把密码误填进用户名框）。
"""
import uuid
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.db.base import utcnow
from app.db.models import AuditLog


def record(
    db: DbSession,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """写一条审计日志。只 add，不提交；由调用方与业务变更在同一事务中提交。"""
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
            ip=ip,
            created_at=utcnow(),
        )
    )
