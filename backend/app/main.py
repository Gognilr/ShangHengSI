"""FastAPI 入口。所有路由统一挂载在 /api/v1 下（SPEC 19.1）。"""
from fastapi import APIRouter, FastAPI

from app.config import get_settings, setup_logging
from app.security.errors import register_error_handlers

API_PREFIX = "/api/v1"

# 各模块的路由在后续任务中 include 到这个总路由
api_router = APIRouter(prefix=API_PREFIX)


def create_app() -> FastAPI:
    setup_logging()
    # 不暴露自动生成的接口文档
    app = FastAPI(
        title="企业商务文件AI智能审查助手",
        version=get_settings().app_version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
