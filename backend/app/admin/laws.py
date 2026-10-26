"""法规库管理接口，仅业务管理员可用。"""
import uuid
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.config import get_settings
from app.db import jobs
from app.db.models import LAW_STATUSES, Law, LawImport, LawImportFile, User
from app.db.session import get_db
from app.laws import imports
from app.security import crypto
from app.security.deps import require_biz_admin
from app.security.errors import AppError, NotFoundError
from app.security.sessions import client_ip

router = APIRouter(prefix='/admin', dependencies=[Depends(require_biz_admin)])
LawStatus = Literal['not_yet', 'valid', 'amended', 'repealed']


def law_dict(row):
    return {**{k: getattr(row, k) for k in ('id', 'title', 'category', 'status', 'published_on',
                                          'effective_on', 'replaced_by', 'source')},
            'has_fulltext': row.fulltext_at is not None}


def import_dict(row, *, detail=False):
    fields = ['id', 'kind', 'status', 'declared_status', 'file_names', 'added', 'changed', 'unchanged',
              'skipped', 'error', 'created_by', 'created_at', 'applied_by', 'applied_at']
    if detail:
        fields.append('diff')
    return {k: getattr(row, k) for k in fields}


def record_audit(db, request, user, action, target, detail):
    audit.record(db, action, user_id=user.id, target_type='law_import' if action.startswith('law_import') else 'law',
                 target_id=str(target), detail=detail, ip=client_ip(request))


@router.post('/law-imports', status_code=202)
def upload_import(request: Request, kind: Literal['catalog', 'fulltext'] = Form(...), status: LawStatus | None = Form(None),
                  files: list[UploadFile] = File(...), admin: User = Depends(require_biz_admin),
                  db: Session = Depends(get_db)):
    if kind == 'catalog' and status is None:
        raise AppError(422, 'LAW_IMPORT_STATUS', '请选择目录的时效性')
    if kind == 'fulltext' and len(files) != 1:
        raise AppError(422, 'LAW_IMPORT_FILES', '请选择一个全文 ZIP 压缩包')
    if not 1 <= len(files) <= 10:
        raise AppError(422, 'LAW_IMPORT_FILES', '请选择 1～10 个目录文件')
    imports.lock(db)
    if db.scalar(select(LawImport.id).where(LawImport.status.in_(['parsing', 'pending'])).limit(1)):
        raise AppError(409, 'LAW_IMPORT_PENDING', '有一项法规库更新尚未处理，请先确认或放弃')
    names, contents = [], []
    for file in files:
        # 浏览器可能发送含路径的名称，只保留末段；从不将用户文件名传给解析器或写入日志。
        name = PurePosixPath((file.filename or '').replace('\\', '/')).name
        allowed = ('.zip',) if kind == 'fulltext' else ('.xls', '.xlsx')
        if not name or len(name) > 255 or PurePosixPath(name).suffix.lower() not in allowed:
            if kind == 'fulltext':
                raise AppError(422, 'LAW_IMPORT_FILES', '全文文件须为 ZIP，文件名最多 255 字')
            raise AppError(422, 'LAW_IMPORT_FILES', '目录文件须为 XLS 或 XLSX，文件名最多 255 字')
        limit = get_settings().max_upload_mb if kind == 'fulltext' else 5
        content = file.file.read(limit*1024*1024+1)
        if len(content) > limit*1024*1024:
            raise AppError(422, 'LAW_IMPORT_FILES', f'每个文件最多 {limit} MB')
        names.append(name)
        contents.append(content)
    row = LawImport(kind=kind, status='parsing', declared_status=status if kind == 'catalog' else None,
                    file_names=names, created_by=admin.id)
    db.add(row)
    db.flush()
    for seq, (name, content) in enumerate(zip(names, contents), 1):
        db.add(LawImportFile(import_id=row.id, seq=seq, name=name, data_enc=crypto.encrypt(content)))
    jobs.enqueue(db, 'law_import', {'import_id': str(row.id)})
    record_audit(db, request, admin, 'law_import_upload', row.id,
                 {'kind': kind, 'files': len(files), 'status': status})
    db.commit()
    return {'id': row.id}


@router.get('/law-imports')
def list_imports(db: Session = Depends(get_db)):
    return {'items': [import_dict(row) for row in db.scalars(
        select(LawImport).order_by(LawImport.created_at.desc(), LawImport.id).limit(20))]}


@router.get('/law-imports/{iid}')
def get_import(iid: uuid.UUID, db: Session = Depends(get_db)):
    return import_dict(imports.load(db, iid), detail=True)


@router.post('/law-imports/{iid}/apply')
def apply_import(iid: uuid.UUID, request: Request, admin: User = Depends(require_biz_admin),
                 db: Session = Depends(get_db)):
    row = imports.apply(db, iid, admin.id)
    record_audit(db, request, admin, 'law_import_apply', iid,
                 {'kind': row.kind, 'added': row.added, 'changed': row.changed})
    db.commit()
    return import_dict(row, detail=True)


@router.post('/law-imports/{iid}/discard')
def discard_import(iid: uuid.UUID, request: Request, admin: User = Depends(require_biz_admin),
                   db: Session = Depends(get_db)):
    row = imports.discard(db, iid, admin.id)
    record_audit(db, request, admin, 'law_import_discard', iid, {'kind': row.kind})
    db.commit()
    return import_dict(row, detail=True)


@router.get('/laws')
def list_laws(q: str = Query('', max_length=300), category: str = Query('', max_length=60),
              status: LawStatus | None = None, page: int = Query(1, ge=1),
              page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    stmt = select(Law)
    if q:
        stmt = stmt.where(Law.title.contains(q, autoescape=True))
    if category:
        stmt = stmt.where(Law.category == category)
    if status:
        stmt = stmt.where(Law.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    counts = dict.fromkeys(LAW_STATUSES, 0)
    counts.update(dict(db.execute(select(Law.status, func.count()).group_by(Law.status)).all()))
    counts['total'] = sum(counts.values())
    return {'items': [law_dict(r) for r in db.scalars(stmt.order_by(Law.title, Law.published_on.desc(), Law.id)
                     .offset((page-1)*page_size).limit(page_size))],
            'page': page, 'page_size': page_size, 'total': total, 'counts': counts}


class LawPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    replaced_by: str | None = Field(max_length=300)


@router.patch('/laws/{law_id}')
def patch_law(law_id: int, body: LawPatch, request: Request, admin: User = Depends(require_biz_admin),
              db: Session = Depends(get_db)):
    imports.lock(db)
    row = db.get(Law, law_id, with_for_update=True)
    if row is None:
        raise NotFoundError('LAW_NOT_FOUND', '法规不存在')
    row.replaced_by = (body.replaced_by or '').strip() or None
    record_audit(db, request, admin, 'law_update', row.id, {})
    db.commit()
    return law_dict(row)
