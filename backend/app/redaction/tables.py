"""表格工具（设计 docs/design/M4.md 2.4）：表格单元格位置编号、表头列提示。

从 engine.py 移入，行为不变（M2-08、Q-65）。tables.py 不导入 engine，节点只按属性（loc、text）使用。
R1-03（design/R1.md 第三节，Q-134 ③）：一个表格可以有多个表头行；“合计、小计、总计”列名算金额列；
合计行的纯数字一律按金额处理。
"""
import re
from typing import Callable, Protocol

from app.redaction.detect import _NUM, amount_label_kind, numeric_cell_view

_TABLE_LOC = re.compile(r"^(?P<table>.+-T\d+)-R(?P<row>\d+)-C(?P<col>\d+)$")  # DOCX、PDF 表格单元格
_SHEET_LOC = re.compile(r"^(?P<table>F\d+-S\d+)-(?P<col>[A-Z]+)(?P<row>\d+)$")  # XLSX 单元格，整个工作表为一个表格
HEADER_SCAN_ROWS = 20  # 第一个表头行只在每个表格的前 20 行中找（open_questions Q-65）
_PRICE_HEADER = re.compile(r"单价|unit\s*price|price", re.IGNORECASE)
_AMOUNT_HEADER = re.compile(r"金额|总价|总额|合计|小计|总计|期末余额|期初余额|年初余额|本年累计|上年同期|期末数|年初数|amount|total", re.IGNORECASE)
_PERIOD_HEADER = re.compile(r'本期|上期|本年|上年|本月|期初|期末|年初|累计|current\s+period|prior\s+period|year-end', re.I)
_EXCLUDED_HEADER = re.compile(r'数量|比例|率|%|％|行次|序号|附注|天数|人数|quantity|ratio|rate|index|note|days|headcount', re.I)
_CURRENCY_DECLARATION = re.compile(r'单位\s*[:：]?\s*(?:万?元|人民币)|人民币|币种|(?<![A-Za-z])(?:USD|EUR|CNY|RMB|GBP|JPY|HKD)(?![A-Za-z])', re.I)
_QTY_HEADER = re.compile(r"数量|quantity|qty", re.IGNORECASE)
_TOTAL_ROW = re.compile(r"合计|小计|总计|(?<![A-Za-z])(?:sub\s*)?total(?![A-Za-z])", re.IGNORECASE)
_BARE_NUMBER = re.compile(rf'^\s*[-−－(（]?\s*(?:{_NUM})\s*[)）]?\s*$')
_INFORMATION_HEADERS = {
    name: (label, category)
    for label, category, names in (
        ('联系人', 'PERSON', '姓名 人员姓名 员工姓名 职工姓名 参保人姓名 参保人员姓名 被保险人姓名 联系人 联系人姓名 项目负责人 法定代表人姓名'),
        ('单位名称', 'ORG', '单位名称 客户 客户名称 客户单位 客户单位名称 业主 业主名称 业主单位 业主单位名称 建设单位 采购单位 用人单位 工作单位 聘用单位 参保单位 缴费单位'),
        ('开户行', 'ORG', '开户行 开户行名称 开户银行 开户银行名称 收款银行'),
        ('身份证号', 'IDNO', '身份证号 身份证号码 身份证 公民身份号码 居民身份证号码'),
        ('证件号', 'IDNO', '证件号 证件号码 社会保障号码'),
        ('电话', 'PHONE', '电话 联系电话 联系人电话 手机 手机号 手机号码 移动电话 固定电话 办公电话 传真 传真号码'),
    )
    for name in names.split()
}
_OTHER_COLUMNS = frozenset(
    '序号 项目 名称 类别 币种 备注 性别 年龄 部门 岗位 职务 职称 工种 人数 起止时间 入职日期 '
    '缴费年月 缴费月份 缴费基数 缴费状态 险种 项目名称 工程名称 合同名称 合同金额 合同日期 签订日期 '
    '完成日期 合同工期 履约情况 业绩情况 数量 单位 规格型号'.split()
)
_EMPTY_VALUES = frozenset({'-', '--', '—', '/', '无', '暂无', '同上', '同前', '待定', '未填写', '不适用', 'n/a'})


def _numeric_cell(text: str) -> bool:
    folded = numeric_cell_view(text)
    return bool(_BARE_NUMBER.fullmatch(folded if folded is not None else text))


class _Node(Protocol):
    loc: str
    text: str


def table_cell(loc: str) -> tuple[str, str, int] | None:
    """表格单元格的 (表格标识, 列, 行号)；不是表格单元格时 None。"""
    m = _TABLE_LOC.match(loc) or _SHEET_LOC.match(loc)
    return (m["table"], m["col"], int(m["row"])) if m else None


def header_kind(text: str) -> str | None:
    """表头文字的类别：PRICE（单价）、AMOUNT（金额）、QTY（数量）；都不是时 None。单价、金额优先于数量。"""
    if _PRICE_HEADER.search(text):
        return "PRICE"
    if _AMOUNT_HEADER.search(text):
        return "AMOUNT"
    if _QTY_HEADER.search(text):
        return "QTY"
    if _PERIOD_HEADER.search(text) and not _EXCLUDED_HEADER.search(text):
        return 'AMOUNT'
    return None


def _rows(nodes: list[_Node]) -> dict[str, dict[int, list[tuple[int, str]]]]:
    """{表格标识: {行号: [(节点下标, 列)]}}。"""
    out: dict[str, dict[int, list[tuple[int, str]]]] = {}
    for i, n in enumerate(nodes):
        cell = table_cell(n.loc)
        if cell is not None:
            table, col, row = cell
            out.setdefault(table, {}).setdefault(row, []).append((i, col))
    return out


def _is_header(nodes: list[_Node], cells: list[tuple[int, str]]) -> bool:
    """表头行：有单价或金额表头，且没有纯数字单元格（“合计金额 | 222063”这样的合计行不是表头）。"""
    texts = [nodes[i].text for i, _ in cells]
    return (any(header_kind(t) in ("PRICE", "AMOUNT") for t in texts)
            and not any(_numeric_cell(t) for t in texts))


def _header_rows_all(nodes: list[_Node], rows: dict[int, list[tuple[int, str]]]) -> list[int]:
    """一个表格的全部表头行（升序）：第一个限前 20 行（Q-65），之后的不限行号。"""
    found = sorted(r for r, cells in rows.items() if _is_header(nodes, cells))
    if not found or found[0] > HEADER_SCAN_ROWS:
        return []
    return found


def header_rows(nodes: list[_Node]) -> dict[str, int]:
    """{表格标识: 第一个表头行号}。报价明细表汇总（9.7）按第一个表头行确定范围（design/R1.md 第三节第 4 条）。"""
    out: dict[str, int] = {}
    for table, rows in _rows(nodes).items():
        found = _header_rows_all(nodes, rows)
        if found:
            out[table] = found[0]
    return out


def _is_total_row(nodes: list[_Node], cells: list[tuple[int, str]]) -> bool:
    """合计行：行内第一个非空单元格含“合计、小计、总计、Total、Subtotal”。"""
    first = next((nodes[i].text for i, _ in sorted(cells, key=lambda c: _col_order(c[1])) if nodes[i].text.strip()), "")
    return bool(_TOTAL_ROW.search(first))


def _col_order(col: str) -> tuple[int, str]:
    return (len(col), col) if col.isalpha() else (int(col), "")


def _col_number(col: str) -> int:
    if col.isdigit():
        return int(col)
    value = 0
    for c in col:
        value = value * 26 + ord(c) - ord("A") + 1
    return value


def information_category(label: str) -> str | None:
    """敏感列名或标签的实体类别（PERSON、ORG、IDNO、PHONE）；不是时 None。"""
    return _INFORMATION_HEADERS.get(_cell_norm(label).rstrip(':：'), (None, None))[1]


def information_slots(
    nodes: list[_Node], complete_label: Callable[[str], bool], has_label: Callable[[str], bool],
) -> dict[int, tuple[str, str | None]]:
    """给表格值借用左邻标签或本列列头。类别用于限制新增列头所提供的证据。"""
    result = {}
    for rows in _rows(nodes).values():
        headers = {}
        for row in sorted(rows):
            cells = rows[row]
            columns = {_col_number(col): i for i, col in cells}
            values = {col: nodes[i].text.strip() for col, i in columns.items() if nodes[i].text.strip()}
            labels = {col: text for col, text in values.items() if complete_label(text)}
            typed = {col: _INFORMATION_HEADERS[key] for col, text in values.items()
                     if (key := _cell_norm(text).rstrip(':：')) in _INFORMATION_HEADERS}
            known = {col for col, text in values.items() if col in labels or col in typed or text in _OTHER_COLUMNS}
            # 有未识别列名时，须有两个敏感列名且多数格为列名，避免把横向填值行当成表头。
            # “姓名 | 张三 | 电话 |（空）”这样列名与值交替的行是横向填值，不是表头。
            order = [col in known for col in sorted(values)]
            alternating = bool(order) and order[0] and all(not (a and b) for a, b in zip(order, order[1:]))
            mixed = (len(typed) >= 2 and len(known) * 2 > len(values) and not alternating
                     and not any(_numeric_cell(t) for t in values.values()))
            total = _is_total_row(nodes, cells) if headers or typed else False
            if not total and ((known and len(known) == len(values) and (headers or labels or typed)) or mixed):
                headers = {col: (label, None) for col, label in labels.items()}
                headers.update(typed)
                continue
            for col, text in values.items():
                i = columns[col]
                if col in labels or has_label(text):
                    continue
                if col - 1 in labels:
                    result[i] = (labels[col - 1], None)
                elif col in headers:
                    label, category = headers[col]
                    if category is None or (not total and text.casefold() not in _EMPTY_VALUES):
                        result[i] = (label, category)
                elif col - 1 in typed:
                    result[i] = typed[col - 1]
    return result


def table_hints(nodes: list[_Node], *, with_qty: bool = False) -> dict[int, str]:
    """表格列提示（9.3 第 5 步，Q-65、Q-134 ③）：表头含“单价、Unit Price”的列为 PRICE，含“金额、总价、合计、Amount”的列为 AMOUNT；
    with_qty=True 时含“数量、Quantity、Qty”的列为 QTY（数量脱敏开启时用）。

    每个表头行的列提示管到下一个表头行为止；表头行本身和第一个表头行之上的单元格没有提示。
    合计行中的纯数字单元格一律为 AMOUNT（宁可多替换）。返回 {节点下标: 类别}。
    """
    out: dict[int, str] = {}
    for table, rows in _rows(nodes).items():
        headers = _header_rows_all(nodes, rows)
        header_set = set(headers)
        cols: dict[str, str] = {}
        current = None
        first_ten = [i for row, cells in rows.items() if row <= 10 for i, _ in cells]
        first_index = min((i for cells in rows.values() for i, _ in cells), default=0)
        title = nodes[first_index - 1].text if first_index and table_cell(nodes[first_index - 1].loc) is None else ''
        declared = bool(_CURRENCY_DECLARATION.search(title + ' ' + ' '.join(nodes[i].text for i in first_ten)))
        excluded_cols = {col for row, cells in rows.items() if row <= 10
                         for i, col in cells if _EXCLUDED_HEADER.search(nodes[i].text)}
        for row in sorted(rows):
            cells = rows[row]
            if row in header_set:
                current = row
                cols = {}
                for i, col in cells:
                    kind = header_kind(nodes[i].text)
                    if kind and (kind != "QTY" or with_qty):
                        cols[col] = kind
                continue
            if current is not None:
                for i, col in cells:
                    if col in cols:
                        out[i] = cols[col]
            if declared:
                for i, col in cells:
                    if col not in excluded_cols and _numeric_cell(nodes[i].text):
                        out.setdefault(i, 'AMOUNT')
            if _is_total_row(nodes, cells):
                for i, _col in cells:
                    if _numeric_cell(nodes[i].text):
                        out[i] = "AMOUNT"
        # R4：仅认同一行紧邻左格；不跨空列、不覆盖已有表头提示。
        for cells in rows.values():
            by_col = {_col_number(col): i for i, col in cells}
            for col, i in by_col.items():
                left = by_col.get(col - 1)
                if i not in out and left is not None and (_numeric_cell(nodes[i].text) or re.fullmatch(rf"[ \t]*({_NUM})万?[ \t]*", nodes[i].text)):
                    kind = amount_label_kind(nodes[left].text)
                    if kind:
                        out[i] = kind
    return out


_PAGE_TABLE = re.compile(r"^F\d+-PG(?P<page>\d+)-T\d+$")  # PDF 文字页与扫描页的表格


def _cell_norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def continued_tables(nodes) -> list[list[tuple[str, int]]]:
    """跨页续表（V34-D-03）：PDF 中被分页切断的同一张表。返回 [[(表格标识, 去掉的重复表头行数), ...]]，只列两张以上的。

    条件：前表是本页最下面的表、续表是下一页最上面的表；列数相同且各列左边界、表格左右边界对齐（容差 max(6 点, 表宽 2%)）；
    前表前 20 行内有单价或金额表头行；续表开头没有表头，或开头几行与前表表头行逐列相同（这些行记为重复表头，不计入数据行）。
    节点只按 loc、text、bbox 使用，不加字段、不改 loc；没有 bbox 的节点不参与。
    """
    info: dict[str, dict] = {}
    for n in nodes:
        cell = table_cell(n.loc)
        bbox = getattr(n, "bbox", None)
        if cell is None or not bbox or not (m := _PAGE_TABLE.match(cell[0])):
            continue
        t = info.setdefault(cell[0], {"file": cell[0].split("-", 1)[0], "page": int(m["page"]), "rows": {}, "left": {}, "box": list(bbox)})
        t["rows"].setdefault(cell[2], {})[_col_number(cell[1])] = n
        col = _col_number(cell[1])
        t["left"][col] = min(t["left"].get(col, bbox[0]), bbox[0])
        box = t["box"]
        t["box"] = [min(box[0], bbox[0]), min(box[1], bbox[1]), max(box[2], bbox[2]), max(box[3], bbox[3])]
    pages: dict[tuple[str, int], list[str]] = {}
    for name, t in info.items():
        pages.setdefault((t["file"], t["page"]), []).append(name)

    def header_rows(t) -> list[dict[int, str]]:
        rows = t["rows"]
        for r in sorted(rows):
            if r > HEADER_SCAN_ROWS:
                break
            texts = [n.text for n in rows[r].values()]
            if any(header_kind(x) in ("PRICE", "AMOUNT") for x in texts) and not any(_numeric_cell(x) for x in texts):
                # 表头行及其上下各一行（两行表头、标题行）都可能在续页重复出现
                return [{c: _cell_norm(n.text) for c, n in rows[x].items()} for x in sorted(rows) if r - 1 <= x <= r + 1]
        return []

    def continues(a, b, heads) -> int | None:
        if len(a["left"]) < 2 or set(b["left"]) - set(a["left"]) or max(a["left"]) != max(b["left"]):
            return None
        tol = max(6.0, 0.02 * (a["box"][2] - a["box"][0]))
        if abs(a["box"][0] - b["box"][0]) > tol or abs(a["box"][2] - b["box"][2]) > tol:
            return None
        if any(abs(a["left"][c] - b["left"][c]) > tol for c in b["left"]):
            return None
        if not heads:
            return None
        skip = 0
        for r in sorted(b["rows"]):
            texts = {c: _cell_norm(n.text) for c, n in b["rows"][r].items()}
            if texts in heads:
                skip += 1
                continue
            values = list(b["rows"][r].values())
            if any(header_kind(n.text) for n in values) and not any(_numeric_cell(n.text) for n in values) and \
                    not _TOTAL_ROW.search(" ".join(n.text for n in values)):
                return None  # 续页另起一张表（有自己的不同表头）
            break
        return skip if skip < len(b["rows"]) else None

    groups: list[list[tuple[str, int]]] = []
    tail: dict[str, list[tuple[str, int]]] = {}
    for (file, page) in sorted(pages, key=lambda k: (k[0], k[1])):
        nxt = pages.get((file, page + 1))
        if not nxt:
            continue
        last = max(pages[(file, page)], key=lambda x: info[x]["box"][3])
        first = min(nxt, key=lambda x: info[x]["box"][1])
        head = tail[last][0][0] if last in tail else last
        skip = continues(info[last], info[first], header_rows(info[head]))
        if skip is None:
            continue
        group = tail.pop(last, None)
        if group is None:
            group = [(last, 0)]
            groups.append(group)
        group.append((first, skip))
        tail[first] = group
    return groups
