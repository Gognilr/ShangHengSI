"""模拟模型服务的回复规则（设计 docs/design/M2.md 第七节）。

测试中用 mock_transport() 注入网关（httpx.MockTransport）；tools/mock_llm_server.py 用同一套规则
起一个 OpenAI 兼容的本地服务供联调。本模块顶层只用标准库，httpx 在 mock_transport() 内才导入。

回复模式：
  normal               按请求中的【审核要求】逐条给结论（第一条 issue，其余 ok），对第一个正文位置给一个问题，quote 逐字摘录
  fenced               normal 的内容外面包一层 ```json
  non_json             不是 JSON 的文字
  bad_loc              问题的 locs 是不存在的位置
  fake_quote           quote 是原文中没有的文字
  missing_requirement  漏掉最后一条审核要求的结论
  http_500             返回 HTTP 500
  timeout              超时（只在 mock_transport 中有效）
"""
import json
import re

MODES = ("normal", "fenced", "non_json", "bad_loc", "fake_quote", "missing_requirement", "http_500", "timeout")

_REQ_LINE = re.compile(r"^\[([A-Z]{1,2}-\d+|CO-\d+|NOTE)\]", re.MULTILINE)
_DOC_LINE = re.compile(r"^\[(F\d+-[^\]]+)\] ?(.*)$", re.MULTILINE)


def _user_text(messages: list[dict]) -> str:
    return "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")


def build_content(messages: list[dict], mode: str = "normal") -> str:
    """按模式生成 assistant 消息的 content。"""
    if mode == "non_json":
        return "好的，我已经审核完毕，整体没有问题。"
    text = _user_text(messages)
    req_section = text.split("【审核要求】", 1)[1].split("【", 1)[0] if "【审核要求】" in text else ""
    req_ids = _REQ_LINE.findall(req_section)
    doc = text.split("<document>", 1)[1] if "<document>" in text else ""
    lines = _DOC_LINE.findall(doc)
    results = [{"req_id": rid, "verdict": "issue" if i == 0 else "ok", "note": "模拟结论"}
               for i, rid in enumerate(req_ids)]
    if mode == "missing_requirement" and results:
        results.pop()
    findings = []
    if lines:
        loc, line_text = lines[0]
        quote = line_text[:20] if line_text else ""
        if mode == "bad_loc":
            loc = "F9-P999"
        if mode == "fake_quote":
            quote = "这是一段原文中不存在的编造引用"
        findings.append({
            "title": "模拟问题", "category": "商务", "severity": "中",
            "req_ids": req_ids[:1], "locs": [loc], "quote": quote, "missing": False,
            "problem": "模拟的问题说明", "impact": "对我方的模拟影响", "suggestion": "模拟的修改建议",
            "suggested_text": None, "basis": "通行商业惯例",
        })
    output = {
        "doc_summary": "模拟摘要：这是一份测试文件。",
        "requirement_results": results,
        "findings": findings,
        "sanctions": {"level": "无明显风险", "reasons": []},
    }
    content = json.dumps(output, ensure_ascii=False)
    if mode == "fenced":
        content = f"```json\n{content}\n```"
    return content


def build_reply(body: dict, mode: str = "normal") -> tuple[int, dict]:
    """返回 (HTTP 状态码, 响应 JSON)。"""
    if mode == "http_500":
        return 500, {"error": {"message": "mock internal error", "type": "server_error"}}
    messages = body.get("messages") or []
    content = build_content(messages, mode)
    prompt_chars = sum(len(m.get("content", "")) for m in messages)
    return 200, {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "model": body.get("model", "mock"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": max(1, prompt_chars // 2), "completion_tokens": max(1, len(content) // 2)},
    }


def mock_transport(modes: str | list[str] = "normal", requests: list | None = None):
    """httpx.MockTransport：依次按 modes 回复（列表用完后重复最后一个）；requests 收集收到的请求。"""
    import httpx

    queue = [modes] if isinstance(modes, str) else list(modes)

    def handler(request: httpx.Request) -> httpx.Response:
        mode = queue.pop(0) if len(queue) > 1 else queue[0]
        if requests is not None:
            requests.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("mock timeout", request=request)
        status, payload = build_reply(json.loads(request.content), mode)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)
