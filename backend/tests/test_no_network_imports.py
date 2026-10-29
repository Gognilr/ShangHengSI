"""T-20：除 app/llm/gateway.py 与 app/lawsync/client.py 外，backend/app 不导入网络库（SPEC 5、23.2）。

出口在网络层另有封堵，这里只做一条简单的源码检查。
"""
import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
ALLOWED = {APP_DIR / "llm" / "gateway.py", APP_DIR / "lawsync" / "client.py"}
FORBIDDEN = {"httpx", "requests", "urllib.request", "urllib3", "http.client", "aiohttp", "socket"}


def _imports(source: str) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
    return [n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)]


def test_no_network_imports_outside_gateway_and_lawsync():
    violations = {str(p.relative_to(APP_DIR)): hits for p in sorted(APP_DIR.rglob("*.py"))
                  if p not in ALLOWED and (hits := _imports(p.read_text(encoding="utf-8")))}
    assert violations == {}
