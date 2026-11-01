"""开发期虚构 DOCX 样本评测；复用业务链路，所有模型调用仍经过网关。"""
import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from unittest.mock import patch
import hashlib
import html
import json
import os
from pathlib import Path
import sys

# 同时支持 python tools/eval_review.py 和 python -m tools.eval_review。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import jobs
from app.db.base import utcnow
from app.db.models import Finding, Job, LlmCall, ReviewRun, User
from app.db.session import get_engine
from app.review.job import run_review
from app.review.service import create_run, run_detail
from app.security.errors import AppError
from app.tasks.pipeline import parse_task
from app.tasks.service import check_uploads, create_task


COPY_RULE = ("代号必须原样照抄，包括方括号和下划线，不得译成英文、拆开、加空格，不得自行编号或写出【主体说明】和正文中没有的代号；"
             "suggested_text 中同样照抄。代号后括号内的比例只用于理解，不得写入 suggested_text。"
             "行首方括号内的位置编号只写入 locs，不得写入其他字段。")


@contextmanager
def probe_options(*, copy_rule=False, bracket_codes=False):
    """只在评测进程内替换绑定；退出（含异常）即恢复，不写产品文件。"""
    from app.redaction import outbound, placeholders
    from app.review import prompt

    original = prompt.load_system_template
    def template():
        version, body = original()
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("2."):
                if bracket_codes:
                    for code in ("我方", "客户_A", "人员_1"):
                        line = line.replace(f"“{code}”", f"“[{code}]”")
                if copy_rule:
                    line += COPY_RULE
                lines[i] = line
        return version, "\n".join(lines)

    with ExitStack() as stack:
        if copy_rule or bracket_codes:
            stack.enter_context(patch.object(prompt, "load_system_template", template))
        if bracket_codes:
            formats = {k: f"[{v}]" if k in ("PERSON", "PROJECT", "REFNO") else v
                       for k, v in placeholders._NUMBERED.items()}
            org = placeholders.Allocator.org
            def bracket_org(self, key, role):
                code = org(self, key, role)
                if not code.startswith("["):
                    self._codes[("ORG", key)] = f"[{code}]"
                return self._codes[("ORG", key)]
            stack.enter_context(patch.object(placeholders, "_NUMBERED", formats))
            stack.enter_context(patch.object(placeholders, "OUR_CODE", "[我方]"))
            stack.enter_context(patch.object(outbound, "OUR_CODE", "[我方]"))
            stack.enter_context(patch.object(placeholders.Allocator, "org", bracket_org))
            stack.enter_context(patch.object(placeholders.Allocator, "our", lambda self: "[我方]"))
        yield


def return_metrics(db, run):
    """检查库内原始代号字段，绝不使用 run_detail 已还原的文字统计。"""
    from app.redaction.engine import load_redaction_map
    from app.redaction.restore import check
    mapping = load_redaction_map(db, run.task_id)
    findings = list(db.scalars(select(Finding).where(Finding.run_id == run.id)))
    metrics = {"clause": Counter(), "other": Counter()}
    examples = []
    fields = [("other", run.doc_summary)]
    for finding in findings:
        fields += [("clause" if key == "suggested_text" else "other", getattr(finding, key))
                   for key in ("title", "quote_model", "problem", "impact", "suggestion", "suggested_text", "basis")]
    for group, value in fields:
        checked = check(value, mapping, clause=group == "clause")
        counts = Counter(m.kind for m in checked.marks)
        metrics[group].update(occurrences=checked.restored + len(checked.marks),
                              restored=checked.restored, fixed=checked.fixed, removed=checked.removed,
                              variant=counts["variant"], unknown=counts["unknown"],
                              shared=counts["shared"], note=counts["note"])
        # 只列代号文法产生的写法，不列位置、系统标记或模型句子。
        examples += [m.raw for m in checked.marks if m.kind in ("variant", "unknown")]
    calls = list(db.scalars(select(LlmCall).where(LlmCall.run_id == run.id)))
    return dict(fields=metrics, findings=len(findings), model_name=run.model_name,
                prompt_version=run.prompt_version, chunk_count=run.chunk_count,
                verified=sum(f.cite_status == "verified" for f in findings),
                calls=dict(Counter(c.status for c in calls)),
                examples=list(dict.fromkeys(examples))[:10])


def render_probe_report(results, *, copy_rule, bracket_codes):
    lines = ["# 回程体检（仅数字与代号写法）", "",
             f"copy_rule={int(copy_rule)}；bracket_codes={int(bracket_codes)}", "",
             "代号出现数=restored+全部标记数；未能还原率=(variant+unknown)/出现数。分母为零记 N/A。",
             "引用核实率=verified/全部问题数（含缺失型）；结构失败按每次校验失败计，另列涉及样本数。", ""]
    for language in ("cn", "en", "all"):
        selected = [r for r in results if language == "all" or r["sample"].startswith(language + "_")]
        lines += [f"## {language}（{len(selected)}）", "",
                  "| 字段 | 出现数 | 自动修正率 | 未能还原率 | 编造 | 比例删除 | note | shared |",
                  "|---|---|---|---|---|---|---|---|"]
        for group in ("clause", "other"):
            counts = Counter()
            for r in selected:
                counts.update(r.get("probe", {}).get("fields", {}).get(group, {}))
            n = counts["occurrences"]
            rate = lambda v: f"{100 * v / n:.4f}%" if n else "N/A"
            lines.append(f"| {group} | {n} | {rate(counts['fixed'])} | {rate(counts['variant'] + counts['unknown'])} | "
                         f"{counts['unknown']} | {counts['removed']} | {counts['note']} | {counts['shared']} |")
        findings = sum(r.get("probe", {}).get("findings", 0) for r in selected)
        verified = sum(r.get("probe", {}).get("verified", 0) for r in selected)
        lines += ["", f"问题数={findings}；verified={verified}；引用核实率=" +
                  (f"{100 * verified / findings:.4f}%" if findings else "N/A") +
                  f"；结构失败次数={sum(r.get('validation_failures', 0) for r in selected)}；"
                  f"结构失败样本数={sum(r.get('validation_failures', 0) > 0 for r in selected)}", ""]
    lines += ["## 样本状态", "", "| 序号 | 状态 | 问题数 | 结构失败 | 调用(sent/error/blocked) |", "|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        p = r.get("probe", {})
        calls = p.get("calls", {})
        lines.append(f"| {i} | {cell(r['status'])} | {p.get('findings', 0)} | {r.get('validation_failures', 0)} | "
                     + "/".join(str(calls.get(s, 0)) for s in ("sent", "error", "blocked")) + " |")
    examples = list(dict.fromkeys(x for r in results for x in r.get("probe", {}).get("examples", [])))[:10]
    lines += ["", "代号写法：" + "、".join(cell(x) for x in examples), ""]
    return "\n".join(lines)


@contextmanager
def EvaluationSession(*args, extra_params=None, **kwargs):
    """V3Q-15：只合并当前评测调用的配置，不修改持久模型对象。"""
    from app.llm import gateway
    original = gateway.load_model
    with Session(*args, **kwargs) as db:
        def load_model(session, model_id):
            cfg = original(session, model_id)
            if session is db and extra_params:
                return replace(cfg, extra_params={**cfg.extra_params, **extra_params})
            return cfg
        with patch.object(gateway, "load_model", load_model):
            yield db


def require_development() -> None:
    # Q-57：默认拒绝；显式开发开关与专用库名同时满足才允许写入、调用模型。
    if os.environ.get("APP_ENV") != "development":
        raise ValueError("仅允许在 APP_ENV=development 的开发环境运行")
    url = make_url(get_settings().database_url)
    if not (url.database or "").startswith(("aireview_eval_", "aireview_test_")):
        raise ValueError("必须使用 aireview_eval_ 或 aireview_test_ 开头的专用数据库")


def json_object(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("附加请求参数必须是 JSON 对象") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("附加请求参数必须是 JSON 对象")
    return value


def _execute_job(db: Session, task_id, kind: str, handler) -> None:
    """同步接管本脚本新建的 job；不领取、不删除其他任务的工作。"""
    row = db.scalars(select(Job).where(
        Job.type == kind, Job.payload["task_id"].astext == str(task_id), Job.status == "queued"
    ).with_for_update()).one()
    row.status, row.locked_by, row.heartbeat_at = "running", "eval-review", utcnow()
    row.attempts += 1
    job_id = row.id
    db.commit()
    try:
        handler()
        jobs.complete(db, job_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        jobs.fail(db, job_id, type(exc).__name__)
        db.commit()
        raise


def evaluate_sample(db: Session, user: User, path: Path, *, stance: str, source: str,
                    return_probe: bool = False) -> dict:
    """每份 DOCX 独立建任务；不猜测我方，沿用解析预选与确认发送的全部检查。"""
    result = {"sample": path.name, "sha256": None,
              "status": "failed", "error": None, "detail": None}
    try:
        with ExitStack() as stack:
            f = stack.enter_context(path.open("rb"))
            uploads = check_uploads([(path.name, f)], 0, get_settings().max_upload_mb * 1024 * 1024)
            result["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
            f.seek(0)
            task = create_task(db, user, uploads, main_index=0, stance=stance, source=source,
                               policy="ai", user_note=None, ip=None)
        result["task_id"] = str(task.id)
        _execute_job(db, task.id, "parse", lambda: parse_task(db, task.id))
        db.refresh(task)
        if task.status != "awaiting_confirm":
            result.update(status=task.status, error="PARSE_FAILED")
            return result
        run = create_run(db, task, user, None)
        result["run_id"] = str(run.id)
        from app.review import validate
        original_validate = validate.validate_output
        result["validation_failures"] = 0
        def counted_validate(*args, **kwargs):
            try:
                return original_validate(*args, **kwargs)
            except validate.OutputError:
                result["validation_failures"] += 1
                raise
        with patch.object(validate, "validate_output", counted_validate):
            _execute_job(db, task.id, "review", lambda: run_review(db, run.id))
        run = db.get(ReviewRun, run.id, populate_existing=True)
        result["status"] = run.status
        if return_probe:
            result["probe"] = return_metrics(db, run)
        else:
            result["detail"] = run_detail(db, run).model_dump(mode="json")
        if run.status not in ("completed", "partial"):
            result["error"] = "REVIEW_BLOCKED" if run.status == "blocked" else "REVIEW_FAILED"
    except AppError as exc:
        db.rollback()
        result["error"] = exc.code
    except Exception:
        db.rollback()
        # 不输出异常消息或堆栈，其中可能包含文件原文、参数和连接串。
        result["error"] = "INTERNAL_ERROR"
    return result


def cell(value) -> str:
    """模型输出、文件名按 Markdown 表格的纯文本转义，避免 HTML 或结构注入。"""
    text = html.escape(str(value if value is not None else ""), quote=True)
    for ch in "\\`*_{}[]()#+.!|~-":
        text = text.replace(ch, "\\" + ch)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def render_report(results: list[dict], *, stance: str, source: str, extra_params: dict,
                  copy_rule=False, bracket_codes=False) -> str:
    # Q-57：报告保存虚构样本的还原结果；任意附加参数仅记摘要，避免误写密钥。
    fingerprint = hashlib.sha256(json.dumps(extra_params, sort_keys=True).encode()).hexdigest()
    lines = ["# 审核效果评测", "", "仅供开发期虚构样本对比；结论统计不代表准确率或人工验收。", "",
             f"生成时间（UTC）：{utcnow().isoformat()}", "",
             f"copy_rule={int(copy_rule)}；bracket_codes={int(bracket_codes)}", "",
             f"立场：{cell(stance)}；来源：{cell(source)}；临时参数 SHA-256：{fingerprint}", "",
             f"样本总数：{len(results)}；完成/部分完成："
             f"{sum(r['status'] in ('completed', 'partial') for r in results)}", ""]
    for index, result in enumerate(results, 1):
        lines += [f"## 样本 {index}", "", "| 项目 | 内容 |", "|---|---|",
                  f"| 文件 | {cell(result['sample'])} |", f"| SHA-256 | {result['sha256']} |",
                  f"| 状态 | {cell(result['status'])} |",
                  f"| 任务 | {cell(result.get('task_id'))} |",
                  f"| 错误码 | {cell(result['error'])} |", ""]
        detail = result["detail"]
        if detail is None:
            lines += ["未产生审核结果。", ""]
            continue
        for key in ("id", "model_name", "prompt_version", "builtin_req_version", "company_req_version"):
            lines += [f"{key}：{cell(detail[key])}", ""]
        lines += ["### 要求结论统计", "", "| 结论 | 数量 |", "|---|---|"]
        counts = Counter(r["verdict"] for r in detail["requirement_results"])
        lines += [f"| {verdict} | {counts[verdict]} |" for verdict in ("ok", "issue", "na", "unable")]
        lines += ["", "### 各要求结论", "", "| 编号 | 来源 | 维度 | 要求 | 结论 | 说明 |",
                  "|---|---|---|---|---|---|"]
        for r in detail["requirement_results"]:
            lines.append("| " + " | ".join(cell(r[k]) for k in
                         ("req_id", "req_source", "dimension", "req_text", "verdict", "note")) + " |")
        lines += ["", "### 问题清单", ""]
        if not detail["findings"]:
            lines += ["无问题记录。", ""]
        for f in detail["findings"]:
            lines += [f"#### 问题 {f['seq']}", "", "| 字段 | 内容 |", "|---|---|"]
            for key in ("title", "category", "severity", "req_ids", "locs", "location", "missing",
                        "cite_status", "quote", "problem", "impact", "suggestion", "suggested_text", "basis"):
                value = f[key]
                lines.append(f"| {key} | {cell(', '.join(value) if isinstance(value, list) else value)} |")
            lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="开发环境虚构 DOCX 样本审核评测（经模型网关）")
    parser.add_argument("samples", type=Path, help="样本目录；只读取第一层 DOCX，每份独立审核")
    parser.add_argument("--output", type=Path, required=True, help="新建的 Markdown 报告路径")
    parser.add_argument("--username", required=True, help="专用开发库中已有的启用用户")
    parser.add_argument("--stance", choices=("sales", "purchase", "other"), default="sales")
    parser.add_argument("--source", choices=("outgoing", "incoming"), default="outgoing")
    parser.add_argument("--extra-params", type=json_object, default={})
    parser.add_argument("--copy-rule", action="store_true", help="评测进程内在第 2 条追加代号照抄规则")
    parser.add_argument("--bracket-codes", action="store_true", help="评测进程内使用方括号代号与提示词示例")
    parser.add_argument("--return-probe", action="store_true", help="只写回程数字报告及同名 JSON，不写模型正文")
    args = parser.parse_args(argv)
    try:
        require_development()
        if not args.samples.is_dir():
            raise ValueError("样本目录不存在")
        paths = sorted((p for p in args.samples.iterdir() if p.is_file() and p.suffix.lower() == ".docx"),
                       key=lambda p: p.name)
        if not paths:
            raise ValueError("样本目录中没有 DOCX 文件")
        with probe_options(copy_rule=args.copy_rule, bracket_codes=args.bracket_codes), \
                EvaluationSession(bind=get_engine(), expire_on_commit=False,
                                  extra_params=args.extra_params) as db:
            user = db.scalars(select(User).where(User.username == args.username,
                                                 User.status == "active")).first()
            if user is None:
                raise ValueError("开发用户不存在或已停用")
            # 在任何模型调用前确认报告可写，且不覆盖已有报告（Q-57）。
            with ExitStack() as stack:
                report = stack.enter_context(args.output.open("x", encoding="utf-8"))
                data = stack.enter_context(args.output.with_suffix(".json").open("x", encoding="utf-8")) if args.return_probe else None
                results = []
                for path in paths:
                    results.append(evaluate_sample(db, user, path, stance=args.stance, source=args.source,
                                                   return_probe=args.return_probe))
                    # 每份完成即保存统计与 ID；失败证据保留在专用库中。
                    if data:
                        data.seek(0)
                        json.dump(dict(copy_rule=args.copy_rule, bracket_codes=args.bracket_codes,
                                       results=results), data, ensure_ascii=False, indent=2)
                        data.truncate()
                        data.flush()
                report.write(render_probe_report(results, copy_rule=args.copy_rule, bracket_codes=args.bracket_codes)
                             if args.return_probe else
                             render_report(results, stance=args.stance, source=args.source, extra_params=args.extra_params,
                                           copy_rule=args.copy_rule, bracket_codes=args.bracket_codes))
        print(f"评测结束：{len(results)} 个样本，报告已写入")
        return 1 if any(r["status"] not in ("completed", "partial") for r in results) else 0
    except ValueError:
        print("评测配置无效：请检查开发环境、专用数据库、目录和启用用户", file=sys.stderr)
    except OSError:
        print("无法读取样本或创建报告（报告不得已存在）", file=sys.stderr)
    except Exception:
        print("评测未完成，请检查开发环境配置", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
