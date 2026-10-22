"""M6-09：A-01 报价表金额校验准确率；A-03 回归覆盖本地校验摘要与汇总行。

A-01：对本地校验样本、其他解析样本和脱敏回归集逐份解析，在识别出的每张报价表上用独立的
Decimal 计算逐行重算“数量×单价”和合计，与 L-01 / L-02 的报告逐条比较：
该报的全部报出、不该报的一条不报，无法识别的数字行必须列为未核验。

A-03：本地校验样本（M4 的 A-03 未包含）全部走真实接口 + 模拟网关，外发记录与实际请求经出口复检
零命中，本地校验结果中的真实金额一律不外发。
"""
import re
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.checks.amounts import _scan_tables
from app.checks.model import CheckFile, CheckNode
from app.checks.service import evaluate
from app.db.models import Job, LocalFinding, Task
from app.redaction.recheck import recheck
from app.security.crypto import decrypt_text
from app.tasks import pipeline
from app.tasks.pipeline import _parse_bytes
from tests.acceptance_support import (
    _docx_bytes, confirm_and_review, ensure_our_party, fixture_samples, golden_samples, outbound_records,
    settle_suspects,
)
from tests.review_support import env, llm  # noqa: F401

CHECKS = Path(__file__).parent / "fixtures" / "checks"
CENT = Decimal("0.01")
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _samples() -> list[tuple[str, bytes]]:
    own = [(f.name, f.read_bytes()) for f in sorted(CHECKS.iterdir()) if f.suffix in (".docx", ".pdf", ".xlsx")]
    return own + [(s.name, s.data) for s in fixture_samples() + golden_samples()]


def _num(text: str) -> Decimal | None:
    """独立的金额读取：单元格里恰好一个数字（可带币种、千分位）才算可读。"""
    found = _NUM.findall(text)
    return Decimal(found[0].replace(",", "")) if len(found) == 1 else None


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _row_of(loc: str, table) -> int:
    return next(r for r, cells in table.rows.items() if any(n.loc == loc for n in cells.values()))


@pytest.fixture(scope="module")
def parsed():
    out = []
    for name, data in _samples():
        result, _ = _parse_bytes(name.rsplit(".", 1)[-1].lower(), data, 1, 0)
        nodes = [CheckNode(n.loc, n.text, 1, n.clause_no) for n in result.nodes if n.part == "body"]
        out.append((name, nodes, CheckFile(1, True, name.rsplit(".", 1)[-1].lower(), "quotation", result.warnings)))
    return out


def test_a01_every_recognized_quote_row_recomputed(parsed):
    stats = {"tables": 0, "rows": 0, "row_errors": 0, "totals": 0, "total_errors": 0, "skipped": 0}
    for name, nodes, file in parsed:
        results = evaluate(nodes, [file], date(2026, 10, 20)).results
        for table in (t for t in _scan_tables(nodes) if t.recognized):
            stats["tables"] += 1
            reported_rows = {_row_of(r.locs[1], table) for r in results
                             if r.code == "L-01" and r.locs and r.locs[0].startswith(table.name + "-")}
            expected_rows, unreadable, amounts = set(), [], []
            for row in table.data_rows:
                qty, price, amount = (_num(table.text(row, k)) for k in ("qty", "price", table.amount))
                amounts.append(amount)
                if qty is None or price is None or amount is None:
                    unreadable.append(row)
                    continue
                stats["rows"] += 1
                if abs(_cents(amount) - _cents(qty * price)) > CENT:
                    expected_rows.add(row)
            assert reported_rows == expected_rows, (name, table.name)
            stats["row_errors"] += len(expected_rows)
            stats["skipped"] += len(unreadable)
            info = " ".join(r.detail for r in results if r.code == "L-INFO")
            for row in unreadable:
                assert re.search(rf"{table.name} 第 [\d、]*\b{row}\b", info), (name, table.name, row)
            total_reported = any(r.code == "L-02" and r.locs[0].startswith(table.name + "-") for r in results)
            if table.total_row is not None and amounts and None not in amounts:
                total = _num(table.text(table.total_row, table.amount))
                if total is not None:
                    stats["totals"] += 1
                    wrong = abs(_cents(total) - sum((_cents(a) for a in amounts), Decimal(0))) > CENT
                    stats["total_errors"] += wrong
                    assert total_reported == wrong, (name, table.name)
                    continue
            assert not total_reported, (name, table.name)
    # 样本必须真的覆盖到报价表、错行和错合计，否则“准确率 100%”没有意义。
    assert stats["tables"] >= 8 and stats["row_errors"] >= 2 and stats["total_errors"] >= 1, stats
    print("A-01", stats)


# 本地校验说明里的金额（带两位小数）：外发文本中不得出现，含去掉千分位的写法。
# 日期不属于 SPEC 9.2 的脱敏范围，正文与摘要中均可外发（design/M6.md L-07、L-08 说明），不在此检查。
_DETAIL_VALUE = re.compile(r"\d[\d,]*\.\d{2}")


@pytest.mark.usefixtures("env")
def test_a03_local_check_samples_outbound_clean(db_sessionmaker, make_user, login, llm):
    make_user("m6-a03")
    c, token = login("m6-a03")
    h = {"X-CSRF-Token": token}
    requests = llm("normal")
    # 本地校验样本多数只有表格、没有签约方；每份附一页虚构签约方说明作为附件，样本本身仍是主文件。
    parties = _docx_bytes(["签约方说明", "卖方：星河虚构进出口有限公司", "买方：Fictional Metals Trading FZE"])
    task_of = {}
    for f in sorted(CHECKS.iterdir()):
        if f.suffix not in (".docx", ".pdf", ".xlsx"):
            continue
        r = c.post("/api/v1/tasks", headers=h, data={"stance": "sales", "source": "outgoing", "policy": "ai", "main_index": "0"},
                   files=[("files", (f.name, f.read_bytes(), "application/octet-stream")),
                          ("files", ("签约方说明.docx", parties, "application/octet-stream"))])
        assert r.status_code == 201, r.text
        tid = uuid.UUID(r.json()["id"])
        with db_sessionmaker() as db:  # 代替 worker 执行 parse
            pipeline.parse_task(db, tid)
            db.execute(update(Job).where(Job.payload["task_id"].astext == str(tid)).values(status="done"))
            db.commit()
            assert db.get(Task, tid).status == "awaiting_confirm", f.name
        settle_suspects(c, h, tid, [])
        ensure_our_party(c, h, tid)
        settle_suspects(c, h, tid, [])
        assert confirm_and_review(c, h, db_sessionmaker, tid)["status"] == "completed", f.name
        task_of[tid] = f.name
    records = outbound_records(db_sessionmaker, requests)
    assert sorted(r.task_id for r in records) == sorted(task_of) and {r.status for r in records} == {"sent"}
    with db_sessionmaker() as db:
        details = {}
        for f in db.scalars(select(LocalFinding)):
            details.setdefault(f.task_id, []).append(decrypt_text(f.detail_enc))
    assert sum(map(len, details.values())) >= 12
    problems, checked = [], 0
    for r in records:
        name = task_of[r.task_id]
        values = {v for d in details.get(r.task_id, []) for v in _DETAIL_VALUE.findall(d)}
        values |= {v.replace(",", "") for v in values}
        checked += len(values)
        for source, text in (("llm_calls", r.request_text), ("request", r.wire_text)):
            assert ("【本地校验摘要】" in text) == bool(details.get(r.task_id)), name
            problems += [f"{name} {source}：{x.category} @ {x.position}"
                         for x in recheck(text, real_values=r.real_values, terms=r.terms)]
            problems += [f"{name} {source}：含本地校验数值 {v}" for v in values if v in text]
    assert problems == [], "\n".join(problems)
    assert checked >= 10, checked
