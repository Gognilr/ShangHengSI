"""归属隔离的 Word 报告下载与最小审计。"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.db.models import ReviewRun, Task, TaskFile, User
from app.export.annotated_docx import build_annotated
from app.db.session import get_db
from app.export.report_docx import build_local_report, build_run_report
from app.security.deps import current_user, owned_run, owned_task
from app.security.errors import AppError

router = APIRouter()
CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/runs/{rid}/annotated")
def annotated(file_seq: int = Query(..., ge=1), run: ReviewRun = Depends(owned_run),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    file = db.scalar(select(TaskFile).where(TaskFile.task_id == run.task_id, TaskFile.seq == file_seq))
    if file is None:
        raise AppError(404, "NOT_FOUND", "文件不存在")
    filename, content, total, unanchored = build_annotated(db, run, file)
    audit.record(db, "export_annotated", user_id=user.id, target_type="run", target_id=str(run.id),
                 detail={"run_no": run.run_no, "file_seq": file.seq, "total": total, "unanchored": unanchored})
    db.commit()
    response = _response(filename, content)
    response.headers["X-SI-Total"] = str(total)
    response.headers["X-SI-Unanchored"] = str(unanchored)
    return response


def _response(filename, content):
    return Response(content, media_type=CONTENT_TYPE,
                    headers={"Content-Disposition": f"attachment; filename=\"report.docx\"; filename*=UTF-8''{quote(filename, safe='')}"})


@router.get("/runs/{rid}/report")
def run_report(run: ReviewRun = Depends(owned_run), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if run.status in ("queued", "running", "blocked"):
        raise AppError(409, "REPORT_UNAVAILABLE", "该轮次尚未完成，无法导出")
    filename, content = build_run_report(db, run, user)
    audit.record(db, "report_export", user_id=user.id, target_type="run", target_id=str(run.id), detail={"run_no": run.run_no})
    db.commit()
    return _response(filename, content)


@router.get("/tasks/{id}/report")
def local_report(task: Task = Depends(owned_task), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if task.status != "completed_local":
        raise AppError(409, "REPORT_UNAVAILABLE", "该任务尚未完成本地校验，无法导出")
    filename, content = build_local_report(db, task, user)
    # Q-96：本地任务没有轮次，不虚构 run_no；审计 detail 留空。
    audit.record(db, "report_export", user_id=user.id, target_type="task", target_id=str(task.id))
    db.commit()
    return _response(filename, content)
