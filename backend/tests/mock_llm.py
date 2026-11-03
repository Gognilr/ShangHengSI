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
  rich                 界面联调（V31-U3-06）：审核给前三个正文位置各一个问题（高 / 中 / 低），文字含文件中没有的代号、
                       代号写法变体、无效日期，第二条审核要求为“信息已脱敏”的无法判断；追问回复含一行【算式】
"""
import json
import re

MODES = ("normal", "fenced", "non_json", "bad_loc", "fake_quote", "missing_requirement", "http_500", "timeout", "rich")
_CODE = re.compile(r"\[(?:金额|单价|数量)_\d+\]")


def _rich_chat(text: str) -> str:
    codes = _CODE.findall(text)
    expr = f"{codes[0]} × 2" if codes else "1200 × 3"
    return ("关于[客户_C]的付款安排：客户A 应在 2031年2月30日 前付清。\n"
            f"【算式】两期合计 = {expr}\n"
            "以上为模拟回复，请以人工核对为准。")


def _rich_review(req_ids: list[str], lines: list[tuple[str, str]]) -> str:
    results = [{"req_id": rid, "verdict": "issue" if i == 0 else "unable" if i == 1 else "ok", "note": "模拟结论",
                **({"unable_reason": "redacted"} if i == 1 else {})} for i, rid in enumerate(req_ids)]
    texts = [
        ("[客户_C] 的付款期限不明确", "客户A 未约定 2031年2月30日 之后的违约责任", "建议补充逾期付款的违约金条款",
         "买方应于收货后 30 日内付清全部货款，逾期按日万分之五支付违约金。"),
        ("交货条款缺少验收标准", "未写明验收方式与异议期限", "补充验收与异议期限", None),
        ("争议解决条款可再明确", "未约定仲裁机构", "约定具体的仲裁机构", None),
    ]
    findings = []
    for (loc, line_text), (title, problem, suggestion, suggested), severity in zip(lines, texts, ("高", "中", "低")):
        findings.append({
            "title": title, "category": "商务", "severity": severity, "req_ids": req_ids[:1], "locs": [loc],
            "quote": line_text[:20], "missing": False, "problem": problem, "impact": "对我方的模拟影响",
            "suggestion": suggestion, "suggested_text": suggested, "basis": "通行商业惯例"})
    return json.dumps({"doc_summary": "模拟摘要：这是一份测试文件，涉及客户A。", "requirement_results": results,
                       "findings": findings, "sanctions": {"level": "需注意", "reasons": ["模拟的制裁提示"]}},
                      ensure_ascii=False)

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
    if mode == "rich":
        last = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        return _rich_chat(text) if "【审核要求】" not in last else _rich_review(req_ids, lines)
    results =[{"req_id": rid, "verdict": "issue" if i == 0 else "ok", "note": "模拟结论"}
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
