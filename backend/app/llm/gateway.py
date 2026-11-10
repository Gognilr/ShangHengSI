"""模型网关 —— 全系统唯一可以访问外网的模块（SPEC 5、11.1、11.7、17.4，设计 docs/design/M2.md 第七节）。

call_model() 依次执行：读取模型 → 调用上限检查 → 出口复检 → 发送（失败重试 2 次；追问不重试）→ 每次尝试写一条 llm_calls。
- 出口复检对全部 messages 的 content 拼接后检查（9.8）；拦截时写一条 status=blocked、request_text 为空、
  block_reason 只含类别和位置的记录，然后抛出 GatewayError("BLOCKED")，不发送。
- 请求体：model、messages、temperature、max_tokens，json_mode 开启时加 response_format，再合并附加请求参数。
  附加请求参数不能覆盖 messages（否则可绕过出口复检），该键被忽略。
- llm_calls 用独立的数据库会话写入并立即提交：调用方回滚自己的事务时，外发记录仍然保留。
- 审核调用传 on_delta 时流式接收（V31-U5F-03），只改接收方式，上限、复检、记录与重试不变。
- API 密钥只出现在请求头中；写入 llm_calls 的文字中若出现密钥原文一律替换为 ***；日志只写记录 id、用途、状态、耗时。
"""
import json
import logging
import re
import time
import threading
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.db.models import LlmCall, LlmModel
from app.llm import usage
from app.redaction.recheck import BlockReason, recheck, recheck_context
from app.security import crypto

logger = logging.getLogger("app.llm.gateway")

RETRY_DELAYS: tuple[float, ...] = (10, 30)  # SPEC 7.5：重试 2 次，间隔 10 秒、30 秒（测试中改为 0）
TRANSPORT: httpx.BaseTransport | None = None  # 测试中注入 httpx.MockTransport
_RESPONSE_KEEP_CHARS = 2000  # 出错时保留的返回内容长度
STREAM_TOTAL_SECONDS = 600
_RETRYABLE_STATUS = {408, 409, 425, 429}
TEST_MESSAGE = "这是一条连接测试消息，不含任何业务数据。请只回复：OK"

MESSAGES = {
    "MODEL_UNAVAILABLE": "所选模型已停用，请换一个模型",
    "NOT_CONFIGURED": "模型尚未配置，请联系管理员",
    "CAP_REACHED": "已达本月调用上限，请联系管理员",
    "USER_CAP_REACHED": "已达今日个人调用上限，请明天再试或联系管理员",
    "BLOCKED": "出口复检发现未脱敏的信息，已拦截，未发送",
    "HTTP_ERROR": "模型服务调用失败",
    "TIMEOUT": "模型服务响应超时",
    "BAD_RESPONSE": "模型服务返回的内容无法识别",
}


@dataclass
class ModelResult:
    content: str
    model_name: str
    tokens_in: int
    tokens_out: int
    duration_ms: int
    llm_call_id: int


class GatewayError(Exception):
    def __init__(self, code: str, message: str | None = None, *, reasons: list[BlockReason] | None = None,
                 llm_call_id: int | None = None, detail: str | None = None):
        super().__init__(message or MESSAGES.get(code, code))
        self.code = code
        self.reasons = reasons or []
        self.llm_call_id = llm_call_id
        # 模型服务返回的出错原因（已去掉密钥）。只在设置页“测试连接”中给管理员看，不写日志（M8-05）
        self.detail = detail


@dataclass
class _Config:
    base_url: str
    api_key: str
    name: str
    temperature: float
    max_tokens: int
    timeout_s: float
    json_mode: bool
    extra_params: dict = field(default_factory=dict)
    monthly_call_cap: int = 0
    id: int | None = None
    chunk_chars: int = 60000
    price_in_per_m: Decimal = Decimal("0")
    price_out_per_m: Decimal = Decimal("0")


def load_model(db: Session, model_id: int) -> _Config:
    model = db.get(LlmModel, model_id)
    if model is None or not model.enabled:
        raise GatewayError("MODEL_UNAVAILABLE")
    return _Config(
        id=model.id, base_url=model.base_url.strip(), api_key=crypto.decrypt_text(model.api_key_enc),
        name=model.model_name.strip(), temperature=float(model.temperature), max_tokens=model.max_tokens,
        timeout_s=model.timeout_s, json_mode=model.json_mode, extra_params=model.extra_params,
        monthly_call_cap=model.monthly_call_cap, chunk_chars=model.chunk_chars,
        price_in_per_m=model.price_in_per_m, price_out_per_m=model.price_out_per_m,
    )


def format_reasons(reasons: list[BlockReason]) -> str:
    """拦截原因的文字形式：只有类别和位置，如 “MAP@120；PHONE@356”。"""
    return "；".join(f"{r.category}@{r.position}" for r in reasons)


# 拦截原因的中文说明（V31-U5F-02）：给用户看，只有类别和次数
REASON_LABELS = {
    "MAP": "已替换的内容在别处仍是原文", "TERM": "敏感词未替换", "AMOUNT": "金额未替换", "PHONE": "电话未替换",
    "EMAIL": "邮箱未替换", "ACCOUNT": "账号未替换", "IDNO": "证件号未替换", "SWIFT": "银行代码未替换",
    "URL": "网址未替换", "IM": "联系方式未替换", "POSTCODE": "邮编未替换", "REFNO": "编号未替换",
    "PERSON": "人名未替换", "ADDR": "地址未替换", "INVISIBLE": "含不可见字符",
}


def describe_reasons(reasons: list[BlockReason]) -> str:
    """如 “金额未替换 2 处、人名未替换 1 处”。"""
    counts: dict[str, int] = {}
    for r in reasons:
        label = REASON_LABELS.get(r.category, "未脱敏的信息")
        counts[label] = counts.get(label, 0) + 1
    return "、".join(f"{label} {n} 处" for label, n in counts.items())


ASSIST_TITLES = {"chat": "智能助手", "meeting": "会议纪要", "translate": "翻译"}
REVIEW_TITLE = "智能审核"


class _Recorder:
    """用独立会话写 llm_calls 并立即提交。"""

    def __init__(self, db: Session, api_key: str, base: dict):
        self.bind = db.get_bind()
        self.api_key = api_key
        self.base = base

    def _clean(self, text: str | None) -> str | None:
        if text is None or not self.api_key:
            return text
        return text.replace(self.api_key, "***")

    def write(self, **fields) -> int:
        for k in ("request_text", "response_text", "error_message"):
            if k in fields:
                fields[k] = self._clean(fields[k])
        with Session(bind=self.bind) as s:
            base = dict(self.base)
            if base.get("purpose") == "assist" and base.get("task_id") is not None:
                from sqlalchemy import select
                from app.db.models import Task
                # 与会话删除使用同一任务行锁：删除中或删除后落下的调用记录不能重新留下标题。
                task = s.scalar(select(Task).where(Task.id == base["task_id"]).with_for_update())
                # 会话标题取自用户第一句原话或会议主题，未脱敏；外发记录只写用途（V31Q-113）。
                base["task_title"] = ASSIST_TITLES.get(task.purpose, "智能助手") if task is not None else None
            elif base.get("task_id") is not None:
                # 审核任务的主文件名常含对方公司名，管理员只应看到脱敏后的外发内容：同样只写用途（V31Q-115）
                base["task_title"] = REVIEW_TITLE
            row = LlmCall(**base, **fields)
            s.add(row)
            s.commit()
            return row.id


def _build_body(cfg: _Config, messages: list[dict], json_mode: bool) -> dict:
    body: dict = {"model": cfg.name, "messages": messages, "temperature": cfg.temperature,
                  "max_tokens": cfg.max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for key, value in cfg.extra_params.items():
        if key != "messages":  # 不允许替换已通过出口复检的消息
            body[key] = value
    return body


def _parse_response(resp: httpx.Response) -> tuple[str, int, int]:
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("content")
    usage = data.get("usage") or {}
    return content, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def call_model(db: Session, *, model_id: int, purpose: str, messages: list[dict],
               user_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None,
               task_title: str | None = None, run_id: uuid.UUID | None = None,
               json_mode: bool | None = None, timeout_s: float | None = None,
               retry: bool = True, extra_task_ids=(), on_send=None, on_delta=None) -> ModelResult:
    """on_send：出口复检通过、即将发出时调用（审核中进度用，V31-U5F-01），不改变检查顺序。
    on_delta：给出时流式接收回复，以本次尝试累计收到的字数回调（审核中“已收到约 n 字”，V31-U5F-03）；
    上限检查、出口复检、记录与重试和非流式完全相同。"""
    return _call_config(db, cfg=load_model(db, model_id), purpose=purpose, messages=messages,
                        user_id=user_id, task_id=task_id, task_title=task_title, run_id=run_id,
                        json_mode=json_mode, timeout_s=timeout_s, retry=retry, extra_task_ids=extra_task_ids,
                        on_send=on_send, on_delta=on_delta)


def _refuse_local_only(db: Session, task_id: uuid.UUID | None) -> None:
    """外发策略为不外发的任务一律不调用模型，不论用途（V31Q-114：原先只在助手用途检查，审核与追问靠调用方）。"""
    from app.db.models import Task
    task = db.get(Task, task_id) if task_id is not None else None
    if task is not None and task.policy == "local_only":
        raise GatewayError("BLOCKED")


def precheck(db: Session, *, model_id: int, purpose: str, messages: list[dict], user_id: uuid.UUID | None = None,
             task_id: uuid.UUID | None = None, task_title: str | None = None, run_id: uuid.UUID | None = None) -> None:
    """只做出口复检、不发送：分段审核在发出任何一段之前先查全部分段，任一段命中即整轮不发（V31-U5F-01）。
    拦截与网关发送前的复检相同：记一条 blocked 的 llm_calls 并抛出 GatewayError("BLOCKED")。真正发送时网关仍会再复检。"""
    _refuse_local_only(db, task_id)
    cfg = load_model(db, model_id)
    ctx = recheck_context(db, task_id)
    reasons = recheck("\n".join(m["content"] for m in messages), real_values=ctx.real_values, terms=ctx.terms,
                      placeholders=ctx.placeholders, kept=ctx.kept)
    if reasons:
        recorder = _Recorder(db, cfg.api_key, {"user_id": user_id, "task_id": task_id, "task_title": task_title,
                                               "run_id": run_id, "purpose": purpose, "model_id": cfg.id,
                                               "model_name": cfg.name[:100]})
        call_id = recorder.write(status="blocked", request_text=None, block_reason=format_reasons(reasons))
        logger.warning("llm_call %s 出口复检拦截（发送前预检） purpose=%s 命中=%s", call_id, purpose, len(reasons))
        raise GatewayError("BLOCKED", reasons=reasons, llm_call_id=call_id)


def _call_config(
    db: Session,
    *,
    cfg: _Config,
    purpose: str,
    messages: list[dict],
    user_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    task_title: str | None = None,
    run_id: uuid.UUID | None = None,
    json_mode: bool | None = None,
    timeout_s: float | None = None,
    retry: bool = True,
    extra_task_ids=(),
    on_send=None,
    on_delta=None,
) -> ModelResult:
    """调用模型。成功返回 ModelResult；失败抛出 GatewayError（code 见 MESSAGES）。

    timeout_s 覆盖设置中的超时（追问固定 120 秒，SPEC 13）；retry=False 时失败不重试（追问由用户手动重试）。
    """
    if purpose not in ("review", "chat", "test", "assist"):
        raise ValueError("purpose")
    _refuse_local_only(db, task_id)
    if purpose == "assist":
        retry = False  # 比价由用户手动重试，不能因 JSON 对齐失败或网络错误重复计费。
    if not all(isinstance(m.get("content"), str) and isinstance(m.get("role"), str) for m in messages):
        raise ValueError("messages")
    if not (cfg.base_url and cfg.api_key and cfg.name):
        raise GatewayError("NOT_CONFIGURED")
    if timeout_s is not None:
        cfg.timeout_s = float(timeout_s)
    recorder = _Recorder(db, cfg.api_key, {"user_id": user_id, "task_id": task_id, "task_title": task_title,
                                           "run_id": run_id, "purpose": purpose, "model_id": cfg.id, "model_name": cfg.name[:100]})

    # 1. 调用上限：共用月度、个人每日的只读检查。
    reached = usage.cap_reached(db, user_id, purpose, cfg.id)
    if reached:
        raise GatewayError(reached)

    # 2. 出口复检
    ctx = recheck_context(db, task_id, extra_task_ids=extra_task_ids)
    reasons = recheck("\n".join(m["content"] for m in messages), real_values=ctx.real_values, terms=ctx.terms,
                      placeholders=ctx.placeholders, kept=ctx.kept)
    if reasons:
        call_id = recorder.write(status="blocked", request_text=None, block_reason=format_reasons(reasons))
        logger.warning("llm_call %s 出口复检拦截 purpose=%s 命中=%s", call_id, purpose, len(reasons))
        raise GatewayError("BLOCKED", reasons=reasons, llm_call_id=call_id)

    # 3. 发送，失败重试
    if on_send is not None:
        on_send()
    body = _build_body(cfg, messages, cfg.json_mode if json_mode is None else json_mode)
    if purpose == "assist":
        body["stream"] = False
        on_delta = None
        if json_mode is True:
            body["response_format"] = {"type": "json_object"}
    elif on_delta is not None:
        body["stream"], body["stream_options"] = True, {"include_usage": True}
    request_text = json.dumps(body, ensure_ascii=False)
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    last: GatewayError | None = None
    for attempt in range(len(RETRY_DELAYS) + 1 if retry else 1):
        if attempt:
            time.sleep(RETRY_DELAYS[attempt - 1])
        started = time.monotonic()
        outcome = _send_once(cfg, url, headers, request_text, on_delta)
        duration = int((time.monotonic() - started) * 1000)
        if isinstance(outcome, _Failure):
            kept = outcome.response_text[:_RESPONSE_KEEP_CHARS] if outcome.response_text else None
            err = outcome.error
            err.llm_call_id = recorder.write(status="error", request_text=request_text, response_text=kept,
                                             error_message=str(err), duration_ms=duration)
            logger.error("llm_call %s 失败 purpose=%s 错误=%s 第 %s 次", err.llm_call_id, purpose, err.code,
                         attempt + 1)
            last = err
            if not outcome.retry:
                break
            continue
        content, tokens_in, tokens_out = outcome
        call_id = recorder.write(status="sent", request_text=request_text, response_text=content,
                                 tokens_in=tokens_in, tokens_out=tokens_out, duration_ms=duration)
        logger.info("llm_call %s 已发送 purpose=%s 耗时=%sms", call_id, purpose, duration)
        return ModelResult(content, cfg.name, tokens_in, tokens_out, duration, call_id)
    assert last is not None
    raise last


@dataclass
class _Failure:
    error: GatewayError
    retry: bool
    response_text: str | None = None


def _send_once(cfg: _Config, url: str, headers: dict, request_text: str,
               on_delta=None) -> tuple[str, int, int] | _Failure:
    """发送一次。4xx（408、429 等除外）不重试：密钥错误、参数错误重试也不会成功。
    on_delta 不为空时请求体已带 stream=true，改为流式接收，每收到一段回复就以本次累计字数回调（V31-U5F-03）。"""
    if on_delta is not None:
        return _send_stream(cfg, url, headers, request_text, on_delta)
    try:
        with httpx.Client(timeout=cfg.timeout_s, transport=TRANSPORT, trust_env=False,
                          follow_redirects=False) as client:
            resp = client.post(url, content=request_text.encode("utf-8"), headers=headers)
    except httpx.TimeoutException:
        return _Failure(GatewayError("TIMEOUT"), True)
    except httpx.HTTPError as exc:
        return _Failure(GatewayError("HTTP_ERROR", f"{MESSAGES['HTTP_ERROR']}（{type(exc).__name__}）"), True)
    if resp.status_code != 200:
        retry = resp.status_code >= 500 or resp.status_code in _RETRYABLE_STATUS
        return _Failure(GatewayError("HTTP_ERROR", f"{MESSAGES['HTTP_ERROR']}（HTTP {resp.status_code}）",
                                     detail=_error_detail(resp.text, cfg.api_key)), retry, resp.text)
    try:
        return _parse_response(resp)
    except (ValueError, KeyError, IndexError, TypeError):
        return _Failure(GatewayError("BAD_RESPONSE"), True, resp.text)


def _send_stream(cfg: _Config, url: str, headers: dict, request_text: str,
                 on_delta) -> tuple[str, int, int] | _Failure:
    """流式发送一次（审核用）：失败分类与 _send_once 相同。服务端忽略 stream、直接返回整段 JSON（application/json）时按普通回复解析。
    两段数据之间超过 cfg.timeout_s 或总时长超过 max(STREAM_TOTAL_SECONDS, cfg.timeout_s) 记为超时。"""
    parts: list[str] = []
    tokens_in = tokens_out = 0
    received = 0
    started = time.monotonic()

    def notify(n: int) -> None:  # 进度回调失败不能影响发送与记录
        try:
            on_delta(n)
        except Exception:
            logger.warning("流式进度回调失败（已忽略）")

    total_s = max(STREAM_TOTAL_SECONDS, cfg.timeout_s)
    try:
        with httpx.Client(timeout=cfg.timeout_s, transport=TRANSPORT, trust_env=False,
                          follow_redirects=False) as client:
            with client.stream("POST", url, content=request_text.encode("utf-8"), headers=headers) as resp:
                if resp.status_code != 200:
                    resp.read()
                    retry = resp.status_code >= 500 or resp.status_code in _RETRYABLE_STATUS
                    return _Failure(GatewayError("HTTP_ERROR", f"{MESSAGES['HTTP_ERROR']}（HTTP {resp.status_code}）",
                                                 detail=_error_detail(resp.text, cfg.api_key)), retry, resp.text)
                if "application/json" in resp.headers.get("content-type", ""):
                    resp.read()
                    try:
                        content, tokens_in, tokens_out = _parse_response(resp)
                    except (ValueError, KeyError, IndexError, TypeError):
                        return _Failure(GatewayError("BAD_RESPONSE"), True, resp.text)
                    notify(len(content))
                    return content, tokens_in, tokens_out
                complete = False
                pending: list[str] = []
                lines = resp.iter_lines()
                for line in lines:
                    if time.monotonic() - started > total_s:
                        return _Failure(GatewayError("TIMEOUT"), True, "".join(parts) or None)
                    if line.startswith("data:"):
                        pending.append(line[5:].lstrip(" "))
                        continue
                    if line or not pending:
                        continue
                    data, pending = "\n".join(pending), []
                    if data == "[DONE]":
                        complete = True
                        break
                    payload = json.loads(data)
                    if not isinstance(payload, dict) or "error" in payload:
                        raise ValueError("event")
                    use = payload.get("usage") or {}
                    tokens_in = int(use.get("prompt_tokens") or tokens_in)
                    tokens_out = int(use.get("completion_tokens") or tokens_out)
                    for choice in payload.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            if not isinstance(piece, str):
                                raise ValueError("content")
                            parts.append(piece)
                            received += len(piece)
                            notify(received)
                        # 推理模型先流式输出思考内容（测试机实测约 2 分钟）：只计字数让用户看到在出字，不保存、不记录
                        thinking = delta.get("reasoning_content")
                        if isinstance(thinking, str) and thinking:
                            received += len(thinking)
                            notify(received)
                        if choice.get("finish_reason") is not None:
                            complete = True  # 继续读到 [DONE]，用量常在最后一个事件中
                if not complete:
                    return _Failure(GatewayError("BAD_RESPONSE"), True, "".join(parts) or None)
    except httpx.TimeoutException:
        return _Failure(GatewayError("TIMEOUT"), True, "".join(parts) or None)
    except httpx.HTTPError as exc:
        return _Failure(GatewayError("HTTP_ERROR", f"{MESSAGES['HTTP_ERROR']}（{type(exc).__name__}）"), True,
                        "".join(parts) or None)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return _Failure(GatewayError("BAD_RESPONSE"), True, "".join(parts) or None)
    return "".join(parts), tokens_in, tokens_out


_DETAIL_MAX = 300
_KEY_LIKE = re.compile(r"\b(?:sk|key|token)-[A-Za-z0-9_\-]{6,}", re.IGNORECASE)


def _error_detail(text: str | None, api_key: str) -> str | None:
    """取 OpenAI 兼容格式的 error.message（取不到时用返回内容开头），去掉密钥与类似密钥的串、合并空白，截断。"""
    if not text:
        return None
    try:
        data = json.loads(text)
        err = data.get("error") if isinstance(data, dict) else None
        msg = err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else None
    except ValueError:
        msg = None
    msg = msg if isinstance(msg, str) and msg.strip() else text
    if api_key:
        msg = msg.replace(api_key, "***")
    msg = _KEY_LIKE.sub("***", " ".join(msg.split()))
    return msg[:_DETAIL_MAX] or None


STRUCTURED_MESSAGE = '请只输出以下 JSON，不要添加其他文字：{"ok": true, "items": [{"n": 1}, {"n": 2}]}'


def test_config(db: Session, cfg: _Config, user_id: uuid.UUID | None, *, structured: bool) -> ModelResult:
    """固定测试消息同样经出口复检与记录；不受业务调用上限限制。"""
    result = _call_config(db, cfg=cfg, purpose="test", user_id=user_id, retry=False,
                          messages=[{"role": "user", "content": STRUCTURED_MESSAGE if structured else TEST_MESSAGE}],
                          json_mode=cfg.json_mode if structured else False)
    if structured:
        content = result.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*\n?", "", content, count=1, flags=re.IGNORECASE)
            content = re.sub(r"\s*```$", "", content)
        try:
            data = json.loads(content)
            valid = (isinstance(data, dict) and set(data) == {"ok", "items"} and data["ok"] is True
                     and isinstance(data["items"], list) and len(data["items"]) == 2
                     and all(isinstance(item, dict) and set(item) == {"n"} and type(item["n"]) is int
                             and item["n"] == n for item, n in zip(data["items"], (1, 2))))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise GatewayError("BAD_RESPONSE", llm_call_id=result.llm_call_id)
    return result


test_config.__test__ = False


def stream_model(db: Session, *, model_id: int, purpose: str, messages: list[dict], on_delta,
                 user_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None,
                 task_title: str | None = None, extra_task_ids=(), json_mode: bool = False) -> ModelResult:
    """助手 SSE 发送唯一入口：上限、整包复检、单次发送、独立记录；流式失败不重试。"""
    from app.db.models import Task
    if purpose != "assist":
        raise ValueError("purpose")
    if not messages or not all(isinstance(m.get("content"), str) and m.get("role") in ("system", "user", "assistant") for m in messages):
        raise ValueError("messages")
    task = db.get(Task, task_id) if task_id is not None else None
    if task is not None and task.policy == "local_only":
        raise GatewayError("BLOCKED")
    cfg = load_model(db, model_id)
    if not (cfg.base_url and cfg.api_key and cfg.name):
        raise GatewayError("NOT_CONFIGURED")
    reached = usage.cap_reached(db, user_id, purpose, cfg.id)
    if reached:
        raise GatewayError(reached)
    recorder = _Recorder(db, cfg.api_key, {"user_id": user_id, "task_id": task_id, "task_title": task_title,
                                          "purpose": purpose, "model_id": cfg.id, "model_name": cfg.name[:100]})
    ctx = recheck_context(db, task_id, extra_task_ids=extra_task_ids)
    reasons = recheck("\n".join(m["content"] for m in messages), real_values=ctx.real_values,
                      terms=ctx.terms, placeholders=ctx.placeholders, kept=ctx.kept)
    if reasons:
        cid = recorder.write(status="blocked", request_text=None, block_reason=format_reasons(reasons))
        logger.warning("llm_call %s 出口复检拦截 purpose=assist 命中=%s", cid, len(reasons))
        raise GatewayError("BLOCKED", reasons=reasons, llm_call_id=cid)
    body = _build_body(cfg, messages, json_mode)
    body["stream"], body["stream_options"] = True, {"include_usage": True}
    request_text = json.dumps(body, ensure_ascii=False)
    started = last_received = time.monotonic()
    parts, tokens_in, tokens_out = [], 0, 0
    complete, stopped, pending = False, False, []

    def event(data):
        nonlocal complete, stopped, tokens_in, tokens_out
        if data == "[DONE]":
            complete = True
            return False
        payload = json.loads(data)
        if not isinstance(payload, dict) or "error" in payload:
            raise ValueError("event")
        usage_data = payload.get("usage") or {}
        tokens_in = int(usage_data.get("prompt_tokens") or tokens_in)
        tokens_out = int(usage_data.get("completion_tokens") or tokens_out)
        choices = payload.get("choices")
        if not isinstance(choices, list):
            raise ValueError("choices")
        if choices:
            choice = choices[0]
            content = (choice.get("delta") or {}).get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ValueError("content")
                parts.append(content)
                if on_delta(content) is False:
                    stopped = True
                    return False
            if choice.get("finish_reason") is not None:
                complete = True
        return True

    error = None
    deadline_hit = threading.Event()
    deadline_timer = None
    try:
        with httpx.Client(timeout=min(cfg.timeout_s, STREAM_TOTAL_SECONDS), transport=TRANSPORT, trust_env=False,
                          follow_redirects=False) as client:
            with client.stream("POST", cfg.base_url.rstrip("/") + "/chat/completions",
                               headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
                               content=request_text.encode("utf-8")) as response:
                if response.status_code != 200:
                    raise GatewayError("HTTP_ERROR")

                def expire():
                    deadline_hit.set()
                    try:
                        response.close()
                    except Exception:
                        pass  # 只用于唤醒阻塞读取，不记录响应或异常原文。

                deadline_timer = threading.Timer(max(0, STREAM_TOTAL_SECONDS - (time.monotonic() - started)), expire)
                deadline_timer.daemon = True
                deadline_timer.start()
                for line in response.iter_lines():
                    now = time.monotonic()
                    if deadline_hit.is_set() or now - started >= STREAM_TOTAL_SECONDS or now - last_received > cfg.timeout_s:
                        raise GatewayError("TIMEOUT")
                    last_received = now
                    if line.startswith("data:"):
                        pending.append(line[5:].lstrip(" "))
                    elif not line and pending:
                        data, pending = "\n".join(pending), []
                        if not event(data):
                            break
                if pending and not stopped:
                    event("\n".join(pending))
                if not (complete or stopped):
                    raise GatewayError("BAD_RESPONSE")
    except httpx.TimeoutException:
        error = GatewayError("TIMEOUT")
    except httpx.HTTPError:
        error = GatewayError("HTTP_ERROR")
    except GatewayError as exc:
        error = exc
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        error = GatewayError("BAD_RESPONSE")
    except Exception:
        # 消费方写库失败也必须留下本次已开始的外发记录，再交 worker 统一收尾。
        error = GatewayError("HTTP_ERROR")
    finally:
        if deadline_timer is not None:
            deadline_timer.cancel()
    if deadline_hit.is_set():
        error = GatewayError("TIMEOUT")
    duration = int((time.monotonic() - started) * 1000)
    content = "".join(parts)
    cid = recorder.write(status="error" if error else "sent", request_text=request_text,
                         response_text=content[:_RESPONSE_KEEP_CHARS] if error else content,
                         tokens_in=tokens_in, tokens_out=tokens_out, duration_ms=duration,
                         **({"error_message": str(error)} if error else {}))
    logger.info("llm_call %s 流式结束 purpose=assist 状态=%s 耗时=%sms", cid, "error" if error else "sent", duration)
    if error:
        error.llm_call_id = cid
        raise error
    return ModelResult(content, cfg.name, tokens_in, tokens_out, duration, cid)
