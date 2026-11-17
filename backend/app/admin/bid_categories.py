"""标书分类维护（V34-B-01a）：业务管理员增删、改名、停用，全公司统一。

已被项目使用的分类不能删除，只能停用；停用后新建与项目设置不能再选，已用的项目保留。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bid.folders import categories
from app.db.models import BidCategory, BidProject
from app.db.session import get_db
from app.security.errors import AppError, NotFoundError
from app.security.permissions import require

router = APIRouter(prefix="/admin/bid-categories", dependencies=[Depends(require("content.bid_categories"))])
MAX_CATEGORIES = 30


class CategoryInput(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=20)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def strip(cls, value):
        if value is not None and not value.strip():
            raise ValueError("请填写分类名称")
        return value.strip() if value else value


def _view(db):
    used = dict(db.execute(select(BidProject.category_id, func.count()).where(BidProject.category_id.is_not(None))
                           .group_by(BidProject.category_id)).all())
    return {"items": [{**c, "used": used.get(c["id"], 0)} for c in categories(db)]}


def _row(db, cid: int) -> BidCategory:
    row = db.get(BidCategory, cid)
    if row is None:
        raise NotFoundError("CATEGORY_NOT_FOUND", "分类不存在")
    return row


def _unique(db, name: str, cid: int | None = None):
    hit = db.scalar(select(BidCategory.id).where(BidCategory.name == name))
    if hit is not None and hit != cid:
        raise AppError(409, "CATEGORY_EXISTS", "已有同名分类")


@router.get("")
def list_categories(db: Session = Depends(get_db)):
    return _view(db)


@router.post("", status_code=201)
def create_category(body: CategoryInput, db: Session = Depends(get_db)):
    if not body.name:
        raise AppError(422, "CATEGORY_NAME", "请填写分类名称")
    if (db.scalar(select(func.count()).select_from(BidCategory)) or 0) >= MAX_CATEGORIES:
        raise AppError(422, "TOO_MANY_CATEGORIES", f"分类最多 {MAX_CATEGORIES} 个")
    _unique(db, body.name)
    db.add(BidCategory(name=body.name, seq=(db.scalar(select(func.max(BidCategory.seq))) or 0) + 1, enabled=True))
    db.commit()
    return _view(db)


@router.patch("/{cid}")
def update_category(cid: int, body: CategoryInput, db: Session = Depends(get_db)):
    row = _row(db, cid)
    if body.name is not None:
        _unique(db, body.name, cid)
        row.name = body.name
    if body.enabled is not None:
        if not body.enabled and row.enabled and not db.scalar(select(func.count()).select_from(BidCategory).where(
                BidCategory.enabled.is_(True), BidCategory.id != cid)):
            raise AppError(409, "LAST_CATEGORY", "至少保留一个启用的分类")
        row.enabled = body.enabled
    db.commit()
    return _view(db)


@router.delete("/{cid}")
def delete_category(cid: int, db: Session = Depends(get_db)):
    row = _row(db, cid)
    if db.scalar(select(BidProject.task_id).where(BidProject.category_id == cid).limit(1)):
        raise AppError(409, "CATEGORY_IN_USE", "已有项目使用这个分类，不能删除，可以停用")
    if row.enabled and not db.scalar(select(func.count()).select_from(BidCategory).where(
            BidCategory.enabled.is_(True), BidCategory.id != cid)):
        raise AppError(409, "LAST_CATEGORY", "至少保留一个启用的分类")
    db.delete(row)
    db.commit()
    return _view(db)
