"""FastAPI 入口。所有路由统一挂载在 /api/v1 下（SPEC 19.1）。"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from app.auth.router import router as auth_router
from app.checks.router import router as checks_router
from app.config import get_settings, setup_logging
from app.security.crypto import check_master_key
from app.security.csrf import csrf_guard
from app.review.router import router as review_router
from app.redaction.router import router as redaction_router
from app.security.errors import register_error_handlers
from app.tasks.router import router as tasks_router

API_PREFIX = "/api/v1"


def build_api_router() -> APIRouter:
    """/api/v1 总路由。CSRF 校验挂在这里，对其下所有修改类接口生效（SPEC 17.1）。

    各模块的路由在后续任务中 include 到这里。
    """
    router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(csrf_guard)])
    router.include_router(auth_router)
    router.include_router(tasks_router)
    router.include_router(review_router)
    router.include_router(redaction_router)
    router.include_router(checks_router)
    return router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # 主密钥缺失或不是 32 字节时拒绝启动（SPEC 17.3、open_questions Q-08）
    check_master_key()
    yield


def create_app() -> FastAPI:
    setup_logging()
    # 不暴露自动生成的接口文档
    app = FastAPI(
        title="企业商务文件AI智能审查助手",
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
