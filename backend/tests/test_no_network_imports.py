"""T-20：除 app/llm/gateway.py 外，项目代码中没有任何外部网络调用（SPEC 5、23.2）。

扫描范围是 backend/app/ 下的项目代码（open_questions Q-09），不含 tests 和第三方库。
用语法树检查，比 SPEC 列出的 httpx、requests、urllib.request 更严：凡能直接发起网络连接的标准库和常见第三方库都禁止，
包括通过 importlib.import_module / __import__ 动态导入。
"""
import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
GATEWAY = APP_DIR / "llm" / "gateway.py"
CONVERTER_DIR = Path(__file__).resolve().parents[2] / "converter"  # 仓库根目录下；只挂载 backend 时不存在

FORBIDDEN = {
    "httpx",
    "requests",
    "urllib.request",
    "urllib3",
    "http.client",
    "aiohttp",
    "socket",
    "ssl",
    "smtplib",
    "ftplib",
    "telnetlib",
    "websocket",
    "websockets",
    "xmlrpc.client",
}


def _forbidden(module: str) -> bool:
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN)


def find_network_imports(source: str) -> list[str]:
    """返回源码中所有被禁止的导入（模块名）。"""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if _forbidden(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _forbidden(node.module):
                hits.append(node.module)
            # from urllib import request / from http import client
            hits += [f"{node.module}.{a.name}" for a in node.names if _forbidden(f"{node.module}.{a.name}")]
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _forbidden(arg.value):
                    hits.append(arg.value)
    return hits


def _app_files() -> list[Path]:
    return sorted(p for p in APP_DIR.rglob("*.py") if p != GATEWAY)


def _converter_files() -> list[Path]:
    """converter 容器的代码（R2）：同样不得有网络调用，它本来就断网。"""
    return sorted(CONVERTER_DIR.rglob("*.py")) if CONVERTER_DIR.is_dir() else []


def test_scan_covers_project_code():
    files = _app_files()
    assert len(files) >= 10
    assert APP_DIR / "main.py" in files


@pytest.mark.skipif(not CONVERTER_DIR.is_dir(), reason="需要整仓挂载才有 converter/")
def test_scan_covers_converter():
    assert CONVERTER_DIR / "main.py" in _converter_files()


def test_no_network_calls_outside_gateway():
    violations = {}
    for path in _app_files() + _converter_files():
        hits = find_network_imports(path.read_text(encoding="utf-8"))
        if hits:
            violations[str(path.relative_to(CONVERTER_DIR.parent if CONVERTER_DIR in path.parents else APP_DIR.parent))] = hits
    assert violations == {}, f"只有 app/llm/gateway.py 可以访问网络：{violations}"


@pytest.mark.parametrize(
    "snippet",
    [
        "import httpx",
        "import requests",
        "import urllib.request",
        "from urllib import request",
        "from urllib.request import urlopen",
        "from http import client",
        "import http.client as hc",
        "import socket",
        "from requests.adapters import HTTPAdapter",
        "import importlib\nimportlib.import_module('httpx')",
        "__import__('requests')",
    ],
)
def test_detector_catches_network_imports(snippet):
    assert find_network_imports(snippet)


@pytest.mark.parametrize(
    "snippet",
    ["import json", "from urllib.parse import quote", "import http", "from . import socket_utils", "import hashlib"],
)
def test_detector_allows_harmless_imports(snippet):
    assert find_network_imports(snippet) == []
