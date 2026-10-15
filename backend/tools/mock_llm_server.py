"""OpenAI 兼容的模拟模型服务，仅供本地联调（设计 docs/design/M2.md 第七节）。

回复规则与 tests/mock_llm.py 相同。只用标准库，监听 0.0.0.0:9000，接口 POST /v1/chat/completions。
回复模式取环境变量 MOCK_LLM_MODE（默认 normal，可选值见 tests/mock_llm.py）；mode=timeout 时等待 MOCK_LLM_DELAY 秒（默认 600）。

  docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.mock.yml up -d
  然后把模型接口地址设为 http://mock-llm:9000/v1（python -m app.cli set-model …），请求仍经过网关的全部步骤。
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from mock_llm import MODES, build_reply  # noqa: E402

MODE = os.environ.get("MOCK_LLM_MODE", "normal")
PORT = int(os.environ.get("MOCK_LLM_PORT", "9000"))


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": {"message": "not found"}})
            return
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self._send(401, {"error": {"message": "missing api key"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "invalid json"}})
            return
        if MODE == "timeout":
            time.sleep(float(os.environ.get("MOCK_LLM_DELAY", "600")))
        status, payload = build_reply(body, MODE)
        self._send(status, payload)

    def log_message(self, fmt: str, *args) -> None:  # 只记请求行，不记内容
        sys.stderr.write(f"mock-llm {self.address_string()} {fmt % args}\n")


def main() -> None:
    if MODE not in MODES:
        sys.exit(f"MOCK_LLM_MODE 取值无效：{MODE}，可选 {', '.join(MODES)}")
    print(f"mock-llm 监听 0.0.0.0:{PORT}，模式 {MODE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
