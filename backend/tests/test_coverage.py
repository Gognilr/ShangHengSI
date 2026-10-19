"""审核覆盖说明的结构化内容与 A-05 可见性。"""
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Task, TaskFile
from app.review import service
from app.review.coverage import build_coverage, describe
from app.review.prompt import Requirement
from app.review.validate import RequirementResultView, ValidatedReview
from tests.review_support import env, ready_task  # noqa: F401


@pytest.fixture
def owner(make_user):
    return make_user("coverage-owner")


def test_describe_uses_fixed_sentences_and_omits_duplicate_ocr_warning():
    coverage = {
        "excluded": [
            {"file": 1, "part": "header", "count": 2},
            {"file": 1, "part": "footer", "count": 1},
            {"file": 1, "part": "comment", "count": 1},
            {"file": 1, "part": "textbox", "count": 1},
            {"file": 2, "part": "hidden_sheet", "count": 15},
        ],
        "quote_tables": [{"file": 2, "loc": "F2-S1", "rows": 12}],
        "ocr_low": [{"file": 3, "pages": [5, 2, 2]}],
        "warnings": [
            {"file": 1, "text": "检测到修订痕迹，按接受修订后的文本审核"},
            {"file": 3, "text": "第 2 页 OCR 识别置信度低，请核对原件"},
        ],
        "unable": [{"req_id": "G-06", "note": "需合规人员确认"}, {"req_id": "G-09", "note": None}],
        "chunked": True,
        "chunk_count": 3,
        "failed_chunks": [{"index": 2, "first_loc": "F1-P120", "last_loc": "F1-P260",
                           "reason": "模型服务调用失败"}],
        "sanctions_offline": True,
    }
    notes = describe(coverage, {1: "合同.docx", 2: "报价.xlsx", 3: "扫描.pdf"})
    assert notes == [
        "合同.docx：页眉 2 处、页脚 1 处、批注 1 处、文本框 1 处未参与 AI 审核",
        "报价.xlsx：隐藏工作表的单元格 15 个未参与 AI 审核",
        "报价.xlsx F2-S1 报价明细表共 12 行，只把汇总信息发给 AI，行级数据未逐行审核",
        "扫描.pdf 第 2、5 页 OCR 识别置信度低，请核对原件",
        "合同.docx：检测到修订痕迹，按接受修订后的文本审核",
        "无法判断的审核要求：G-06（需合规人员确认）、G-09",
        "文件较长，分 3 段审核，未做跨分段的一致性检查",
        "第 2 段（F1-P120 至 F1-P260）审核失败：模型服务调用失败，该部分没有审核结果",
    ]
    assert "星河虚构进出口有限公司" not in "\n".join(notes)


def test_build_coverage_includes_a05_samples_and_only_identifiers(db_sessionmaker, owner, ready_task):
    fixture_dir = Path(__file__).resolve().parent / "fixtures"
    files = [
        ("杂项.docx", (fixture_dir / "docx" / "parts_misc.docx").read_bytes()),
        ("隐藏.xlsx", (fixture_dir / "xlsx" / "hidden_sheet.xlsx").read_bytes()),
        ("扫描.pdf", (fixture_dir / "pdf" / "scanned_contract.pdf").read_bytes()),
        ("虚构报价.docx", (fixture_dir / "docx" / "coverage_quote_table.docx").read_bytes()),
    ]
    task = ready_task(owner, files=files)

    with db_sessionmaker() as db:
        stored_task = db.get(Task, task.id)
        main = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.is_main.is_(True)))
        ctx = service.build_context(db, stored_task, stance=task.stance, source=task.source,
                                    user_note=None, main_doc_type=main.doc_type)
        ctx.requirements = [
            Requirement(id="G-01", source="builtin", dimension="表述", text="虚构要求一"),
            Requirement(id="G-02", source="builtin", dimension="逻辑", text="虚构要求二"),
        ]
        review = ValidatedReview(
            doc_summary="虚构摘要",
            requirement_results=[
                RequirementResultView("G-01", "builtin", "虚构要求一", "表述", "issue", None),
                RequirementResultView("G-02", "builtin", "虚构要求二", "逻辑", "unable", "需人工确认"),
            ],
            findings=[], sanctions_level="none", sanctions_reasons=[],
        )
        coverage = build_coverage(db, stored_task, ctx, review, 1, [])
        file_names = {f.seq: f.original_name for f in db.scalars(
            select(TaskFile).where(TaskFile.task_id == task.id).order_by(TaskFile.seq)
        )}
        notes = describe(coverage, file_names)

    assert coverage["requirements"] == {"total": 2, "issue": 1, "ok": 0, "na": 0, "unable": 1}
    assert coverage["unable"] == [{"req_id": "G-02", "note": "需人工确认"}]
    assert {item["part"] for item in coverage["excluded"] if item["file"] == 1} >= {
        "header", "footer", "comment", "textbox"
    }
    assert any(item["file"] == 2 and item["part"] == "hidden_sheet" for item in coverage["excluded"])
    assert coverage["ocr_low"] and coverage["ocr_low"][0] == {"file": 3, "pages": [2]}
    assert coverage["quote_tables"] == [{"file": 4, "loc": "F4-T1", "rows": 3}]
    # A-05：每个文件的每条解析警告都列出（与 ocr_low 重复的低置信度警告除外），0 条被静默忽略
    assert {item["file"] for item in coverage["warnings"]} >= {1, 2, 3}
    for item in coverage["warnings"]:
        if "OCR 识别置信度低" not in item["text"]:
            assert f"{file_names[item['file']]}：{item['text']}" in notes, item
    joined = "\n".join(notes)
    assert "杂项.docx" in joined and "隐藏.xlsx" in joined
    assert "扫描.pdf 第 2 页 OCR 识别置信度低，请核对原件" in joined
    assert "虚构报价.docx F4-T1 报价明细表共 3 行，只把汇总信息发给 AI，行级数据未逐行审核" in joined
    assert "付款条款见附表" not in joined and "星河虚构进出口有限公司" not in joined
    assert "虚构焦炭甲" not in joined and "USD 1,234.56" not in joined
    assert coverage["sanctions_offline"] is True


def test_all_failed_coverage_keeps_total_but_zero_verdict_counts(db_sessionmaker, owner, ready_task):
    task = ready_task(owner)
    with db_sessionmaker() as db:
        stored_task = db.get(Task, task.id)
        main = db.scalar(select(TaskFile).where(TaskFile.task_id == task.id, TaskFile.is_main.is_(True)))
        ctx = service.build_context(db, stored_task, stance=task.stance, source=task.source,
                                    user_note=None, main_doc_type=main.doc_type)
        failed = [{"index": 1, "first_loc": "F1-P1", "last_loc": "F1-P4", "reason": "模型服务调用失败"}]
        coverage = build_coverage(db, stored_task, ctx, None, 1, failed)

    assert coverage["requirements"] == {
        "total": len(ctx.requirements), "issue": 0, "ok": 0, "na": 0, "unable": 0,
    }
    assert coverage["unable"] == [] and coverage["failed_chunks"] == failed
    assert coverage["chunked"] is False and coverage["no_cross_chunk_check"] is False
