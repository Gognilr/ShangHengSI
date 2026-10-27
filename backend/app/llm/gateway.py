"""模型网关 —— 全系统唯一可以访问外网的模块（SPEC 5、11.1、11.7、17.4，设计 docs/design/M2.md 第七节）。

call_model() 依次执行：读取设置 → 每月调用上限检查 → 出口复检 → 发送（失败重试 2 次；追问不重试）→ 每次尝试写一条 llm_calls。
- 出口复检对全部 messages 的 content 拼接后检查（9.8）；拦截时写一条 status=blocked、request_text 为空、
  block_reason 只含类别和位置的记录，然后抛出 GatewayError("BLOCKED")，不发送。
- 请求体：model、messages、temperature、max_tokens，json_mode 开启时加 response_format，再合并附加请求参数。
  附加请求参数不能覆盖 messages（否则可绕过出口复检），该键被忽略。
- llm_calls 用独立的数据库会话写入并立即提交：调用方回滚自己的事务时，外发记录仍然保留。
- API 密钥只出现在请求头中；写入 llm_calls 的文字中若出现密钥原文一律替换为 ***；日志只写记录 id、用途、状态、耗时。
"""
import json
import logging
import re
import time
import threading
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.admin.settings_store import get_settings_map
from app.db.models import LlmCall
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


def _load_config(db: Session) -> _Config:
    s = get_settings_map(db, "model.")
    api_key = crypto.decrypt_from_b64(s["api_key_enc"]) if s["api_key_enc"] else ""
    extra = s["extra_params"] if isinstance(s["extra_params"], dict) else {}
    return _Config(
        base_url=str(s["base_url"]).strip(), api_key=api_key, name=str(s["name"]).strip(),
        temperature=float(s["temperature"]), max_tokens=int(s["max_tokens"]), timeout_s=float(s["timeout_s"]),
        json_mode=bool(s["json_mode"]), extra_params=extra, monthly_call_cap=int(s["monthly_call_cap"] or 0),
    )


def format_reasons(reasons: list[BlockReason]) -> str:
    """拦截原因的文字形式：只有类别和位置，如 “MAP@120；PHONE@356”。"""
    return "；".join(f"{r.category}@{r.position}" for r in reasons)


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
                base["task_title"] = task.title if task is not None else None
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


def call_model(
    db: Session,
    *,
    purpose: str,
    messages: list[dict],
    user_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    task_title: str | None = None,
    run_id: uuid.UUID | None = None,
    json_mode: bool | None = None,
    timeout_s: float | None = None,
    retry: bool = True,
) -> ModelResult:
    """调用模型。成功返回 ModelResult；失败抛出 GatewayError（code 见 MESSAGES）。

    timeout_s 覆盖设置中的超时（追问固定 120 秒，SPEC 13）；retry=False 时失败不重试（追问由用户手动重试）。
    """
    if purpose not in ("review", "chat", "test"):
        raise ValueError("purpose")
    if not all(isinstance(m.get("content"), str) and isinstance(m.get("role"), str) for m in messages):
        raise ValueError("messages")
    cfg = _load_config(db)
    if not (cfg.base_url and cfg.api_key and cfg.name):
        raise GatewayError("NOT_CONFIGURED")
    if timeout_s is not None:
        cfg.timeout_s = float(timeout_s)
    recorder = _Recorder(db, cfg.api_key, {"user_id": user_id, "task_id": task_id, "task_title": task_title,
                                           "run_id": run_id, "purpose": purpose, "model_name": cfg.name[:100]})

    # 1. 调用上限：共用月度、个人每日的只读检查。
    reached = usage.cap_reached(db, user_id, purpose)
    if reached:
        raise GatewayError(reached)

    # 2. 出口复检
    ctx = recheck_context(db, task_id)
    reasons = recheck("\n".join(m["content"] for m in messages), real_values=ctx.real_values, terms=ctx.terms,
                      placeholders=ctx.placeholders)
    if reasons:
        call_id = recorder.write(status="blocked", request_text=None, block_reason=format_reasons(reasons))
        logger.warning("llm_call %s 出口复检拦截 purpose=%s 命中=%s", call_id, purpose, len(reasons))
        raise GatewayError("BLOCKED", reasons=reasons, llm_call_id=call_id)

    # 3. 发送，失败重试
    body = _build_body(cfg, messages, cfg.json_mode if json_mode is None else json_mode)
    request_text = json.dumps(body, ensure_ascii=False)
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    last: GatewayError | None = None
    for attempt in range(len(RETRY_DELAYS) + 1 if retry else 1):
        if attempt:
            time.sleep(RETRY_DELAYS[attempt - 1])
        started = time.monotonic()
        outcome = _send_once(cfg, url, headers, request_text)
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


def _send_once(cfg: _Config, url: str, headers: dict, request_text: str) -> tuple[str, int, int] | _Failure:
    """发送一次。4xx（408、429 等除外）不重试：密钥错误、参数错误重试也不会成功。"""
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


def test_connection(db: Session, user_id: uuid.UUID | None) -> ModelResult:
    """设置页“测试连接”：发送不含任何业务数据的固定测试消息（purpose=test），同样经过上限检查和出口复检。"""
    return call_model(db, purpose="test", messages=[{"role": "user", "content": TEST_MESSAGE}], user_id=user_id,
                      json_mode=False)


test_connection.__test__ = False  # 不让 pytest 当成测试函数收集


def stream_model(db: Session, *, purpose: str, messages: list[dict], on_delta,
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
    cfg = _load_config(db)
    if not (cfg.base_url and cfg.api_key and cfg.name):
        raise GatewayError("NOT_CONFIGURED")
    reached = usage.cap_reached(db, user_id, purpose)
    if reached:
        raise GatewayError(reached)
    recorder = _Recorder(db, cfg.api_key, {"user_id": user_id, "task_id": task_id, "task_title": task_title,
                                          "purpose": purpose, "model_name": cfg.name[:100]})
    ctx = recheck_context(db, task_id, extra_task_ids=extra_task_ids)
    reasons = recheck("\n".join(m["content"] for m in messages), real_values=ctx.real_values,
                      terms=ctx.terms, placeholders=ctx.placeholders)
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
