"""只允许任务所有者读取本地校验结果。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.checks.service import list_findings
from app.db.models import Task
from app.db.session import get_db
from app.security.deps import owned_task

router = APIRouter()


@router.get("/tasks/{id}/local-findings")
def get_local_findings(task: Task = Depends(owned_task), db: Session = Depends(get_db)) -> dict:
    return {"items": list_findings(db, task)}
