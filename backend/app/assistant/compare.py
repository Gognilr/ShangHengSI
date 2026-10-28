"""模型只对齐价格代号；持久化结构不含还原价格，查看时用 Decimal 现场计算。"""
import json
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.checks.amounts import _CURRENCY_TOKEN, parse_number
from app.redaction.detect import _CURRENCY
from app.redaction.restore import restore

NOTICE = "价格由本地从文件中还原计算，未发送给模型"


def normalize(payload, mapping: dict[str, str], file_seqs: list[int]) -> dict:
    data = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict) or not isinstance(data.get("suppliers"), list) or not isinstance(data.get("rows"), list):
        raise ValueError("报价结构不正确")
    selected = set(file_seqs)
    suppliers, seen = [], set()

    def file_number(item):
        number = item.get("file") if isinstance(item, dict) else None
        if type(number) is not int or number not in selected:
            raise ValueError("报价文件不正确")
        return number

    def text(value):
        return value if isinstance(value, str) else ""

    def code(value, prefix):
        return value if isinstance(value, str) and re.fullmatch(r"\[\[" + prefix + r"_\d+\]\]", value) and value in mapping else None

    for supplier in data["suppliers"]:
        seq = file_number(supplier)
        if seq in seen:
            raise ValueError("报价文件重复")
        seen.add(seq)
        name = supplier.get("name")
        if not isinstance(name, str) or name not in mapping or not re.fullmatch(r"\[\[(?:OUR|ORG_\d+)\]\]", name):
            name = f"文件 {seq}"
        currency = text(supplier.get("currency")).upper()
        suppliers.append({"file": seq, "name": name, "currency": currency if currency in set(_CURRENCY.values()) else None,
                          "terms": text(supplier.get("terms"))})
    if seen != selected or not data["rows"]:
        raise ValueError("报价文件或项目缺失")
    rows = []
    for row in data["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("quotes"), list):
            raise ValueError("报价行不正确")
        quotes, seen = [], set()
        for quote in row["quotes"]:
            seq = file_number(quote)
            if seq in seen:
                raise ValueError("报价单元格重复")
            seen.add(seq)
            quotes.append({"file": seq, "qty": text(quote.get("qty")), "unit_price": code(quote.get("unit_price"), "PRICE"),
                           "amount": code(quote.get("amount"), "AMT")})
        rows.append({"item": text(row.get("item")), "unit": text(row.get("unit")), "quotes": quotes})
    return {"suppliers": suppliers, "rows": rows, "notes": text(data.get("notes"))}


def _money(code, mapping, fallback):
    if code not in mapping:
        return None, None
    value = mapping[code]
    parsed = parse_number(value)
    token = _CURRENCY_TOKEN.search(value)
    currency = _CURRENCY.get(token.group().upper()) if token else fallback
    return parsed, currency


def calculate(data: dict, mapping: dict[str, str]) -> dict:
    suppliers = {s["file"]: s for s in data["suppliers"]}
    totals = {seq: {"amount": Decimal(0), "currency": None, "status": "完整"} for seq in suppliers}
    rows = []
    for row in data["rows"]:
        quotes, present, groups = [], set(), {}
        for source in row["quotes"]:
            seq = source["file"]
            present.add(seq)
            price, currency = _money(source["unit_price"], mapping, suppliers[seq]["currency"])
            amount, amount_currency = _money(source["amount"], mapping, suppliers[seq]["currency"])
            valid = price is not None and amount is not None and currency is not None and currency == amount_currency
            quote = {**source, "qty": restore(source["qty"], mapping), "unit_price": price, "amount": amount,
                     "currency": currency, "difference_pct": None, "status": "可核对" if valid else "无法核对"}
            quotes.append(quote)
            total = totals[seq]
            if valid:
                groups.setdefault(currency, []).append(quote)
                if total["currency"] not in (None, amount_currency):
                    total["status"] = "不完整"
                total["currency"] = amount_currency
                with localcontext() as ctx:
                    ctx.prec = max(28, max(total["amount"].adjusted(), amount.adjusted()) -
                                   min(total["amount"].as_tuple().exponent, amount.as_tuple().exponent) + 4)
                    total["amount"] += amount
            else:
                total["status"] = "不完整"
        for missing in set(suppliers) - present:
            totals[missing]["status"] = "不完整"
        minimum_files = []
        complete = present == set(suppliers) and all(q["status"] == "可核对" for q in quotes)
        for currency, group in groups.items():
            minimum = min(q["unit_price"] for q in group)
            if len(groups) == 1 and complete:
                minimum_files = [q["file"] for q in group if q["unit_price"] == minimum]
            if minimum != 0 and len(group) >= 2 and complete:
                for quote in group:
                    price = quote["unit_price"]
                    with localcontext() as ctx:
                        ctx.prec = max(28, len(price.as_tuple().digits) + len(minimum.as_tuple().digits) +
                                       abs(price.adjusted() - minimum.adjusted()) + 8)
                        quote["difference_pct"] = ((price - minimum) / minimum * Decimal(100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        rows.append({"item": restore(row["item"], mapping), "unit": restore(row["unit"], mapping), "quotes": quotes,
                     "minimum_files": minimum_files, "status": "币种不同" if len(groups) > 1 else "可核对" if complete else "部分无法核对"})
    for total in totals.values():
        if total["status"] == "不完整":
            total["amount"] = None
    return {"notice": NOTICE, "suppliers": [{**s, "name": restore(s["name"], mapping), "terms": restore(s["terms"], mapping)}
                                          for s in suppliers.values()],
            "rows": rows, "totals": totals, "notes": restore(data["notes"], mapping)}
