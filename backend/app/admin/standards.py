"""公司范本管理，只允许业务管理员，不回传向量。"""
import json
import uuid
from fastapi import APIRouter,Depends,File,Form,UploadFile
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.audit import service as audit
from app.config import get_settings
from app.db.models import StandardTemplate,DOC_TYPES,STANCES,User
from app.db.session import get_db
from app.security.deps import require_biz_admin
from app.security.errors import AppError
from app.security.crypto import encrypt_text
from app.standards import service

router=APIRouter(prefix='/admin/templates',dependencies=[Depends(require_biz_admin)])


class TemplateIn(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    name:str=Field(min_length=1,max_length=100)
    doc_types:list[str]=Field(min_length=1)
    stances:list[str]=Field(min_length=1)
    @field_validator('doc_types','stances')
    @classmethod
    def scope(cls,value,info):
        if any(v not in (DOC_TYPES if info.field_name=='doc_types' else STANCES) for v in value):raise ValueError('范围不正确')
        return list(dict.fromkeys(value))
    @field_validator('name')
    @classmethod
    def name_value(cls,value):
        if not value.strip():raise ValueError('名称不能为空')
        return value.strip()


class TemplatePatch(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    name:str|None=Field(None,min_length=1,max_length=100)
    doc_types:list[str]|None=None
    stances:list[str]|None=None
    enabled:bool|None=None


class ClausePatch(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    stance:str|None=Field(None,max_length=300)
    acceptable:str|None=Field(None,max_length=300)
    escalate:str|None=Field(None,max_length=300)


@router.get('')
def listing(db:Session=Depends(get_db)):
    return {'items':[service.view(db,r) for r in db.scalars(select(StandardTemplate).order_by(StandardTemplate.created_at.desc()))]}


@router.post('',status_code=201)
def upload(name:str=Form(...),doc_types:str=Form(...),stances:str=Form(...),file:UploadFile=File(...),
           admin:User=Depends(require_biz_admin),db:Session=Depends(get_db)):
    try:values=TemplateIn(name=name,doc_types=json.loads(doc_types),stances=json.loads(stances)).model_dump()
    except (ValueError,TypeError):raise AppError(422,'VALIDATION_ERROR','请填写范本名称、文件类型和立场') from None
    limit=get_settings().max_upload_mb*1024*1024
    data=file.file.read(limit+1)
    if len(data)>limit:raise AppError(422,'FILE_TOO_LARGE','范本文件超过上传限制')
    row=service.create(db,data,values,admin.id)
    db.commit()
    return service.view(db,row,True)


@router.get('/{tid}')
def detail(tid:uuid.UUID,db:Session=Depends(get_db)):
    return service.view(db,service.load(db,tid),True)


@router.patch('/{tid}')
def patch(tid:uuid.UUID,body:TemplatePatch,admin:User=Depends(require_biz_admin),db:Session=Depends(get_db)):
    row=service.load(db,tid,True)
    values=body.model_dump(exclude_unset=True)
    try:
        validated=TemplateIn(name=values.get('name',service.view(db,row)['name']),
                             doc_types=values.get('doc_types',row.doc_types),stances=values.get('stances',row.stances))
    except ValueError:raise AppError(422,'VALIDATION_ERROR','范本名称或适用范围不正确') from None
    if 'enabled' in values and values['enabled'] is None:raise AppError(422,'VALIDATION_ERROR','启用状态不正确')
    row.name_enc=encrypt_text(validated.name);row.doc_types=validated.doc_types;row.stances=validated.stances
    if 'enabled' in values:row.enabled=values['enabled']
    audit.record(db,'template_update',user_id=admin.id,target_type='template',target_id=str(tid),detail={})
    db.commit();return service.view(db,row)


@router.delete('/{tid}',status_code=204)
def remove(tid:uuid.UUID,admin:User=Depends(require_biz_admin),db:Session=Depends(get_db)):
    db.delete(service.load(db,tid,True))
    audit.record(db,'template_delete',user_id=admin.id,target_type='template',target_id=str(tid),detail={})
    db.commit()


@router.patch('/{tid}/clauses/{cid}')
def patch_clause(tid:uuid.UUID,cid:uuid.UUID,body:ClausePatch,admin:User=Depends(require_biz_admin),db:Session=Depends(get_db)):
    c=service.update_clause(db,tid,cid,body.model_dump(exclude_unset=True),admin.id)
    db.commit();return dict(id=c.id,seq=c.seq,**service.decoded(c))
