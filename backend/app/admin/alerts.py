"""管理员提醒（SPEC 6.7，设计 M7 第十节，Q-112）：/auth/me 对管理员返回，普通用户始终为空。

三种提醒：最近一次备份失败；备份目录剩余空间不足（小于最近一次成功备份大小的 2 倍）；本月调用次数达到上限的 90%。
提醒文字只含次数、容量和固定说明，不含路径和其他系统信息。
"""
import shutil
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.config import get_settings
from app.db.models import BackupRecord
from app.llm.usage import monthly_sent_calls

_GB = Decimal(1024**3)
_NEAR = Decimal("0.9")


def _gb(n: int) -> str:
    return f"{(Decimal(n) / _GB):.1f}"


def _backup_alerts(db: Session) -> list[dict]:
    alerts: list[dict] = []
    finished = db.scalar(
        select(BackupRecord).where(BackupRecord.status.in_(("success", "failed"))).order_by(BackupRecord.id.desc()).limit(1)
    )
    if finished is not None and finished.status == "failed":
        alerts.append({"code": "BACKUP_FAILED", "message": "最近一次备份失败，请到“设置 - 数据”查看原因"})
    last_ok = db.scalar(
        select(BackupRecord).where(BackupRecord.status == "success").order_by(BackupRecord.id.desc()).limit(1)
    )
    if last_ok is not None and last_ok.size_bytes is not None:
        try:
            free = shutil.disk_usage(get_settings().backup_dir).free
        except OSError:
            alerts.append({"code": "BACKUP_SPACE_LOW", "message": "备份目录不可用"})
        else:
            need = last_ok.size_bytes * 2
            if need > free:
                alerts.append({
                    "code": "BACKUP_SPACE_LOW",
                    "message": f"备份目录剩余空间不足：剩余 {_gb(free)} GB，建议不少于 {_gb(need)} GB",
                })
    return alerts


def _cap_alerts(db: Session) -> list[dict]:
    cap = int(get_setting(db, "model.monthly_call_cap") or 0)
    if cap <= 0:
        return []
    used = monthly_sent_calls(db)
    if Decimal(used) >= Decimal(cap):
        return [{"code": "CALL_CAP_NEAR", "message": f"本月模型调用已达上限（{used}/{cap} 次），审核和追问暂停"}]
    if Decimal(used) >= Decimal(cap) * _NEAR:
        return [{"code": "CALL_CAP_NEAR", "message": f"本月模型调用已达上限的 90%（{used}/{cap} 次）"}]
    return []


def admin_alerts(db: Session) -> list[dict]:
    return [*_backup_alerts(db), *_cap_alerts(db)]
