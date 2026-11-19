"""模型补充要求清单（bid.md 2.2 第 2 点）：只做抽取，不做判断；“只做本地检查”的项目从不进入。

流程：用户点“让模型补充” → 本机对招标文件整份脱敏（redacting）→ 用户在 AI 视野核对后确认（awaiting）
→ 分段经模型网关发送（extracting，purpose bid）→ 结果按引文校验后并入清单（done / failed）。
只发脱敏后的招标文件正文；投标文件不在此流程中。
"""
import json
import logging
import re
import uuid

from sqlalchemy import select

from app.bid import requirements as R
from app.bid import service
from app.db import jobs
from app.db.models import BidProject, BidRequirement, DocNode, Task, TaskFile
from app.llm import gateway
from app.llm.usage import remaining_calls
from app.redaction import engine
from app.redaction.restore import restore
from app.security import crypto
from app.security.errors import AppError
from app.worker import job_handler

logger = logging.getLogger(__name__)
CHUNK = 24000  # 每次调用的正文字数上限（脱敏后）
_PREVIEW_VERSION = "@bid_extract_neutral_version"
STALE_PREVIEW = "招标文件脱敏预览已失效，请重新开始模型补充"

SYSTEM = """你是招标文件整理助手。用户给出一份招标文件的若干行（已在本机脱敏，方括号中的代号请原样保留），每行以 [位置] 开头。
请只做抽取与分类，不做任何判断：列出投标人必须满足或响应的要求。分组只能取：
qualification 资格条件；substantive 实质性条款（★ 或要求实质性响应的条款）；veto 否决条款（不满足即否决投标、废标或无效投标）；
pricing 报价要求；validity 投标有效期与保证金；signing 签章与格式页要求。
不要列出合同履行、付款、验收等中标后的条款，不要列出“投标文件格式”章节中的范本文字。
同一要求在多份文件、多张数据表中重复出现时只列一次；多个设备位号的要求内容相同时合并为一条，在 text 中列出这些位号。
只说明设备名称、型号、位号、数量或术语定义的文字不是要求，不要列出。
若某行改动了前文的要求（如答疑、补遗、澄清），在 replaces 中给出被改动要求所在行的位置。
只输出 JSON：{"items":[{"group":"分组","text":"一句话写清要求","loc":"位置","quote":"该行中逐字摘录的原文片段","replaces":"被改动要求的位置或空"}]}"""


def _project(db, task):
    return db.get(BidProject, task.id)


def _lines(db, task):
    rows = db.execute(select(DocNode.loc, DocNode.redacted_text).join(TaskFile, TaskFile.id == DocNode.file_id)
                      .where(DocNode.task_id == task.id, DocNode.part == "body", TaskFile.bidder_id.is_(None),
                             TaskFile.parser_version.is_not(None))
                      .order_by(TaskFile.seq, DocNode.ord)).all()
    return [(loc, text) for loc, text in rows]


def chunks(lines):
    out, current, size = [], [], 0
    for loc, text in lines:
        line = f"[{loc}] {text}"
        if current and size + len(line) > CHUNK:
            out.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        out.append(current)
    return out


def _preview_lines(db, task):
    if engine._load_full_map(db, task.id).get(_PREVIEW_VERSION) != str(task.material_version):
        return []
    lines = _lines(db, task)
    return [] if any(text is None for _, text in lines) else lines


def receipt(db, task) -> dict | None:
    """待确认时的回执：将发送的字数、分段（= 调用次数）、本机保护的项数、剩余次数。"""
    project = _project(db, task)
    if task.policy == "local_only" or project.extract_state != "awaiting":
        return None
    lines = _preview_lines(db, task)
    if not lines:
        return None
    parts = chunks(lines)
    from app.models_api import select_model
    try:
        model = select_model(db, None, "review", structured=True)
        left = remaining_calls(db, task.owner_id, model.id)
    except AppError:
        left = {"monthly": None, "daily": None}
    return {"chars": sum(len(t or "") for _, t in lines), "calls": len(parts),
            "protected": len(engine.load_redaction_map(db, task.id)), "remaining": left}


def require_idle(project):
    if project.extract_state == "redacting":
        raise AppError(409, "EXTRACT_BUSY", "招标文件正在脱敏，请完成后再操作")
    if project.extract_state == "extracting":
        raise AppError(409, "EXTRACT_BUSY", "模型正在补充要求，请完成后再操作")


def start(db, task):
    """开始本机脱敏；不提交。"""
    if task.policy == "local_only":
        raise AppError(409, "LOCAL_ONLY", "本项目只做本地检查，不调用模型")
    if not R.parsed_tender_ids(db, task.id):
        raise AppError(422, "NO_TENDER", "请先加入并解析招标文件")
    project = _project(db, task)
    require_idle(project)
    project.extract_state, project.extract_error = "redacting", None
    jobs.enqueue(db, "bid", {"task_id": str(task.id), "stage": "redact"})


def confirm(db, task):
    project = _project(db, task)
    if task.policy == "local_only":
        raise AppError(409, "LOCAL_ONLY", "本项目只做本地检查，不调用模型")
    if project.extract_state != "awaiting":
        raise AppError(409, "EXTRACT_STATE", "请先完成本机脱敏")
    if not _preview_lines(db, task):
        raise AppError(409, "EXTRACT_STALE", STALE_PREVIEW)
    project.extract_state = "extracting"
    jobs.enqueue(db, "bid", {"task_id": str(task.id), "stage": "extract"})


def cancel(db, task):
    project = _project(db, task)
    if project.extract_state in ("awaiting", "failed", "done"):
        project.extract_state, project.extract_error = None, None


def _locate(original, quote_raw, mapping):
    """引文先还原再在原文中找；找不到时返回 None（宁可记整行，也不编造位置）。"""
    quote = restore(quote_raw or "", mapping).strip()
    if len(quote) >= 4:
        i = original.find(quote)
        if i >= 0:
            return i, i + len(quote)
        compact = re.sub(r"\s+", "", quote)
        flat = re.sub(r"\s+", "", original)
        if compact and compact in flat:
            return 0, len(original)
    return None


def _grams(text):
    t = re.sub(r"[\W_]+", "", R._ENUM_HEAD.sub("", text.strip()))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _same_requirement(text, rows, threshold=0.85):
    """已有启用行中文字近似相同（二元组 Dice ≥ 0.85）的一行；没有返回 None。"""
    mine, numbers = _grams(text), set(re.findall(r"\d+(?:\.\d+)?", text))
    if len(mine) < 4:
        return None
    for r in rows:
        if not r.enabled:
            continue
        other_text = crypto.decrypt_text(r.text_enc)
        other = _grams(other_text)
        # 数值不同（70%～120% 与 80%～120%、不同位号）不是同一要求
        if other and set(re.findall(r"\d+(?:\.\d+)?", other_text)) == numbers                 and 2 * len(mine & other) / (len(mine) + len(other)) >= threshold:
            return r
    return None


# 技术条目里表示要求的用语；一条都没有的是工况、物性、环境数据一类说明（“操作介质为……”“海拔为……”），不列（V34-B-08c）
_DEMAND = re.compile(r"须|需|应|必须|不得|宜|要求|满足|符合|采用|选用|使用|执行|包括|包含|提供|注明|标明|明确|具备|具有|适用|"
                     r"不少于|不低于|不高于|不大于|不小于|不超过|保证|负责|带|配")


def _is_description(group, text):
    return group == "substantive" and not _DEMAND.search(text)


def merge(db, task, items: list[dict]) -> int:
    """校验并并入清单，返回新增行数。位置不在招标文件中、引文对不上的条目丢弃。"""
    mapping = engine.load_redaction_map(db, task.id)
    nodes = {n.loc: n for n in R.tender_nodes(db, task.id)}
    rows = list(db.scalars(select(BidRequirement).where(BidRequirement.task_id == task.id)))
    seq, added = R.next_seq(db, task.id), 0
    for item in items:
        if not isinstance(item, dict):
            continue
        group, text, loc = item.get("group"), str(item.get("text") or "").strip(), str(item.get("loc") or "").strip("[] ")
        if group not in R.GROUPS or group == "format" or not text or loc not in nodes:
            continue
        node = nodes[loc]
        span = _locate(node.text, str(item.get("quote") or ""), mapping)
        if span is None:
            continue
        start, end = span
        if any(any(l.get("loc") == loc and l.get("start", 0) < end and l.get("end", 0) > start for l in r.locs) for r in rows):
            continue  # 规则已抓到的同一处，不重复
        restored = restore(text, mapping)
        if _is_description(group, restored):
            continue
        same = _same_requirement(restored, rows)
        if same is not None:
            # 文字近似相同（多份数据表的同一注释）：只并入出处，不新增一行（V34-B-08c）
            here = {"file_id": node.file_id, "loc": loc, "start": start, "end": end}
            if not any(l.get("loc") == loc for l in same.locs):
                same.locs = [*same.locs, here]
            continue
        replaced = str(item.get("replaces") or "").strip("[] ")
        if replaced and replaced != loc:
            for r in rows:
                if r.enabled and r.source != "manual" and any(l.get("loc") == replaced for l in r.locs):
                    r.enabled = False  # 被后发文件改动的旧要求停用（保留可查）
        row = BidRequirement(task_id=task.id, group=group, text_enc=crypto.encrypt_text(restored), redacted_text=text,
                             locs=[{"file_id": node.file_id, "loc": loc, "start": start, "end": end}],
                             method=R.default_method(group, restored, task.policy), enabled=True, source="model", seq=seq)
        db.add(row)
        rows.append(row)
        seq += 1
        added += 1
    return added


def _parse(content):
    data = json.loads(content)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError
    return items


def _fail(db, task_id, message):
    db.rollback()
    project = db.get(BidProject, task_id, populate_existing=True, with_for_update=True)
    if project is not None and project.extract_state in ("redacting", "extracting"):
        project.extract_state, project.extract_error = "failed", message[:200]
        db.commit()


def _local_check(db, task, project):
    """本地检查一（B-03）：失败时退回“待开标”，可再次开始。"""
    from app.bid import checks, results
    if project.check_state != "running":
        return
    if service.originals_expired(db, task.id):
        # 已排队的旧作业也不能覆盖存量结果。有结果的项目保持可导出。
        if project.check_enc is not None:
            project.stage, project.check_state = "checked", "done"
        else:
            project.stage, project.check_state = "ready", "failed"
        project.check_error = service.ORIGINAL_EXPIRED_MSG
        db.commit()
        return
    try:
        results.clear_model_results(db, project)
        checks.run(db, task)
        project.check_state, project.check_error, project.stage = "done", None, "checked"
        db.commit()
    except Exception:
        logger.warning("标书本地检查失败 task=%s", task.id)
        db.rollback()
        project = db.get(BidProject, task.id, populate_existing=True)
        if project is not None:
            project.check_state, project.check_error, project.stage = "failed", "本地检查中断，请重新开始初审", "ready"
            db.commit()
        return
    # 外发策略允许时自动准备片段；入队失败时保留已完成的本地检查，仍可手动准备。
    from app.bid import model
    try:
        model.start(db, task, auto=True)
        db.commit()
    except Exception:
        logger.warning("标书模型核对片段自动入队失败 task=%s", task.id)
        db.rollback()


@job_handler("bid")
def handle_bid(db, payload):
    task_id = uuid.UUID(str(payload["task_id"]))
    stage = payload.get("stage")
    model_work = stage in service.MODEL_JOB_STAGES
    task = service.lock_task(db, task_id) if model_work else db.get(Task, task_id)
    project = db.get(BidProject, task_id, populate_existing=True) if task is not None else None
    if task is None or project is None or task.kind != "bid":
        return
    if model_work:
        if task.policy == "local_only":
            service.clear_model_work(db, project)
            db.commit()
            return
        # 进行中状态阻止切换策略；耗时处理和模型请求前释放任务行锁。
        db.commit()
    if stage == "check":
        _local_check(db, task, project)
        return
    if stage in ("snippets", "model"):
        from app.bid import model
        model.handle(db, task, project, stage)
        return
    if stage == "ocr":
        from app.bid.pipeline import ocr_project
        ocr_project(db, task_id)
        return
    if stage == "redact":
        if project.extract_state != "redacting":
            return
        try:
            from app.bid.model import invitee_terms
            engine.redact_task(db, task, style="neutral", extra_terms=invitee_terms(db, task))
            # 标记本次中性脱敏的材料版本，旧回执需重新准备再确认。
            mapping = engine._load_full_map(db, task.id)
            mapping.pop("@date_shift_days", None)
            mapping[_PREVIEW_VERSION] = str(task.material_version)
            engine.write_full_map(db, task.id, mapping)
            project.extract_state = "awaiting"
            db.commit()
        except Exception:
            logger.warning("标书招标文件脱敏失败 task=%s", task_id)
            _fail(db, task_id, "本机脱敏失败，请重试")
        return
    if stage != "extract" or project.extract_state != "extracting":
        return
    lines = _preview_lines(db, task)
    if not lines:
        _fail(db, task_id, STALE_PREVIEW)
        return
    from app.models_api import select_model
    try:
        model = select_model(db, None, "review", structured=True)
    except AppError as exc:
        _fail(db, task_id, str(exc))
        return
    items: list = []
    parts = chunks(lines)
    db.commit()
    for number, part in enumerate(parts, 1):
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"第 {number} / {len(parts)} 段：\n" + "\n".join(part)}]
        try:
            outcome = gateway.call_model(db, model_id=model.id, purpose="bid", messages=messages, user_id=task.owner_id,
                                         task_id=task.id, task_title=task.title, json_mode=True)
            items += _parse(outcome.content)
        except gateway.GatewayError as exc:
            _fail(db, task_id, str(exc))
            return
        except (ValueError, TypeError, AttributeError):
            _fail(db, task_id, "模型返回的内容无法识别，请重试")
            return
    # 与清单编辑共用任务行锁，避免合并和删除要求时以相反顺序更新项目与要求。
    task = service.lock_task(db, task_id)
    project = db.get(BidProject, task_id, populate_existing=True, with_for_update=True) if task is not None else None
    if project is None or project.extract_state != "extracting":
        db.rollback()
        return
    added = merge(db, task, items)
    project.extract_state, project.extract_error = "done", None
    project.requirements_version += 1
    db.commit()
    logger.info("标书要求清单模型补充完成 task=%s 新增=%s", task_id, added)
