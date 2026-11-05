"""FastAPI 入口。所有路由统一挂载在 /api/v1 下（SPEC 19.1）。"""
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from app.admin.model import router as admin_model_router
from app.models_api import router as models_router
from app.admin.laws import router as admin_laws_router
from app.admin.standards import router as admin_standards_router
from app.admin.data import router as admin_data_router
from app.admin.redaction import router as admin_redaction_router
from app.admin.requirements import router as admin_requirements_router
from app.admin.users import router as admin_users_router
from app.auth.router import router as auth_router
from app.assistant.router import router as assistant_router
from app.chat.router import router as chat_router
from app.checks.router import router as checks_router
from app.export.router import router as export_router
from app.config import get_settings, setup_logging
from app.security.crypto import check_master_key
from app.security.csrf import csrf_guard
from app.review.router import router as review_router
from app.redaction.router import router as redaction_router
from app.security.errors import register_error_handlers
from app.tasks.router import router as tasks_router
from app.tasks.search import router as search_router

API_PREFIX = "/api/v1"


def build_api_router() -> APIRouter:
    """/api/v1 总路由。CSRF 校验挂在这里，对其下所有修改类接口生效（SPEC 17.1）。

    各模块的路由在后续任务中 include 到这里。
    """
    from app.kb.router import router as kb_router
    router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(csrf_guard)])
    router.include_router(kb_router)
    router.include_router(auth_router)
    router.include_router(assistant_router)
    router.include_router(tasks_router)
    router.include_router(search_router)
    router.include_router(review_router)
    router.include_router(redaction_router)
    router.include_router(checks_router)
    router.include_router(export_router)
    router.include_router(chat_router)
    router.include_router(admin_users_router)
    router.include_router(admin_model_router)
    router.include_router(models_router)
    router.include_router(admin_requirements_router)
    router.include_router(admin_redaction_router)
    router.include_router(admin_data_router)
    router.include_router(admin_laws_router)
    router.include_router(admin_standards_router)
    return router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # 主密钥缺失或不是 32 字节时拒绝启动（SPEC 17.3、open_questions Q-08）
    check_master_key()
    from app.redaction import ner
    ner.check()
    from app.embedding import engine
    engine.check()
    # 后台预热（V31-U5F-03 测试机实测）：确认页第一次决定要用实体识别与向量模型，冷加载在测试机上约 40 秒。
    # 启动后在后台线程加载，不阻塞启动；失败只记一条警告，第一次使用时照常再加载。
    def warm() -> None:
        try:
            ner._get_model()
            engine._get_model()
        except Exception:
            logging.getLogger("app.main").warning("本地模型预热失败，将在第一次使用时加载")
    threading.Thread(target=warm, name="model-warmup", daemon=True).start()
    yield


def create_app() -> FastAPI:
    setup_logging()
    # 不暴露自动生成的接口文档
    app = FastAPI(
        title="商衡SI",
        version=get_settings().app_version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(build_api_router())
    return app


app = create_app()
