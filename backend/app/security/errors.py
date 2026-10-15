"""统一错误格式 {"code", "message"}（SPEC 19.1）。

错误响应与日志都不得回显请求内容：422 只返回固定说明，500 只记录异常类型。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """业务错误。status 为 HTTP 状态码，code 为大写下划线错误码，message 为中文说明。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


class NotFoundError(AppError):
    """不存在或不属于当前用户，二者对外不可区分（SPEC 17.2）。"""

    def __init__(self, code: str = "NOT_FOUND", message: str = "资源不存在") -> None:
        super().__init__(404, code, message)


# 框架自身产生的 HTTP 错误（路由未命中等）按状态码映射到统一格式
_HTTP_ERRORS = {
    401: ("UNAUTHORIZED", "请先登录"),
    403: ("FORBIDDEN", "没有权限执行此操作"),
    404: ("NOT_FOUND", "资源不存在"),
    405: ("METHOD_NOT_ALLOWED", "请求方法不被允许"),
    409: ("CONFLICT", "当前状态不允许此操作"),
    413: ("PAYLOAD_TOO_LARGE", "上传内容过大"),
}


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _HTTP_ERRORS.get(exc.status_code, ("HTTP_ERROR", "请求失败"))
        return error_response(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        # 不回显具体字段值，避免把请求内容写进响应
        return error_response(422, "VALIDATION_ERROR", "请求参数不正确")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 只记录异常类型和路径，不输出堆栈（堆栈可能带出节点文本，SPEC 17.7）
        logger.error("未处理的异常 %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return error_response(500, "INTERNAL_ERROR", "服务器内部错误")
