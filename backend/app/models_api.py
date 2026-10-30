"""普通用户模型列表与业务模型选择；不暴露连接信息。"""
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.settings_store import get_setting
from app.db.models import LlmModel, User
from app.db.session import get_db
from app.security.deps import current_user
from app.security.errors import AppError

router = APIRouter()


def select_model(db: Session, model_id: int | None, purpose: str, *, structured: bool = False) -> LlmModel:
    if model_id is None:
        model_id = get_setting(db, f"model.default_{purpose}_id")
    model = db.get(LlmModel, model_id) if model_id is not None else None
    if (model is None or not model.enabled or not getattr(model, f"for_{purpose}")
            or (structured and not model.structured_ok)):
        raise AppError(422, "MODEL_NOT_ALLOWED", "所选模型不可用于本次操作，请换一个模型或联系管理员")
    return model


@router.get("/models")
def list_models(purpose: Literal["review", "assist"], _user: User = Depends(current_user), db: Session = Depends(get_db)):
    default_id = get_setting(db, f"model.default_{purpose}_id")
    rows = db.scalars(select(LlmModel).where(LlmModel.enabled.is_(True),
                     getattr(LlmModel, f"for_{purpose}").is_(True)).order_by(LlmModel.sort_order, LlmModel.id))
    return {"items": [{"id": m.id, "display_name": m.display_name, "description": m.description,
                       "is_default": m.id == default_id, "structured_ok": m.structured_ok} for m in rows]}
