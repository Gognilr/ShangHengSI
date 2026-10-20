"""M5-08：全部可解析样本经真实接口与模拟网关统计 A-02、A-05、A-07。

模型逐个引用实际发送的正文位置，避免只校验每份文件的第一个标题；分段长度为 1000，
同时覆盖普通节点与报价汇总行。加密 PDF 的预期解析失败由 pipeline 测试覆盖。
"""
import json
import re
import uuid
from collections import Counter

import httpx
import pytest
from sqlalchemy import select

from app.admin import settings_store
from app.db.models import DocNode, Finding, ReviewRun, Task, TaskFile
from app.main import API_PREFIX
from app.parsing.model import with_clause_no
from app.redaction.engine import load_redaction_map
from app.redaction.restore import restore
from app.review import service
from app.security.crypto import decrypt_text
from tests.acceptance_support import (
    confirm_and_review,
    ensure_our_party,
    fixture_samples,
    golden_samples,
    settle_suspects,
    upload_and_parse,
)
from tests.mock_llm import build_reply
from tests.review_support import env, llm  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")
REQ_LINE = re.compile(r"^\[([A-Z]{1,2}-\d+|CO-\d+|NOTE)\]", re.MULTILINE)
DOC_LINE = re.compile(r"^\[(F\d+-[^\]]+)\] ?(.*)$", re.MULTILINE)


def test_all_samples_verified_locations_coverage_and_requirement_identity(db_sessionmaker, make_user, login, llm):
    make_user("m5-acceptance")
    c, token = login("m5-acceptance")
    h = {"X-CSRF-Token": token}
    with db_sessionmaker() as db:
        settings_store.set_setting(db, "model.chunk_chars", 1000, None)
        db.commit()

    def every_location(request):
        body = json.loads(request.content)
        status, payload = build_reply(body)
        out = json.loads(payload["choices"][0]["message"]["content"])
        text = body["messages"][1]["content"].split("<document>", 1)[1].split("</document>", 1)[0]
        out["findings"] = [dict(
            title=f"虚构定位检查 {loc}", category="商务", severity="中",
            req_ids=[out["requirement_results"][0]["req_id"]], locs=[loc], quote=line,
            missing=False, problem="虚构测试条目，用于核对定位。",
        ) for loc, line in DOC_LINE.findall(text) if line.strip()]
        payload["choices"][0]["message"]["content"] = json.dumps(out, ensure_ascii=False)
        return httpx.Response(status, json=payload)

    requests = llm(handler=every_location)
    samples = golden_samples() + fixture_samples()
    stats = Counter(samples=len(samples))
    parts = set()
    for sample in samples:
        start = len(requests)
        tid = upload_and_parse(c, h, db_sessionmaker, sample)
        settle_suspects(c, h, tid, sample.must_suspect)
        ensure_our_party(c, h, tid)
        settle_suspects(c, h, tid, sample.must_suspect)
        detail = confirm_and_review(c, h, db_sessionmaker, tid)
        assert detail["status"] == "completed", sample.name
        calls = requests[start:]
        assert len(calls) == detail["chunk_count"]
        stats["runs"] += 1
        stats["chunks"] += len(calls)
        stats["chunked_runs"] += len(calls) > 1

        # 独立从实际请求提取要求编号，比较身份与数量；不能用“相同数量但不同编号”蒙混通过。
        actual_ids = [r["req_id"] for r in detail["requirement_results"]]
        wire_lines = {}
        for call in calls:
            user = json.loads(call.content)["messages"][1]["content"]
            section = user.split("【审核要求】", 1)[1].split("【", 1)[0]
            assert actual_ids == REQ_LINE.findall(section), sample.name
            body = user.split("<document>", 1)[1].split("</document>", 1)[0]
            wire_lines.update((loc, line) for loc, line in DOC_LINE.findall(body) if line.strip())
        assert len(actual_ids) == len(set(actual_ids))

        with db_sessionmaker() as db:
            run = db.get(ReviewRun, uuid.UUID(detail["id"]))
            task = db.get(Task, tid)
            files = list(db.scalars(select(TaskFile).where(TaskFile.task_id == tid)))
            main = next(f for f in files if f.is_main)
            ctx = service.build_context(db, task, stance=run.stance, source=run.source,
                                        user_note=run.user_note, main_doc_type=main.doc_type)
            mapping = load_redaction_map(db, tid)
            nodes = {n.loc: n for n in db.scalars(select(DocNode).where(DocNode.task_id == tid))}
            summaries = {t.first_loc: (t.loc, wire_lines[t.loc]) for t in ctx.quote_tables if t.loc in wire_lines}
            findings = list(db.scalars(select(Finding).where(Finding.run_id == run.id)))
            api_findings = {str(f["id"]): f for f in detail["findings"]}
            assert len(findings) == len(wire_lines) > 0, sample.name
            for finding in findings:
                assert finding.cite_status == "verified" and finding.locs
                first = finding.locs[0]
                assert first in nodes, (sample.name, first)
                if first in summaries and finding.title.endswith(summaries[first][0]):
                    expected = restore(summaries[first][1], mapping)
                    stats["summary_citations"] += 1
                else:
                    node = nodes[first]
                    expected = with_clause_no(decrypt_text(node.text_enc), node.clause_no)
                assert decrypt_text(finding.quote_snapshot_enc) == expected, (sample.name, first)
                api = api_findings[str(finding.id)]
                assert api["locs"][0] == first and api["quote"] == expected and api["location"]
                stats["verified_citations"] += 1

            # A-05：持久化内容与展示一一核对；低置信警告由同页的专门说明覆盖。
            coverage, notes = run.coverage, detail["coverage"]["notes"]
            joined = "\n".join(notes)
            for file in files:
                for warning in file.warnings or []:
                    assert warning in joined or (
                        "OCR 识别置信度低" in warning and
                        any(x["file"] == file.seq and x["pages"] for x in coverage["ocr_low"])
                    ), (sample.name, warning)
            excluded = Counter((n.file_id, n.part) for n in nodes.values() if n.part != "body")
            seqs = {f.id: f.seq for f in files}
            assert {(x["file"], x["part"]): x["count"] for x in coverage["excluded"]} == {
                (seqs[fid], part): count for (fid, part), count in excluded.items()
            }
            parts.update(part for _, part in excluded)
            stats["ocr_low_pages"] += sum(len(x["pages"]) for x in coverage["ocr_low"])
            assert ("未做跨分段的一致性检查" in joined) == (len(calls) > 1)
            assert len(coverage["quote_tables"]) == len(ctx.quote_tables)

    assert {"header", "footer", "comment", "textbox", "hidden_sheet"} <= parts
    assert stats["samples"] >= 26 and stats["chunked_runs"] > 0
    assert stats["summary_citations"] > 0 and stats["ocr_low_pages"] > 0
    print("M5_ACCEPTANCE " + json.dumps(dict(stats), sort_keys=True))
