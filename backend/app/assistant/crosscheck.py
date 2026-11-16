"""单证核对（V34-A-08）：本机抽取各单证的关键项目，按归一化值逐项比较，结果表由本机生成。

模型只在某份文件本机找不到某个项目时，给出该项目在文件中的位置与原样文字（代号形式），不给结论；
真实值只在内存与加密的 local_result_enc 中出现，不写日志。
"""
import json
import re
import unicodedata
from decimal import Decimal

from app.checks.consistency import check_cross_files
from app.checks.model import CheckFile, CheckNode
from app.checks.numbers import parse_number
from app.redaction import parties as P
from app.redaction.dates import find_dates
from app.redaction.detect import find_amounts
from app.redaction.tables import table_cell

NOTICE = "本机从文件中识别并比较，金额与名称未发送给模型"

# (键, 显示名, 值的类别, 标签)；顺序即表格的行序。标签只认明确写出的，不猜无标签的数字。
FIELDS = (
    ("goods", "品名", "text", r"品名|货物名称|商品名称|货名|货物描述|description\s+of\s+goods|goods\s+description|commodity"),
    ("quantity", "数量", "measure", r"总数量|数量|quantity|qty"),
    ("gross", "毛重", "weight", r"总毛重|毛重|gross\s*weight|g\.\s*w\."),
    ("net", "净重", "weight", r"总净重|净重|net\s*weight|n\.\s*w\."),
    ("packages", "件数", "count", r"总件数|包装件数|件数|箱数|no\.?\s*of\s*packages|packages|cartons"),
    ("amount", "金额", "money", r"总金额|合同金额|发票金额|总价|金额|total\s*amount|total\s*value|invoice\s*value|amount"),
    ("shipper", "发货人", "text", r"发货人|托运人|shipper|consignor"),
    ("consignee", "收货人", "text", r"收货人|consignee"),
    ("pol", "装运港", "place", r"装运港|装货港|起运港|port\s+of\s+loading"),
    ("pod", "目的港", "place", r"目的港|卸货港|port\s+of\s+(?:discharge|destination)|destination\s+port"),
    ("ship_date", "装运日期", "date", r"装运日期|装船日期|装运期|shipment\s+date|date\s+of\s+shipment|on\s+board\s+date"),
    ("invoice_date", "发票日期", "date", r"发票日期|invoice\s+date"),
    ("contract_no", "合同号", "ref", r"合同编号|合同号|contract\s*(?:no\.?|number)"),
    ("invoice_no", "发票号", "ref", r"发票编号|发票号码|发票号|invoice\s*(?:no\.?|number)"),
    ("bl_no", "提单号", "ref", r"提单编号|提单号码|提单号|b/l\s*(?:no\.?|number)"),
    ("lc_no", "信用证号", "ref", r"信用证编号|信用证号码|信用证号|l/c\s*(?:no\.?|number)"),
)
LABELS = {key: label for key, label, _, _ in FIELDS}
_KIND = {key: kind for key, _, kind, _ in FIELDS}
_ANY_LABEL = "|".join(f"(?P<{key}>{pattern})" for key, _, _, pattern in FIELDS)
# 行内写法“标签：值”：标签前为行首或分隔符，值到换行、分号或下一个标签为止
_UNIT_NOTE = r"\s*(?:[（(](?P<unit>[^）)]{1,12})[）)])?"
_INLINE = re.compile(r"(?:^|(?<=[\s,，;；。、(（|]))(?i:" + _ANY_LABEL + r")" + _UNIT_NOTE + r"\s*[:：]\s*", re.MULTILINE)
_HEADER = re.compile(r"\s*(?i:" + _ANY_LABEL + r")" + _UNIT_NOTE + r"\s*[:：]?\s*")
_TOTAL = re.compile(r"合计|总计|\btotal\b", re.I)
_UNITS = {"kg": ("kg", 1), "kgs": ("kg", 1), "千克": ("kg", 1), "公斤": ("kg", 1), "t": ("kg", 1000), "mt": ("kg", 1000),
          "吨": ("kg", 1000), "公吨": ("kg", 1000), "g": ("kg", Decimal("0.001")), "克": ("kg", Decimal("0.001")),
          "pcs": ("pcs", 1), "pc": ("pcs", 1), "pieces": ("pcs", 1), "件": ("pcs", 1), "个": ("pcs", 1), "只": ("pcs", 1),
          "sets": ("set", 1), "set": ("set", 1), "套": ("set", 1), "台": ("set", 1),
          "ctns": ("ctn", 1), "ctn": ("ctn", 1), "cartons": ("ctn", 1), "carton": ("ctn", 1), "箱": ("ctn", 1),
          "pkgs": ("pkg", 1), "packages": ("pkg", 1), "包": ("pkg", 1)}
_MEASURE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Za-z]+|[一-鿿]{1,2})?")


def _clip(text: str) -> str:
    """值的范围：到换行、分号、制表符或连续空格为止，最长 80 字。"""
    return re.split(r"[\n;；\t]| {2,}", text, maxsplit=1)[0].strip().rstrip("，,。.")[:80]


def _plain(text: str) -> str:
    return re.sub(r"[\W_]+", "", P.normalize(text))


def key(kind: str, text: str):
    """归一化值：写法不同的同一金额、重量、日期、单号得到同一个键；无法解析时为 None。"""
    if kind == "money":
        hits = [h for h in find_amounts(text) if h.value is not None]
        if hits:
            return ("money", hits[0].value)
        number = parse_number(text)
        return ("money", number) if number is not None else None
    if kind == "count":  # 件数只比数目：“50 箱”“50 CTNS”“50 件”同为 50
        match = _MEASURE.search(unicodedata.normalize("NFKC", text))
        return ("count", Decimal(match[1].replace(",", ""))) if match else None
    if kind in ("measure", "weight"):
        match = _MEASURE.search(unicodedata.normalize("NFKC", text))
        if not match:
            return None
        value = Decimal(match[1].replace(",", ""))
        unit, factor = _UNITS.get((match[2] or "").casefold(), ((match[2] or "").casefold(), 1))
        if kind == "weight" and unit not in ("kg", ""):
            return ("weight", value, unit)
        return (kind, value * factor, unit if kind == "measure" else "kg")
    if kind == "date":
        hits = find_dates(text)
        return ("date", hits[0].value) if hits else None
    if kind == "ref":
        value = re.sub(r"[\s\-－—_/.#]", "", unicodedata.normalize("NFKC", text)).upper()
        return ("ref", value) if value else None
    if kind == "place":
        head = re.split(r"[,，]", text, maxsplit=1)[0]
        value = re.sub(r"(?:港|port)$", "", _plain(head))
        return ("text", value) if value else None
    value = _plain(text)
    return ("text", value) if value else None


def _same_measure(a, b) -> bool:
    """数量、件数一方没写单位时只比数值。"""
    if a[0] in ("measure",) and b[0] == a[0] and (not a[2] or not b[2]):
        return a[1] == b[1]
    return a == b


def extract(nodes: list[CheckNode]) -> dict[int, dict[str, list[dict]]]:
    """{文件序号: {项目: [{text, loc, key}]}}；同一文件同一项目同值只留第一处。"""
    found: dict[int, dict[str, list[dict]]] = {}

    def add(field, node, text, unit=None):
        text = _clip(text)
        if not text:
            return
        if unit and _KIND[field] in ("measure", "weight", "count") and re.fullmatch(r"[0-9][0-9,.]*", text):
            text += " " + unit.strip()  # “毛重(KGS)：12,500” 的单位写在标签里
        value = key(_KIND[field], text)
        if value is None and _KIND[field] not in ("text", "place"):
            return  # “Amount in words”之类解析不出值的不算该项目
        items = found.setdefault(node.file_seq, {}).setdefault(field, [])
        if value is None or all(not _equal(field, value, item["key"]) for item in items):
            items.append({"text": text, "loc": node.loc, "key": value})

    for node, text in _paired(nodes):
        for match in _INLINE.finditer(text):
            tail = text[match.end():]
            following = _INLINE.search(tail)
            add(_field(match), node, tail[:following.start()] if following else tail, match["unit"])
    # 表头写项目名、合计行（或唯一数据行）写值的表格
    tables: dict[tuple[int, str], dict[int, dict[str, CheckNode]]] = {}
    for node in nodes:
        cell = table_cell(node.loc)
        if cell:
            tables.setdefault((node.file_seq, cell[0]), {}).setdefault(cell[2], {})[cell[1]] = node
    for rows in tables.values():
        for header in sorted(rows)[:3]:
            columns = {col: (_field(m), m["unit"]) for col, n in rows[header].items() if (m := _HEADER.fullmatch(n.text))}
            if len(columns) < 2:
                continue
            body = [r for r in sorted(rows) if r > header]
            totals = [r for r in body if any(_TOTAL.search(n.text) for n in rows[r].values())]
            data = [r for r in body if r not in totals and any(n.text.strip() for n in rows[r].values())]
            target = totals[-1] if totals else data[0] if len(data) == 1 else None
            if target is not None:
                for col, (field, unit) in columns.items():
                    cell = rows[target].get(col)
                    if cell is not None and cell.text.strip() and (field != "goods" or not totals):
                        add(field, cell, cell.text, unit)
            break
    return found


def _field(match) -> str:
    return next(key for key in LABELS if match[key] is not None)


def _paired(nodes: list[CheckNode]):
    """左格写标签、右格写值的表格与“标签一段、值一段”的写法与下一节点合读；不跨文件、不跨表格行。"""
    for i, node in enumerate(nodes):
        text = node.text
        prev = nodes[i - 1] if i else None
        if prev is not None and prev.file_seq == node.file_seq and _HEADER.fullmatch(prev.text) \
                and not _HEADER.fullmatch(text) and not _INLINE.search(text):
            here, there = table_cell(node.loc), table_cell(prev.loc)
            if (here is None and there is None) or (here and there and here[0] == there[0] and here[2] == there[2]):
                text = prev.text.strip().rstrip(":：") + "：" + text
        yield node, text


def _equal(field, a, b) -> bool:
    if a is None or b is None:
        return False
    return _same_measure(a, b) if _KIND[field] == "measure" else a == b


def _cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


def compare(found: dict[int, dict[str, list[dict]]], seqs: list[int]) -> list[dict]:
    rows = []
    for field, label, kind, _ in FIELDS:
        values = {seq: found.get(seq, {}).get(field, []) for seq in seqs}
        present = [seq for seq in seqs if values[seq]]
        if not present:
            continue
        status = _status(field, kind, [values[seq] for seq in present]) if len(present) >= 2 else "仅一份文件有"
        rows.append({"field": field, "label": label, "status": status,
                     "values": {str(seq): [{k: v for k, v in item.items() if k != "key"} for item in values[seq]]
                                for seq in seqs}})
        if field == "amount":
            currencies = {seq: [h.currency for item in values[seq] for h in find_amounts(item["text"])[:1] if h.currency]
                          for seq in present}
            named = [seq for seq in present if currencies[seq]]
            if named:
                distinct = {c for seq in named for c in currencies[seq]}
                rows.append({"field": "currency", "label": "币种",
                             "status": "仅一份文件有" if len(named) < 2 else "一致" if len(distinct) == 1 else "不一致",
                             "values": {str(seq): [{"text": c, "loc": values[seq][0]["loc"]} for c in currencies.get(seq, [])]
                                        for seq in seqs}})
    return rows


def _status(field, kind, groups: list[list[dict]]) -> str:
    keys = [[item["key"] for item in group] for group in groups]
    if any(k is None for group in keys for k in group):
        return "无法比较"
    if any(len(group) > 1 for group in keys):
        # 每份文件的值集合相同才算一致
        same = all(len(group) == len(keys[0]) and all(any(_equal(field, a, b) for b in keys[0]) for a in group)
                   for group in keys)
        return "一致" if same else "文件内有多个值"
    first = keys[0][0]
    if all(_equal(field, group[0], first) for group in keys):
        return "一致"
    texts = [group[0]["text"] for group in groups]
    if kind in ("text", "place") and len({_cjk(t) for t in texts}) > 1:
        return "写法不同"  # 中英文写法不同，本机无法判断是否同一对象
    return "不一致"


def missing(rows: list[dict], seqs: list[int]) -> dict[str, list[int]]:
    """本机有至少一份文件识别到、另一些文件没找到的项目：只这些请模型定位。"""
    out = {}
    for row in rows:
        if row["field"] == "currency":
            continue
        absent = [seq for seq in seqs if not row["values"][str(seq)]]
        if absent and len(absent) < len(seqs):
            out[row["field"]] = absent
    return out


def request_text(gaps: dict[str, list[int]]) -> str:
    return "【待定位项目】\n" + "\n".join(f"{LABELS[field]}：文件 {'、'.join(map(str, seqs))}" for field, seqs in gaps.items())


def merge_located(rows, gaps, payload, redacted: dict[str, tuple[int, str]], restore_text) -> int:
    """把模型定位的文字并入结果表并本机重判；位置与原样文字核对不上的一律丢弃。返回并入条数。"""
    data = json.loads(payload) if isinstance(payload, str) else payload
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("定位结构不正确")
    names = {label: field for field, label in LABELS.items()}
    by_field = {row["field"]: row for row in rows}
    added = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        field = names.get(item.get("field"), item.get("field"))
        seq, loc, text = item.get("file"), str(item.get("loc") or "").strip("⟦⟧[] "), item.get("text")
        if field not in gaps or type(seq) is not int or seq not in gaps[field] or not isinstance(text, str):
            continue
        node = redacted.get(loc)
        squash = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
        if node is None or node[0] != seq or not text.strip() or squash(text) not in squash(node[1]):
            continue
        cell = by_field[field]["values"][str(seq)]
        if not cell:
            cell.append({"text": _clip(restore_text(text)), "loc": loc, "by": "model"})
            added += 1
    for row in rows:
        if row["field"] in gaps and any(v.get("by") == "model" for vs in row["values"].values() for v in vs):
            groups = [[{**v, "key": key(_KIND[row["field"]], v["text"])} for v in vs] for vs in row["values"].values() if vs]
            row["status"] = _status(row["field"], _KIND[row["field"]], groups) if len(groups) >= 2 else "仅一份文件有"
    return added


def build(nodes: list[CheckNode], files: list[CheckFile]) -> dict:
    seqs = [f.seq for f in files]
    rows = compare(extract(nodes), seqs)
    # 跨文件的收款账号、签约方名称、合同号与同一品名单价数量（L-17～L-19）
    findings = [r.detail for r in check_cross_files(nodes, files)]
    return {"files": seqs, "rows": rows, "findings": findings, "notes": []}


def summary(result: dict) -> str:
    """消息正文：只有计数，不含任何值。"""
    counts = {}
    for row in result["rows"]:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    parts = [f"{status} {n}" for status, n in counts.items()]
    return f"本机核对 {len(result['rows'])} 项" + ("：" + "，".join(parts) if parts else "") + (
        f"；另有 {len(result['findings'])} 条跨文件提示" if result["findings"] else "")


def local_result(db, task, files) -> tuple[dict, dict[str, tuple[int, str]]]:
    """所选文件的本机结果，以及位置 → (文件序号, 代号正文)，供核对模型定位。"""
    from sqlalchemy import select
    from app.db.models import DocNode
    from app.security import crypto
    seqs = {f.id: f.seq for f in files}
    rows = list(db.scalars(select(DocNode).where(DocNode.task_id == task.id, DocNode.file_id.in_(list(seqs)),
                                                 DocNode.part == "body").order_by(DocNode.ord)))
    rows.sort(key=lambda n: (seqs[n.file_id], n.ord))
    nodes = [CheckNode(n.loc, crypto.decrypt_text(n.text_enc) if n.text_enc else "", seqs[n.file_id], n.clause_no)
             for n in rows]
    checks = [CheckFile(f.seq, f.is_main, f.ext, f.doc_type, []) for f in files]
    return build(nodes, checks), {n.loc: (seqs[n.file_id], n.redacted_text or "") for n in rows}


def view(db, task, row) -> dict | None:
    """查看时补上文件名与可读位置；结构本身加密存放在 local_result_enc。"""
    from sqlalchemy import select
    from app.db.models import TaskFile
    from app.security import crypto
    if not row.local_result_enc:
        return None
    data = json.loads(crypto.decrypt_text(row.local_result_enc))
    names = dict(db.execute(select(TaskFile.seq, TaskFile.original_name).where(TaskFile.task_id == task.id)).all())
    from app.assistant.service import _discussion_locations
    places = _discussion_locations(db, task)
    for item in data["rows"]:
        for values in item["values"].values():
            for value in values:
                value["place"] = places.get(value["loc"], value["loc"])
    findings = []
    for text in data["findings"]:
        for loc in sorted(set(re.findall(r"F\d+(?:-[A-Z]{1,2}\d+)+", text)), key=len, reverse=True):
            text = text.replace(loc, places.get(loc, loc))
        findings.append(text)
    return {"notice": NOTICE, "files": [{"file": seq, "name": names.get(seq, f"文件 {seq}")} for seq in data["files"]],
            "rows": data["rows"], "findings": findings, "notes": data["notes"]}
