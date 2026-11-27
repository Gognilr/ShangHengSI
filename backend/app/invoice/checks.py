"""合并同票与四项核对（设计第三节）。提示只写进清单的“核对提示”列，不拦截导出。"""
from app.invoice.model import FIELD_LABELS, OCR_SOURCES, SOURCE_RANK, Invoice, cn_amount

MERGE_FIELDS = ("kind", "code", "date", "buyer_name", "buyer_tax", "seller_name", "seller_tax",
                "amount", "tax", "total", "total_cn", "remark")
KEY_FIELDS = ("number", "date", "total")
VAT_FIELDS = ("amount", "tax", "seller_name", "seller_tax")  # 客票没有这几项
TICKETS = ("铁路电子客票", "航空运输电子客票行程单")


def merge(records: list[Invoice]) -> list[Invoice]:
    """号码相同的视为同一张票，合为一行：逐字段取来源优先级最高的非空值；同一文件同一来源的多页明细接起来。"""
    groups: dict[str, list[Invoice]] = {}
    for r in records:
        groups.setdefault(r.number, []).append(r)
    out = []
    for number, arrived in groups.items():
        rs = sorted(arrived, key=lambda r: SOURCE_RANK.get(r.source, 9))
        base = rs[0]
        inv = Invoice(number=number, source=base.source)
        low = set()
        for key in MERGE_FIELDS:
            for r in rs:
                v = getattr(r, key)
                if v not in (None, ""):
                    setattr(inv, key, v)
                    if key in r.low:
                        low.add(key)
                    break
        if "number" in base.low:
            low.add("number")
        # 明细与税率：取优先级最高、有明细的来源；它的同文件同来源的各页依次接上
        with_items = [r for r in rs if r.items]
        if with_items:
            top = with_items[0]
            same = [r for r in rs if r.source == top.source and r.files == top.files]
            inv.items = [i for r in same for i in r.items]
            inv.rates = list(dict.fromkeys(x for r in same for x in r.rates))
        else:
            inv.rates = list(dict.fromkeys(x for r in rs for x in r.rates))
        inv.files = list(dict.fromkeys(f for r in arrived for f in r.files))  # 文件名按上传顺序
        inv.qr = next((r.qr for r in rs if r.qr is not None), None)
        inv.low = [k for k in FIELD_LABELS if k in low]
        out.append(inv)
    return out


def check(inv: Invoice) -> list[str]:
    hints = []
    if inv.amount is not None and inv.tax is not None and inv.total is not None and inv.amount + inv.tax != inv.total:
        hints.append("价税合计与金额加税额不等")
    if inv.total_cn and inv.total is not None:
        big = cn_amount(inv.total_cn)
        # 大写前没有“负”的红字票按绝对值比较
        if big is None or (big != inv.total and not (big > 0 and big == -inv.total)):
            hints.append("大写与小写金额不一致")
    q = inv.qr
    if q is not None:
        bad = []
        if q.number != inv.number:
            bad.append("号码")
        if q.date and inv.date and q.date != inv.date:
            bad.append("日期")
        if q.amount is not None and q.amount not in [v for v in (inv.total, inv.amount) if v is not None] \
                and -q.amount not in [v for v in (inv.total, inv.amount) if v is not None]:
            bad.append("金额")
        if bad:
            hints.append("二维码与票面不一致：" + "、".join(bad))
    ocr = inv.source in OCR_SOURCES
    # 照片与扫描件没解出二维码时，号码和日期没有第二来源可对，一并请用户核对（V36-I-02 评测：高置信也会读错）
    low = set(inv.low) | ({"number", "date"} if ocr and q is None else set())
    if ocr and low:
        word = "照片识别" if inv.source == "photo" else "扫描识别"
        hints.append(f"{word}，请核对：" + "、".join(FIELD_LABELS[k] for k in FIELD_LABELS if k in low))
    keys = KEY_FIELDS + (() if inv.kind in TICKETS else VAT_FIELDS)
    # 购买方税号：个人抬头的普票本来就没有；只在照片与扫描件里、抬头像单位（5 字以上）或抬头也没读到时提示（V36-I-02 评测）
    if ocr and inv.kind not in TICKETS and (len(inv.buyer_name) >= 5 or not inv.buyer_name):
        keys += ("buyer_tax",)
    missing = [FIELD_LABELS[k] for k in FIELD_LABELS if k in keys and getattr(inv, k) in (None, "")]
    if missing:
        hints.append("未读到：" + "、".join(missing))
    return hints


def finalize(records: list[Invoice]) -> list[Invoice]:
    """合并、核对，按开票日期升序（没有日期的排在最后）。"""
    rows = merge(records)
    for inv in rows:
        inv.hints = check(inv)
    rows.sort(key=lambda r: (r.date or "9999-99-99", r.number))
    return rows
