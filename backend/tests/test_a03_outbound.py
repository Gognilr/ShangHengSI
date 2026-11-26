"""A-03（SPEC 23.1，设计 docs/design/M4.md 第七节）：外发记录中出现对照表真实值、已记住的名称、
未替换的电话 / 账号 / 带币种金额的次数为 0。

对脱敏回归集和 fixtures 中的全部样本走真实接口 + 模拟模型（must_suspect 选替换，其余疑似遗漏忽略），
全部样本在同一个库中依次处理（前面任务记住的名称会作用于后面的任务）。然后对每一次外发，检查
llm_calls.request_text 和模拟服务实际收到的请求：
* 本任务对照表中的真实值、发送时已存在的已记住名称和自定义敏感词都不出现（按出口复检的比较规则，Q-19）；
* 按 9.3 第 4、5 步重扫，没有未替换的电话、邮箱、账号、证件号、带币种的金额；
* 样本标注的 must_replace 文字不出现。
需要 TEST_DATABASE_URL；含 OCR 样本，耗时较长。
"""
import pytest
from sqlalchemy import select

from app.db.models import RedactionTerm, Task
from app.redaction.recheck import recheck
from tests.acceptance_support import compact, fixture_samples, golden_samples, outbound_records, run_sample
from tests.review_support import env, llm, ready_task  # noqa: F401

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def alice(make_user, login):
    make_user("alice")
    client, csrf = login("alice")
    return client, {"X-CSRF-Token": csrf}


def _culprit(text, reason, values, terms) -> str:
    """诊断用：命中该位置的真实值或词条（样本全部虚构，可以写进断言消息）。"""
    pool = values if reason.category == "MAP" else terms if reason.category == "TERM" else []
    for v in pool:
        if any(r.position == reason.position for r in recheck(text, real_values=[v], terms=[])):
            return repr(v)
    return repr(text[reason.position:reason.position + 30])


def _must_replace_present(sample_name: str, needle: str, text: str) -> bool:
    # V2Q-58：只排除该样本该标注与固定要求编号的碰撞，正文短串仍严格检查。
    if sample_name in ('amounts_cn_forms.docx', 'e06a_amount_original.docx',
                       'e06a_amount_bidi.docx', 'e06a_amount_zero_width.docx') and needle == '15':
        text = text.replace('[C-15]', '')
        # 派生样本复制成 DOCX 后第 15 段是位置编号，不能当作正文金额。
        if sample_name.startswith('e06a_'):
            import re
            text = re.sub(r'\[F\d+-P15\]', '', text)
    return needle in text


def test_v2q58_unredacted_body_still_fails():
    assert not _must_replace_present('e06a_amount_original.docx', '15', '[F1-P15] [C-15] 条款')
    assert _must_replace_present('e06a_amount_original.docx', '15', '[F1-P15] 正文金额15万欧元')
    assert not _must_replace_present('amounts_cn_forms.docx', '15', '[C-15] 质保期要求')
    with pytest.raises(AssertionError):
        assert not _must_replace_present('amounts_cn_forms.docx', '15', '[C-15] 正文金额15万欧元')
    assert _must_replace_present('other.docx', '15', '[C-15]')
    assert _must_replace_present('amounts_cn_forms.docx', 'C-15', '[C-15]')


def test_a03_no_real_values_in_outbound_records(db_sessionmaker, alice, llm, monkeypatch):
    c, h = alice
    requests = llm("normal")
    samples = golden_samples() + fixture_samples()
    assert len(samples) >= 20
    task_of = {run_sample(c, h, db_sessionmaker, s): s for s in samples}

    with db_sessionmaker() as db:
        assert db.scalar(select(Task.id).where(Task.status != "reviewed")) is None
        assert db.scalar(select(RedactionTerm.id).where(RedactionTerm.kind == "learned")) is not None
        # 外发记录不写主文件名（常含对方公司名），只写用途（V31Q-115）
        from app.db.models import LlmCall
        assert set(db.scalars(select(LlmCall.task_title).distinct())) == {"文件审核"}
    records = outbound_records(db_sessionmaker, requests)
    # 每个样本都发送了（D-03 起每轮按维度组发送多次，每条记录都逐条复检），且全部 sent（被出口复检拦截的会是 blocked）
    assert {r.task_id for r in records} == set(task_of)
    assert {r.status for r in records} == {"sent"}
    assert sum(len(r.real_values) for r in records) > 200 and any(r.terms for r in records)

    problems = []
    for r in records:
        sample = task_of[r.task_id]
        for source, text in (("llm_calls", r.request_text), ("request", r.wire_text)):
            assert sample.name and text
            for reason in recheck(text, real_values=r.real_values, terms=r.terms):
                problems.append(f"{sample.name} {source}：{reason.category} @ {reason.position} "
                                f"{_culprit(text, reason, r.real_values, r.terms)}")
            problems += [f"{sample.name} {source}：含 must_replace {n!r}" for n in sample.must_replace
                         if _must_replace_present(sample.name, n, text)]
            # 长数字（账号等）换了分组写法也算出现
            problems += [f"{sample.name} {source}：含 must_replace {n!r}（去掉分隔符后）" for n in sample.must_replace
                         if sum(ch.isdigit() for ch in n) >= 8 and compact(n) in compact(text)]
    assert problems == [], '\n'.join(problems)

    # V31Q-18/20：golden 标注的历史映射复现，仍经过真实确认接口和模拟网关。
    import uuid
    import yaml
    from app.db.models import LlmCall, LlmModel
    from app.llm import gateway
    from app.main import API_PREFIX
    from app.redaction import engine, ner
    from tests.acceptance_support import GOLDEN
    from tests.test_assistant_sessions import document, parse
    for path in sorted(GOLDEN.glob('*.yaml')):
        case = yaml.safe_load(path.read_text(encoding='utf-8')).get('history_keep')
        if not case:
            continue
        word = case['text']
        assert word in path.with_suffix('.txt').read_text(encoding='utf-8')
        # 此处验证历史映射生命周期，识别本身由上面的真实 golden 全集覆盖。
        with monkeypatch.context() as local:
            local.setattr(ner, 'recognize', lambda text: [])
            tid = c.post(API_PREFIX + '/assistant/sessions', headers=h).json()['id']
            base = API_PREFIX + '/assistant/sessions/' + tid
            assert c.post(base + '/files', headers=h, files={'files': ('虚构术语.docx', document(word))}).status_code == 200
            parse(db_sessionmaker, tid)
            endpoint = API_PREFIX + '/tasks/' + tid + '/redaction'
            candidate = c.get(endpoint).json()['suspects'][0]
            assert c.post(endpoint + f"/suspects/{candidate['id']}", headers=h, json={'decision': 'ignore'}).status_code == 200
            with db_sessionmaker() as db:
                task = db.get(Task, uuid.UUID(tid))
                mapping = engine._load_full_map(db, task.id)
                mapping[case['code']] = word
                engine.write_full_map(db, task.id, mapping)
                db.commit()
            response = c.post(base + '/confirm', headers=h)
            assert response.status_code == 200, response.text
            with db_sessionmaker() as db:
                task = db.get(Task, uuid.UUID(tid))
                assert engine.load_redaction_map(db, task.id)[case['code']] == word
                values = engine.load_real_values(db, task.id)
                assert word not in values
                before = len(requests)
                gateway.call_model(db, model_id=db.scalar(select(LlmModel.id)), purpose='assist',
                                   messages=[{'role': 'user', 'content': word}], user_id=task.owner_id, task_id=task.id)
                assert len(requests) == before + 1
                assert recheck(word, real_values=values, terms=[]) == []
                assert db.scalar(select(LlmCall.status).order_by(LlmCall.id.desc()).limit(1)) == 'sent'


def test_unshifted_original_date_is_blocked(db_sessionmaker, make_user, ready_task, llm):
    from app.admin.settings_store import set_setting
    from app.db.models import LlmCall, LlmModel
    from app.llm import gateway
    from app.redaction.engine import load_real_values
    owner = make_user('date_outbound')
    with db_sessionmaker() as db:
        set_setting(db, 'redaction.date_shift', True, owner.id)
        db.commit()
    task = ready_task(owner, user_note='签约日期2026年3月5日')
    requests = llm('normal')
    with db_sessionmaker() as db:
        assert '2026年3月5日' in load_real_values(db, task.id)
        with pytest.raises(gateway.GatewayError) as raised:
            gateway.call_model(db, model_id=db.scalar(select(LlmModel.id)), purpose='assist',
                               messages=[{'role': 'user', 'content': '混入原日期2026年3月5日'}],
                               user_id=owner.id, task_id=task.id)
        assert raised.value.code == 'BLOCKED' and requests == []
        assert db.scalar(select(LlmCall.status).order_by(LlmCall.id.desc()).limit(1)) == 'blocked'


def test_v31q20_another_current_replacement_still_blocks(db_sessionmaker, make_user, ready_task):
    """唯一追加反例：同一文字在本轮另一处仍被替换，不能从复检值中豁免。"""
    from app.db.models import DocNode, RedactionItem, RedactionSuspect
    from app.redaction import engine
    from app.security.crypto import encrypt_text
    word = 'Recarburizer Blend'
    task = ready_task(make_user('current_duplicate'))
    with db_sessionmaker() as db:
        nodes = list(db.scalars(select(DocNode).where(DocNode.task_id == task.id, DocNode.part == 'body').order_by(DocNode.ord)))
        nodes[0].text_enc = nodes[1].text_enc = encrypt_text(word)
        db.add(RedactionSuspect(task_id=task.id, loc=nodes[0].loc, char_start=0, char_end=len(word),
                               reason='capitalized', decision='ignore'))
        db.add(RedactionItem(task_id=task.id, loc=nodes[1].loc, char_start=0, char_end=len(word),
                            category='NAME', placeholder='[名称_7]', detected_by='uncertain'))
        engine.write_full_map(db, task.id, {'[名称_7]': word})
        db.flush()
        values = engine.load_real_values(db, task.id)
        assert word in values
        assert any(r.category == 'MAP' for r in recheck(word, real_values=values, terms=[]))


def v351_ocr_sample(case):
    import io
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    graphic = Image.new("RGB", (600, 800), "white")
    pen = ImageDraw.Draw(graphic)
    regular = ImageFont.load_default(size=20)
    pen.text((60, 50), "FICTIONAL CONTRACT - SCAN CORRECTION QA", fill="black", font=regular)
    for x in (50, 312, 560):
        pen.line((x, 265, x, 425), fill="black", width=3)
    for y in (265, 345, 425):
        pen.line((50, y, 560, y), fill="black", width=3)
    for line in case["scanned_lines"]:
        x, y, _, _ = line["bbox"]
        pen.text((x, y - 4), line["text"], fill="black", font=regular)
    pen.line([(163, 386), (174, 370), (192, 398), (211, 374), (228, 392)],
             fill=(220, 220, 220), width=4)
    pdf_bytes = io.BytesIO()
    doc = Canvas(pdf_bytes, pagesize=(600, 800))
    doc.drawString(45, 746, "Contract - Fictional transaction for QA only")
    doc.drawString(45, 717, "Seller: Fictional Heron Machinery Ltd.")
    doc.drawString(45, 688, "Buyer: Fictional River Controls Co., Ltd.")
    doc.drawString(45, 659, "Delivery must follow the quantities specified in the appendix.")
    doc.showPage()
    doc.drawImage(ImageReader(graphic), 0, 0, width=600, height=800)
    doc.showPage()
    doc.save()
    return pdf_bytes.getvalue()


@pytest.mark.parametrize("workspace", [False, True], ids=["legacy-review", "session-review"])
def test_v351_ocr_correction_before_first_review(workspace, db_sessionmaker, make_user, login, llm, ready_task, monkeypatch):
    import hashlib
    import uuid
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    import yaml
    from app.db import jobs
    from app.db.models import AuditLog, DocNode, Job, LlmCall, RedactionItem, ReviewRun, TaskFile
    from app.parsing import ocr
    from app.redaction import engine, ner
    from app.security import crypto
    from app.worker import process_job

    case = yaml.safe_load((Path(__file__).parent / "fixtures/ocr_eval/v351_correction.yaml").read_text(encoding="utf-8"))
    pdf_bytes = v351_ocr_sample(case)
    monkeypatch.setattr(ner, "recognize", lambda text: [])
    monkeypatch.setattr(ocr, "recognize", lambda img: [
        ocr.OcrLine(line["initial"], [value * img.width / 600 for value in line["bbox"]], line["confidence"])
        for line in case["scanned_lines"]
    ])
    requests = llm()
    filename = "虚构扫描校订.pdf"
    owner = make_user("v351_ocr_owner")
    client, token = login(owner.username)
    headers = {"X-CSRF-Token": token}
    outsider = make_user("v351_ocr_outsider")
    other, other_token = login(outsider.username)
    base = "/api/v1"
    if workspace:
        made = client.post(f"{base}/assistant/sessions", headers=headers, json={"purpose": "chat", "review_intent": True})
        assert made.status_code == 201, made.text
        task_id = uuid.UUID(made.json()["id"])
        root = f"{base}/assistant/sessions/{task_id}"
        upload = client.post(root + "/files", headers=headers, files={"files": (filename, pdf_bytes)})
        assert upload.status_code == 200, upload.text
        with db_sessionmaker() as db:
            claimed = jobs.claim(db, "v351-ocr-parse", ("parse",))
            assert claimed is not None
            parse_job = (claimed.id, claimed.type, claimed.payload)
        process_job(db_sessionmaker, *parse_job, heartbeat_seconds=0.05)
        preview_url = root + "/outbound?mode=review&include_pending=true&stance=sales"
        start = {"stance": "sales", "source": "outgoing", "confirm_pending": True}
    else:
        task_id = ready_task(owner, [(filename, pdf_bytes)]).id
        root = f"{base}/tasks/{task_id}"
        preview_url = root + "/redaction/outbound"
        start = {}
        with db_sessionmaker() as db:
            db.get(Task, task_id).created_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
            db.commit()

    session = client.get(root).json()
    assert session["status"] == "awaiting_confirm", session
    fid = session["files"][0]["id"]
    fpath = f"{root}/files/{fid}"
    nodes = client.get(fpath + "/nodes").json()["items"]
    date = next(n for n in nodes if "2026-11-28" in n["text"])
    contact = next(n for n in nodes if n["text"] == "13800138OO1")
    masked = case["user_masked"]
    courier = next(n for n in nodes if masked["initial"] in n["text"])
    unclear = next(n for n in nodes if case["manual_check"] in n["text"])
    digital = next(n for n in nodes if n["page"] == 1)
    assert date["kind"] == "ocr_block" and date["ocr_editable"]
    assert contact["kind"] == "table_cell" and contact["ocr_editable"]
    assert unclear["kind"] == "table_cell" and not unclear["ocr_editable"]
    assert not digital["ocr_editable"]
    assert client.get(fpath + "/raw").content == pdf_bytes

    def edit(node, text, version):
        return client.patch(f"{fpath}/ocr/{node['loc']}", headers=headers,
                            json={"text": text, "expected_material_version": version})

    mask = client.post(f"{base}/tasks/{task_id}/redaction/manual", headers=headers, json={
        "loc": courier["loc"], "char_start": courier["text"].index(masked["initial"]),
        "char_end": courier["text"].index(masked["initial"]) + len(masked["initial"]), "category": "CUSTOM"})
    assert mask.status_code == 200, mask.text
    initial = client.get(preview_url).json()
    prev = initial["material_version"]
    assert edit(digital, "不能改数字文字层", prev).status_code == 422
    assert edit(unclear, "虚构猜测签名", prev).status_code == 422
    denied = other.patch(f"{fpath}/ocr/{date['loc']}", headers={"X-CSRF-Token": other_token},
                         json={"text": "不属于本人的 OCR", "expected_material_version": prev})
    assert denied.status_code == 404

    correct_date = edit(date, case["scanned_lines"][0]["text"], prev)
    assert correct_date.status_code == 200, correct_date.text
    revised = correct_date.json()["material_version"]
    assert revised > prev
    stale = edit(contact, "13800138001", prev)
    assert stale.status_code == 409 and stale.json()["code"] == "MATERIAL_CHANGED"
    correct_contact = edit(contact, case["scanned_lines"][3]["text"], revised)
    assert correct_contact.status_code == 200, correct_contact.text
    fixed = courier["text"].replace(masked["initial"], masked["text"])
    current_version = client.get(preview_url).json()["material_version"]
    correct_courier = edit(courier, fixed, current_version)
    assert correct_courier.status_code == 200, correct_courier.text
    final_nodes = {node["loc"]: node for node in client.get(fpath + "/nodes").json()["items"]}
    for old, value in ((date, "Signed on 2026-11-26"), (contact, "13800138001"), (courier, fixed)):
        corrected = final_nodes[old["loc"]]
        assert corrected["text"] == value and corrected["ocr_corrected"]
        assert (corrected["page"], corrected["bbox"]) == (old["page"], old["bbox"])
    assert client.get(fpath + "/raw").content == pdf_bytes
    with db_sessionmaker() as db:
        assert db.get(TaskFile, uuid.UUID(fid)).sha256 == hashlib.sha256(pdf_bytes).hexdigest()
        for old in (date, contact, courier):
            stored = db.scalar(select(DocNode).where(DocNode.task_id == task_id, DocNode.loc == old["loc"]))
            assert old["text"].encode() not in stored.ocr_original_enc
            assert crypto.decrypt_text(stored.ocr_original_enc) == old["text"]
        kept = db.scalars(select(RedactionItem).where(RedactionItem.task_id == task_id,
            RedactionItem.loc == courier["loc"], RedactionItem.detected_by == "user")).all()
        assert [fixed[r.char_start:r.char_end] for r in kept] == [masked["text"]]
        audit = db.scalars(select(AuditLog).where(AuditLog.action == "ocr_correction")).all()
        assert len(audit) == 3 and all("13800138001" not in str(entry.detail) for entry in audit)
        assert not db.scalar(select(LlmCall.id))
        assert db.get(TaskFile, uuid.UUID(fid)).confirmed_at is None
        # 明确为 PDF 中可编辑的两个 OCR 节点。
        assert "13800138001" in engine.load_real_values(db, task_id)

    current = client.get(preview_url).json()
    assert current["material_version"] > initial["material_version"]
    assert "2026-11-28" not in current["text"] and "13800138001" not in current["text"]
    assert masked["text"] not in current["text"] and masked["initial"] not in current["text"]
    assert "XQ" not in current["text"] and case["manual_check"] in current["text"]
    parties = client.get(f"{base}/tasks/{task_id}/parties").json()["items"]
    assert parties
    changed = client.put(f"{base}/tasks/{task_id}/parties", headers=headers,
        json={"items": [{"id": p["id"], "is_our_party": i == 0, "country": p["country"], "org_type": p["org_type"]}
                        for i, p in enumerate(parties)]})
    assert changed.status_code == 200, changed.text
    runs = root + "/runs"
    stale = client.post(runs, headers=headers, json={**start, "expected_material_version": prev})
    assert stale.status_code == 409 and stale.json()["code"] == "MATERIAL_CHANGED"
    missing = client.post(runs, headers=headers, json=start)
    assert missing.status_code == 409 and missing.json()["code"] == "MATERIAL_CHANGED"
    assert requests == []
    fresh = client.get(preview_url).json()["material_version"]
    started = client.post(runs, headers=headers, json={**start, "expected_material_version": fresh})
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    with db_sessionmaker() as db:
        job = db.scalar(select(Job).where(Job.type == "review", Job.payload["run_id"].astext == run_id))
        assert job is not None and db.get(TaskFile, uuid.UUID(fid)).confirmed_at is not None
        job_id, job_type, payload = job.id, job.type, job.payload
    process_job(db_sessionmaker, job_id, job_type, payload, heartbeat_seconds=0.05)
    with db_sessionmaker() as db:
        assert db.get(ReviewRun, uuid.UUID(run_id)).status == "completed"
    records = outbound_records(db_sessionmaker, requests)
    assert len(records) == len(requests) > 0
    assert any(case["safe_excerpt"] in record.wire_text and case["manual_check"] in record.wire_text for record in records)
    for record in records:
        for sent in (record.request_text, record.wire_text):
            for excluded in ("13800138001", "13800138OO1", "2026-11-28", "XQ", masked["text"], masked["initial"]):
                assert excluded not in sent
            assert not recheck(sent, real_values=record.real_values, terms=record.terms)
    report = client.get(f"{base}/runs/{run_id}")
    assert report.status_code == 200
    assert edit(date, "Signed on 2026-12-01", fresh).status_code == 409
    assert client.get(f"{base}/runs/{run_id}").json() == report.json()
    if not workspace:
        from app.admin.settings_store import set_setting
        from app.backup.retention import run_retention
        from app.db.base import utcnow
        from app.security.rotate import encrypted_columns

        reopened = ready_task(owner, [(filename, client.get(fpath + "/raw").content)])
        reopen_root = f"{base}/tasks/{reopened.id}"
        reopen_fid = client.get(reopen_root).json()["files"][0]["id"]
        reopen_file = f"{reopen_root}/files/{reopen_fid}"
        assert any(n["loc"] == date["loc"] and n["text"] == date["text"]
                   for n in client.get(reopen_file + "/nodes").json()["items"])
        reopen_preview = client.get(reopen_root + "/redaction/outbound").json()["material_version"]
        newer = client.patch(f"{reopen_file}/ocr/{date['loc']}", headers=headers,
                             json={"text": "Signed on 2026-11-26", "expected_material_version": reopen_preview})
        assert newer.status_code == 200, newer.text
        assert client.get(f"{base}/runs/{run_id}").json() == report.json()
        assert DocNode.__table__.c.ocr_original_enc in encrypted_columns()
        with db_sessionmaker() as db:
            db.get(Task, task_id).last_activity_at = utcnow() - timedelta(days=3)
            set_setting(db, "retention.original_days", 1, None)
            db.commit()
            assert run_retention(db)["tasks"] == 1
            assert not db.scalar(select(DocNode.id).where(DocNode.task_id == task_id, DocNode.ocr_original_enc.is_not(None)))
        assert client.get(fpath + "/raw").status_code == 404
