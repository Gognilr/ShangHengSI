"""invoice 任务：逐个文件识别，合并核对后加密写回内存盘（设计第三、五节）。

在解析组里取任务；每个文件持 worker.ocr_slot()（pdfium 与 OCR 引擎不是线程安全的，且同一时间最多 1 个 OCR）。
批次中途被清除（目录不在）时立即停止，不写结果。日志只记批次 id、文件数、张数、耗时与失败类别。
"""
import logging
import time

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import Job
from app.invoice import checks, recognize, service, store
from app.invoice.intake import MAX_OCR_PAGES
from app.invoice.model import Unrecognized
from app.worker import job_handler, ocr_slot, register_periodic

log = logging.getLogger(__name__)
MSG_INTERNAL = "识别时出错，请改传 PDF 或重新拍摄"


def _progress(db: Session, bid: str, done: int, total: int) -> None:
    db.execute(update(Job).where(Job.type == "invoice", Job.payload["batch_id"].astext == bid)
               .values(progress={"done": done, "total": total}))
    db.commit()


@job_handler("invoice")
def handle(db: Session, payload: dict) -> None:
    bid = str(payload["batch_id"])
    manifest = store.load_manifest(bid)
    if manifest is None:  # 已清除
        return
    started = time.monotonic()
    files = manifest["files"]
    records, failed = [], [Unrecognized(s["name"], s["reason"]) for s in manifest["skipped"]]
    budget = MAX_OCR_PAGES
    errors = 0
    for i, meta in enumerate(files):
        data = store.load_file(bid, i)
        if data is None:
            return
        try:
            with ocr_slot():
                res = recognize.recognize(meta["name"], meta["ext"], data, ocr_budget=budget)
        except Exception as exc:  # noqa: BLE001 —— 单个文件失败不拖垮整批，只记类别
            errors += 1
            log.warning("发票批次 %s 第 %s 个文件识别失败: %s", bid, i + 1, type(exc).__name__)
            failed.append(Unrecognized(meta["name"], MSG_INTERNAL))
            res = None
        if res is not None:
            budget -= res.ocr_pages
            records += res.invoices
            failed += [Unrecognized(f"{meta['name']} {where}".strip(), reason) for where, reason in res.failures]
        _progress(db, bid, i + 1, len(files))
    rows = checks.finalize(records)
    result = {"invoices": [r.to_json() for r in rows], "unrecognized": [{"name": u.name, "reason": u.reason} for u in failed]}
    if store.save_result(bid, result):
        store.drop_originals(bid)
    log.info("发票批次 %s 识别完成：文件 %s 个，发票 %s 张，未识别 %s 项，出错 %s 个，耗时 %.1fs",
             bid, len(files), len(rows), len(failed), errors, time.monotonic() - started)


register_periodic("invoice_sweep", service.sweep, 60)
