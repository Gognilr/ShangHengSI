"""开发期虚构 DOCX 样本评测；复用业务链路，所有模型调用仍经过网关。"""
import argparse
from collections import Counter
from contextlib import ExitStack
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
from app.db.models import Job, ReviewRun, Setting, User
from app.db.session import get_engine
from app.review.job import run_review
from app.review.service import create_run, run_detail
from app.security.errors import AppError
from app.tasks.pipeline import parse_task
from app.tasks.service import check_uploads, create_task


class EvaluationSession(Session):
    """Q-57：附加参数仅覆盖当前会话的读取结果，不写 settings，不影响其他进程。"""

    def __init__(self, *args, extra_params=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra_params = extra_params or {}

    def get(self, entity, ident, **kwargs):
        row = super().get(entity, ident, **kwargs)
        if entity is Setting and ident == "model.extra_params" and self.extra_params:
            base = row.value if row is not None and isinstance(row.value, dict) else {}
            return Setting(key=ident, value={**base, **self.extra_params})
        return row


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


def evaluate_sample(db: Session, user: User, path: Path, *, stance: str, source: str) -> dict:
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
        _execute_job(db, task.id, "review", lambda: run_review(db, run.id))
        run = db.get(ReviewRun, run.id, populate_existing=True)
        result.update(status=run.status, detail=run_detail(db, run).model_dump(mode="json"))
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


def render_report(results: list[dict], *, stance: str, source: str, extra_params: dict) -> str:
    # Q-57：报告保存虚构样本的还原结果；任意附加参数仅记摘要，避免误写密钥。
    fingerprint = hashlib.sha256(json.dumps(extra_params, sort_keys=True).encode()).hexdigest()
    lines = ["# 审核效果评测", "", "仅供开发期虚构样本对比；结论统计不代表准确率或人工验收。", "",
             f"生成时间（UTC）：{utcnow().isoformat()}", "",
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
    args = parser.parse_args(argv)
    try:
        require_development()
        if not args.samples.is_dir():
            raise ValueError("样本目录不存在")
        paths = sorted((p for p in args.samples.iterdir() if p.is_file() and p.suffix.lower() == ".docx"),
                       key=lambda p: p.name)
        if not paths:
            raise ValueError("样本目录中没有 DOCX 文件")
        with EvaluationSession(bind=get_engine(), expire_on_commit=False,
                               extra_params=args.extra_params) as db:
            user = db.scalars(select(User).where(User.username == args.username,
                                                 User.status == "active")).first()
            if user is None:
                raise ValueError("开发用户不存在或已停用")
            # 在任何模型调用前确认报告可写，且不覆盖已有报告（Q-57）。
            with args.output.open("x", encoding="utf-8") as report:
                results = [evaluate_sample(db, user, p, stance=args.stance, source=args.source) for p in paths]
                report.write(render_report(results, stance=args.stance, source=args.source,
                                           extra_params=args.extra_params))
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
