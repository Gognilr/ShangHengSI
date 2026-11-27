"""发票管家接口（设计第六节，前缀 /invoice）。所有接口校验 owner_id，非本人一律 404。

上传在内存中读完（不经 SpooledTemporaryFile 落盘），检查后加密写入内存盘。上传与下载需 use.export。
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session as DbSession
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.db.models import User
from app.db.session import get_db
from app.invoice import intake, service
from app.security.deps import current_user
from app.security.errors import AppError
from app.security.permissions import require
from app.security.sessions import client_ip

router = APIRouter(prefix="/invoice")
_export = require("use.export")
_NO_STORE = {"Cache-Control": "no-store"}


class _MemoryMultipart(MultiPartParser):
    spool_max_size = 0  # 内容只在内存中，由请求字节上限约束


async def _read_files(request: Request) -> list[tuple[str | None, bytes]]:
    limit = intake.MAX_TOTAL_BYTES + 2 * 1024 * 1024

    async def bounded():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise AppError(422, "FILE_TOO_LARGE", "每批合计不超过 200 MB")
            yield chunk

    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise AppError(422, "NO_FILES", "请选择发票文件")
    try:
        form = await _MemoryMultipart(request.headers, bounded(), max_files=intake.MAX_FILES, max_fields=0).parse()
    except MultiPartException as exc:
        if "too many files" in str(exc).lower():
            raise AppError(422, "TOO_MANY_FILES", f"每批不超过 {intake.MAX_FILES} 个文件") from None
        raise AppError(422, "NO_FILES", "上传内容无法读取，请重新选择文件") from None
    try:
        out = []
        for f in form.getlist("files"):
            if isinstance(f, UploadFile):
                out.append((f.filename, await f.read()))
        return out
    finally:
        await form.close()


@router.post("/batches", status_code=status.HTTP_201_CREATED)
async def create_batch(request: Request, user: User = Depends(_export), db: DbSession = Depends(get_db)):
    raw = await _read_files(request)
    bid = await run_in_threadpool(service.start_batch, db, user, raw)
    return {"id": bid}


@router.get("/batches/{bid}")
def get_batch(bid: str, response: Response, user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    response.headers.update(_NO_STORE)
    return service.view(service.owned_batch(db, user, bid))


@router.get("/batches/{bid}/excel")
def download_excel(bid: str, request: Request, user: User = Depends(_export), db: DbSession = Depends(get_db)):
    job = service.owned_batch(db, user, bid)
    data, name = service.export(db, user, job, client_ip(request))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={**_NO_STORE, "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                             "X-Content-Type-Options": "nosniff"})


@router.delete("/batches/{bid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(bid: str, user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    service.clear_batch(db, service.owned_batch(db, user, bid))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
