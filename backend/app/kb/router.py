"""知识库管理；所有接口仅业务管理员及兼任可用。"""
from fastapi import APIRouter, Depends, UploadFile, File
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import KbDoc
from app.security.deps import require_biz_admin
from app.security.errors import AppError
from app.admin.settings_store import get_setting, set_setting
from app.kb import service

router = APIRouter(prefix='/admin/kb', dependencies=[Depends(require_biz_admin)])


class Patch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool | None = None
    name: str | None = Field(None, min_length=1, max_length=255)

    @field_validator('name')
    @classmethod
    def nonempty(cls, value):
        if value is not None and not value.strip():
            raise ValueError('请输入文档名')
        return value


class Switch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool


@router.get('')
def listing(db=Depends(get_db)):
    return {'items': [service.view(db, row) for row in db.scalars(select(KbDoc).order_by(KbDoc.created_at.desc(), KbDoc.id))],
            'enabled': bool(get_setting(db, 'assistant.kb_enabled'))}


@router.patch('/settings')
def settings(body: Switch, db=Depends(get_db), user=Depends(require_biz_admin)):
    set_setting(db, 'assistant.kb_enabled', body.enabled, user.id)
    db.commit()
    return {'enabled': body.enabled}


@router.post('', status_code=201)
def upload(file: UploadFile = File(...), db=Depends(get_db), user=Depends(require_biz_admin)):
    row = service.create(db, file, user)
    result = service.view(db, row)
    db.commit()
    return result


@router.get('/{id}/chunks')
def chunks(id: str, db=Depends(get_db)):
    return service.chunks(db, service.load(db, id))


@router.patch('/{id}')
def patch(id: str, body: Patch, db=Depends(get_db), user=Depends(require_biz_admin)):
    values = body.model_dump(exclude_unset=True)
    if not values or any(value is None for value in values.values()):
        raise AppError(422, 'VALIDATION_ERROR', '请输入修改内容')
    row = service.load(db, id, locked=True)
    service.update(db, row, values, user)
    result = service.view(db, row)
    db.commit()
    return result


@router.delete('/{id}')
def delete(id: str, db=Depends(get_db), user=Depends(require_biz_admin)):
    service.remove(db, service.load(db, id, locked=True), user)
    db.commit()
    return {'ok': True}
