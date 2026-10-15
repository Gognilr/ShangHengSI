"""测试公共夹具（基础部分）。测试样本只用虚构内容，模型调用一律用模拟服务。"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    # raise_server_exceptions=False：让未处理异常走统一的 500 处理器，便于断言错误格式
    return TestClient(app, raise_server_exceptions=False)
