"""确定性符合项（bid.md 3.1）：大小写、合计、多个总价、超最高限价、异常低价提示、投标有效期、保证金、证书有效期与写给 AI 的指令。

金额、天数与日期都在本机按关键参数比较（Decimal，ROUND_HALF_UP）；复用审核的 L-01、L-02、L-04、L-21。
“本地规则”类要求按文字归到某项检查，每家写一个单元格（bid_cells，source local）：
yes 响应、no 疑似不响应、missing 未找到、unclear 无法本地判断。
"""
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from unicodedata import normalize

from app.checks.amounts import check_uppercase
from app.checks.content import check_content
from app.checks.dates import find_dates, parse_date
from app.checks.model import CheckNode
from app.checks.numbers import CENT, money, rounded
from app.parsing.model import LOW_OCR_CONF
from app.redaction.detect import chinese_upper_value, find_amounts
from app.redaction.tables import table_cell


@dataclass(frozen=True)
class BidNode(CheckNode):
    """审核校验的节点加上标书需要的出处：文件、页码、节点类别与 OCR 置信度。"""
    kind: str = "paragraph"
    file_id: object = None
    page: int | None = None

ARTICLE = "第五十一条"
_VALIDITY = re.compile(r"投标有效期[^。；;\n]{0,30}?(\d{1,4})\s*(个月|日历天|天|日)")
_DEPOSIT = re.compile(r"投标保证金")
_CERT = re.compile(r"有效(?:期|日期)限?[^。；;\n]{0,25}?(?:至|截止(?:日期)?|到期日?)\s*[:：]?\s*")
_NOT_CERT = re.compile(r"投标|报价|响应|合同")
_UPPER = re.compile(r"[零壹贰叁肆伍陆柒捌玖拾佰仟]")
TERM_LABELS = {
    "construction_max": r"(?:总|施工)?工期",
    "delivery_max": r"交货期(?:限)?|交付期(?:限)?|供货期(?:限)?",
    "warranty_min": r"质保期(?:限)?|质量保证期(?:限)?|保修期(?:限)?",
    "defect_min": r"缺陷责任期",
}
_TERM_VALUE = r"([1-9][0-9]{0,3})\s*(日历天|工作日|个月|天|日|月|年)"
_TERM_LINE = re.compile(
    rf"(?:^|[。；;\n])\s*(?:我(?:方|公司)(?:郑重)?承诺\s*[:：]?\s*|投标(?:承诺\s*[:：]?\s*)?)"
    rf"(?P<label>{'|'.join(TERM_LABELS.values())})\s*(?:为|[:：])?\s*(?P<value>{_TERM_VALUE})"
    r"(?=\s*(?:[。；;\n]|$))"
)
_PERFORMANCE = re.compile(r"类似(?:项目|工程|合同)?业绩|类似项目情况")
_PERFORMANCE_TITLE = re.compile(
    r"^(?:[\d一二三四五六七八九十.、()]+)?(?:近年|已完成的)?"
    r"(?:类似(?:项目|工程|合同)?业绩(?:一览|汇总|情况)?表|类似项目情况表)$"
)
_PERFORMANCE_HEADERS = {
    "name": re.compile(r"项目名称|工程名称|合同名称|业绩名称"),
    "date": re.compile(r"(?:合同)?(?:签订|签署|签约)(?:日期|时间)"),
    "amount": re.compile(r"(?:单项)?合同(?:金额|价款|总价)(?:\((?:人民币)?(?:亿元|万元|元)\))?"),
}
_RMB_NUMBER = r"(\d{1,3}(?:,\d{3}){1,4}(?:\.\d{1,4})?|\d{1,15}(?:\.\d{1,4})?)"
_RMB = re.compile(rf"(?:人民币|[¥￥])?\s*{_RMB_NUMBER}\s*(亿元|万元|元)?")
_RMB_SCALE = {"亿元": 100_000_000, "万元": 10_000, "元": 1}
_UPPER_BOUND = r"上限\s*[:：]|[:：]?\s*(?:应|须|必须)?(?:不得超过|不超过|不大于|至多|最长)"
_LOWER_BOUND = r"下限\s*[:：]|[:：]?\s*(?:应|须|必须)?(?:不得少于|不得低于|不少于|不低于|至少|最短)"
_TERM_LIMITS = {
    key: re.compile(
        rf"(?:^|[。；;\n])\s*[★▲]?\s*(?:投标人承诺的|投标人承诺|投标人)?(?:要求的?)?"
        rf"(?:{label})\s*(?:{_UPPER_BOUND if key.endswith('_max') else _LOWER_BOUND})"
        rf"\s*(?P<value>{_TERM_VALUE})\s*(?=[。；;\n]|$)"
    )
    for key, label in TERM_LABELS.items()
}
_SIGNING_WINDOW = re.compile(
    r"(?:合同)?(?:签订|签约|签署)(?:日期|时间)(?:为|须在|应在|在|[:：])\s*"
)
_PERFORMANCE_COUNT = re.compile(r"(?:至少|不少于|不得少于|不低于)\s*([1-9][0-9]{0,2})\s*(?:份|项|个)")
_PERFORMANCE_AMOUNT = re.compile(
    r"(?:单项|每项|每份)合同(?:金额|价款|总价)(?:须|应)?(?:不低于|不少于|不得低于|至少)\s*"
    rf"(?:人民币|[¥￥])?\s*{_RMB_NUMBER}\s*(亿元|万元|元)"
)


def _compact(text):
    return "".join(normalize("NFKC", text).split())


def normalize_term(text):
    """规范为天、工作日、月；年只换算为月，不假定每月的天数。"""
    match = re.fullmatch(_TERM_VALUE, normalize("NFKC", text.strip()))
    if match is None:
        return None
    value, unit = int(match[1]), match[2]
    if unit == "年":
        return (value * 12, "月") if value <= 833 else None
    return value, {"日历天": "天", "日": "天", "个月": "月"}.get(unit, unit)


def _term_kind(label):
    return next((key for key, pattern in TERM_LABELS.items() if re.fullmatch(pattern, label)), None)


def tender_term_limits(text):
    text = normalize("NFKC", text)
    for key, pattern in _TERM_LIMITS.items():
        for match in pattern.finditer(text):
            if term := normalize_term(match["value"]):
                yield key, f"{term[0]}{term[1]}"


def tender_performance_limits(text):
    """只预填同一条类似业绩要求中的绝对日期和明确数值。"""
    if not _PERFORMANCE.search(text):
        return
    text = normalize("NFKC", text)
    if re.search(r"任选|满足其一|之一|或提供|或至少", text):
        return
    count = _PERFORMANCE_COUNT.search(text)
    amount = _PERFORMANCE_AMOUNT.search(text)
    if re.search(r"合同(?:金额|价款|总价)", text) and not amount:
        return
    dates_found = None
    if window := _SIGNING_WINDOW.search(text):
        tail = text[window.end():].lstrip()
        dates = find_dates(tail)
        if len(dates) >= 2 and dates[0][0] == 0:
            (_, first_end, first), (second_start, _, second) = dates[:2]
            if first <= second and re.fullmatch(r"\s*(?:至|到|~|～|—)\s*", tail[first_end:second_start]):
                dates_found = first, second
    if re.search(r"签订|签署|签约|近\s*[一二三四五六七八九十\d]+\s*年|以来|完成时间", text) and not dates_found:
        return
    if count:
        yield "performance_count", int(count[1])
    if amount:
        yield "performance_amount", str(rounded(_num(amount[1]) * _RMB_SCALE[amount[2]]))
    if dates_found:
        yield "performance_from", dates_found[0].isoformat()
        yield "performance_until", dates_found[1].isoformat()


def requirement_kind(text):
    """“本地规则”要求对应的检查；对不上的返回 None（由人工核对）。"""
    if re.search(r"限价|控制价|拦标价|最高(?:投标|报价)?费率|最低(?:投标|报价)?下浮率|费率上限|下浮率下限|费率\s*(?:不得|不)(?:高于|超过|低于)", text):
        return "ceiling"
    if re.search(r"两个以上|两份以上|多个(?:不同的)?(?:投标)?报价|备选", text):
        return "multi_total"
    if re.search(r"大写|小写", text):
        return "uppercase"
    if "保证金" in text:
        return "deposit"
    if _PERFORMANCE.search(text):
        return "performance"
    terms = [key for key, label in TERM_LABELS.items() if re.search(label, text)]
    if len(terms) == 1:
        return terms[0]
    if "有效期" in text:
        return "validity"
    return None


def _issue(type_, level, title, rows, article=None, kind=None):
    return {"type": type_, "level": level, "title": title, "rows": rows, "article": article, "kind": kind}


def validity_days(nodes):
    """[(天数, node)]；“个月”按 30 天计。"""
    out = []
    for n in nodes:
        for m in _VALIDITY.finditer(n.text):
            days = int(m.group(1)) * (30 if m.group(2) == "个月" else 1)
            out.append((days, n, m.start(), m.end()))
    return out


def deposits(nodes):
    out = []
    for n in nodes:
        for m in _DEPOSIT.finditer(n.text):
            hits = [h for h in find_amounts(n.text) if h.value is not None and m.end() <= h.start <= m.end() + 30]
            if hits:
                out.append((hits[0].value, n, m.start(), hits[0].end))
    return out


def expired_certificates(nodes, opening: date):
    """[(日期, node, start, end, 低置信)]：“有效期至”后的日期早于开标日；投标、报价有效期不算。"""
    out = []
    for n in nodes:
        for m in _CERT.finditer(n.text):
            if _NOT_CERT.search(n.text[max(0, m.start() - 6):m.start() + 4]):
                continue
            found = [(a, b, v) for a, b, v in find_dates(n.text) if m.end() <= a <= m.end() + 15]
            if found and found[0][2] < opening:
                out.append((found[0][2], n, m.start(), found[0][1], (n.ocr_conf or 1) < LOW_OCR_CONF))
    return out


def upper_value(text):
    """唱标价大写 → 数值；允许前面写“人民币”。无法解析时返回 None。"""
    return chinese_upper_value(re.sub(r"^(?:人民币)?[:：]?", "", "".join(text.split())))


_UPPER_DETAIL = re.compile(r"= ([\d,]+\.\d{2})，相邻的小写金额 ([\d,]+\.\d{2})")
_ROW_DETAIL = re.compile(r"= ([\d,]+\.\d{2})，表中金额 ([\d,]+\.\d{2})")


def _num(text):
    return Decimal(text.replace(",", ""))


def _table_rows(nodes):
    tables = defaultdict(lambda: defaultdict(dict))
    title = None
    for node in nodes:
        if cell := table_cell(node.loc):
            table, col, row = cell
            table_rows = tables[(node.file_seq, table)]
            if not table_rows and title is not None and title.file_seq == node.file_seq:
                table_rows[0] = {"title": title}
            table_rows[row][col] = node
            title = None
        else:
            title = node
    return tables.values()


def _term_evidence(nodes, tables):
    hits = defaultdict(list)
    for node in nodes:
        for match in _TERM_LINE.finditer(node.text):
            if value := normalize_term(match["value"]):
                hits[_term_kind(match["label"])].append((value, (node,)))
    for rows in tables:
        label_col, response_col = None, None
        for row_no in sorted(k for k in rows if k):
            row = rows[row_no]
            texts = {col: _compact(node.text) for col, node in row.items()}
            if any(t in ("投标响应", "投标承诺", "投标人承诺") for t in texts.values()):
                label_col = next((c for c, t in texts.items() if t in ("项目", "名称", "内容", "条款名称")), None)
                response_col = next(c for c, t in texts.items() if t in ("投标响应", "投标承诺", "投标人承诺"))
                continue
            label, response = row.get(label_col), row.get(response_col)
            if label and response and (kind := _term_kind(texts[label_col])):
                if value := normalize_term(response.text):
                    hits[kind].append((value, (label, response)))
    return hits


def _performance_rows(tables, start, end, minimum):
    """仅按有明确归属的表格及签订日期、合同金额表头核对。"""
    records = defaultdict(list)
    for rows in tables:
        titled = any(_PERFORMANCE_TITLE.fullmatch(_compact(n.text)) for n in rows.get(0, {}).values())
        headers = {}
        for row_no in sorted(k for k in rows if k):
            row = rows[row_no]
            texts = {col: _compact(node.text) for col, node in row.items()}
            if any(_PERFORMANCE_TITLE.fullmatch(t) for t in texts.values()):
                titled, headers = True, {}
                continue
            if not titled:
                continue
            found = {key: [(col, t, row[col]) for col, t in texts.items() if pattern.fullmatch(t)]
                     for key, pattern in _PERFORMANCE_HEADERS.items()}
            if found["name"]:
                headers = {key: cells[0] for key, cells in found.items() if len(cells) == 1}
                continue
            if "name" not in headers:
                continue
            name = row.get(headers["name"][0])
            key = _compact(name.text) if name else ""
            if key in ("合计", "小计", "总计"):
                headers = {}
                continue
            if not key and not any(t for t in texts.values()):
                continue
            evidence = [name] if name else []
            checks = []
            if start or end:
                header = headers.get("date")
                signed_node = row.get(header[0]) if header else None
                if signed_node:
                    evidence.extend((header[2], signed_node))
                signed = parse_date(_compact(signed_node.text)) if signed_node else None
                checks.append(None if signed is None else (not start or signed >= start) and (not end or signed <= end))
            if minimum is not None:
                header = headers.get("amount")
                amount_node = row.get(header[0]) if header else None
                amount, unit = None, None
                if amount_node:
                    evidence.extend((header[2], amount_node))
                    match = _RMB.fullmatch(_compact(amount_node.text))
                    if match:
                        unit = match[2] or next((u for u in _RMB_SCALE if f"{u})" in header[1]), None)
                        if unit:
                            amount = _num(match[1]) * _RMB_SCALE[unit]
                checks.append(None if amount is None else rounded(amount) >= minimum)
            if (not key or key in ("-", "—", "/", "无", "同上", "同前")
                    or any(n.ocr_conf is not None for n in evidence)):
                verdict = "unclear"
            elif False in checks:
                verdict = "no"
            else:
                verdict = "unclear" if None in checks else "yes"
            # 同名项目只数一次；同名记录相互矛盾时交人工核对。
            records[key or next(iter(row.values())).loc].append((verdict, evidence))
    return records


def _new_compliance(nodes, params, bidder, at, *, partial=False):
    verdicts, issues = {}, []
    tables = _table_rows(nodes) if any(params.get(k) for k in (*TERM_LABELS, "performance_count")) else []
    terms = _term_evidence(nodes, tables) if any(params.get(k) for k in TERM_LABELS) else {}
    labels = {"construction_max": "工期", "delivery_max": "交货期", "warranty_min": "质保期", "defect_min": "缺陷责任期"}

    def record(kind, label, status, reason, evidence=()):
        places = [at(node) for node in evidence]
        if status == "no" and partial:
            status, reason = "unclear", reason + "；部分投标材料尚未完成解析，请稍后复核"
        verdicts[kind] = (status, reason, places)
        if status == "no":
            issues.append(_issue(kind, "general", reason,
                                 [{"label": label, "value": reason, "places": {bidder: places}}], kind=kind))

    for kind, label in labels.items():
        required = normalize_term(params[kind]) if params.get(kind) else None
        found = terms.get(kind, [])
        evidence = [node for _, group in found[:4] for node in group]
        if required is None:
            record(kind, label, "unclear", f"关键参数未填写{label}")
        elif not found:
            record(kind, label, "missing", f"未找到可直接比较的{label}承诺")
        elif len({value for value, _ in found}) != 1 or found[0][0][1] != required[1]:
            record(kind, label, "unclear", f"{label}承诺不一致或单位不同，请核对", evidence)
        elif any(n.ocr_conf is not None for _, group in found for n in group):
            record(kind, label, "unclear", f"{label}承诺涉及扫描件识读，请核对原件", evidence)
        else:
            (value, unit), group = found[0]
            limit = required[0]
            maximum = kind.endswith("_max")
            fails = value > limit if maximum else value < limit
            direction = ("超过" if maximum else "短于") if fails else ("未超过" if maximum else "不短于")
            record(kind, label, "no" if fails else "yes",
                   f"{label} {value} {unit}，{direction}招标要求的 {limit} {unit}", group)

    count = params.get("performance_count")
    start = parse_date(params["performance_from"]) if params.get("performance_from") else None
    end = parse_date(params["performance_until"]) if params.get("performance_until") else None
    minimum = Decimal(params["performance_amount"]) if params.get("performance_amount") else None
    label = "类似业绩"
    if not count:
        record("performance", label, "unclear", "关键参数未填写类似业绩最低份数")
        return issues, verdicts
    records = _performance_rows(tables, start, end, minimum)
    if not records:
        record("performance", label, "missing", "未找到可按列核对的类似业绩一览表")
        return issues, verdicts
    tallies = defaultdict(list)
    for entries in records.values():
        status = entries[0][0] if len({state for state, _ in entries}) == 1 else "unclear"
        tallies[status].append(entries[0][1])
    passed, unclear = len(tallies["yes"]), len(tallies["unclear"])
    status = "yes" if passed >= count else "unclear" if unclear else "no"
    reason = f"类似业绩一览表中 {passed} 份满足已填写的条件，要求至少 {count} 份"
    if unclear and status == "unclear":
        reason += f"；另有 {unclear} 份的日期、金额或项目归属待核对"
    groups = tallies["yes"] if status == "yes" else tallies["no"] + tallies["unclear"] + tallies["yes"]
    evidence = list({node.loc: node for group in groups[:4] for node in group}.values())
    record("performance", label, status, reason, evidence)
    return issues, verdicts


def corrected_total(fact, wrong_case, ceiling):
    """修正后总价（bid-pricing.md 二.6）：总价大小写不一致以大写为准，分项计算错误以单价为准；
    供评标委员会参考，以招标文件约定的修正原则为准。没有可修正的问题时返回 None。"""
    base = fact.get("file_total") if fact.get("manual_total") is not None else fact["total"]
    if base is None:
        return None
    total_locs = {n.loc for _v, _c, n in fact["totals"]}
    value, basis = rounded(base), []
    for r in wrong_case:
        m = _UPPER_DETAIL.search(r.detail)
        if m and r.locs[0] in total_locs and _num(m[2]) == value:
            value, basis = _num(m[1]), basis + ["大写为准"]
            break
    delta = sum((_num(m[1]) - _num(m[2]) for r in fact["results"] if r.code == "L-01"
                 and (m := _ROW_DETAIL.search(r.detail))), Decimal(0))
    if delta:
        value, basis = value + delta, basis + ["单价为准"]
    if not basis or value == rounded(base):
        return None
    out = {"total": money(value), "from": money(base), "basis": "、".join(basis)}
    if ceiling is not None and value > rounded(ceiling):
        percent = ((value - ceiling) * 100 / ceiling).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        out["over_ceiling"] = f"超最高限价 +{money(value - ceiling)}（+{percent}%）"
    return out


def _evaluate_rate(b, fact, params, at, issues, verdicts):
    from app.bid.pricing import RATE_LABELS, RATE_LIMIT_LABELS, rate_text
    mode = params["quote_mode"]
    label, limit_label = RATE_LABELS[mode], RATE_LIMIT_LABELS[mode]
    distinct, totals = fact["distinct"], fact["totals"]
    limit = Decimal(params["rate_limit"]) if params.get("rate_limit") else None
    where = [at(n) for _value, _currency, n in totals][:4]
    if len(distinct) > 1:
        title = f"出现 {len(distinct)} 个不同的{label}"
        rows = [{"label": label, "value": rate_text(v), "places": {b: [at(n)]}} for v, _, n in totals[:8]]
        issues.append(_issue("multi_total", "veto", title, rows, ARTICLE + "（四）", "multi_total"))
        verdicts["multi_total"] = ("no", title, where)
    elif distinct:
        verdicts["multi_total"] = ("yes", f"{label} {rate_text(distinct[0])}，未见其他不同报价", where)
    else:
        verdicts["multi_total"] = ("missing", f"未找到明确的{label}", [])

    if limit is None:
        verdicts["ceiling"] = ("unclear", f"关键参数未填写{limit_label}", [])
    elif not distinct:
        verdicts["ceiling"] = ("missing", f"未能确定{label}", [])
    else:
        lower = mode == "discount"
        bound = min(distinct) if lower else max(distinct)
        failed = bound < limit if lower else bound > limit
        relation = ("低于" if lower else "高于") if failed else ("不低于" if lower else "不高于")
        title = f"{label} {rate_text(bound)} {relation}{limit_label} {rate_text(limit)}"
        evidence = [at(n) for value, _currency, n in totals if value == bound][:1]
        if failed:
            issues.append(_issue("ceiling", "veto", title, [{"label": label, "value": rate_text(bound), "places": {b: evidence}}],
                                 ARTICLE + "（五）", "ceiling"))
        verdicts["ceiling"] = ("no" if failed else "yes", title, evidence)
    verdicts["uppercase"] = ("unclear", "按百分比报价，金额大小写核对不适用", [])


def _evaluate_fixed_price(b, nodes, fact, params, at, issues, verdicts):
    from app.bid.pricing import rate_text
    ceiling = Decimal(params["ceiling"]) if params.get("ceiling") else None
    by_loc = {n.loc: n for n in nodes}

    # 多个不同总价（第五十一条：同一投标人提交两个以上不同的投标报价）
    distinct, totals = fact["distinct"], fact["totals"]
    if len(distinct) > 1:
        rows = [{"label": "投标总价", "value": money(v), "places": {b: [at(n)]}} for v, _c, n in totals]
        title = f"出现 {len(distinct)} 个不同的投标总价：" + "、".join(money(v) for v in distinct)
        separate = fact.get('separate_scopes', False)
        if separate:
            title += "；报价范围不同，需统一比较口径"
        issues.append(_issue("multi_total", "hint" if separate else "veto", title, rows[:8], None if separate else ARTICLE + "（四）", "multi_total"))
        verdicts["multi_total"] = ("unclear" if separate else "no", title, [r["places"][b][0] for r in rows[:4]])
    elif distinct:
        verdicts["multi_total"] = ("yes", f"投标总价 {money(distinct[0])}，未见其他不同总价", [at(totals[0][2])])
    else:
        verdicts["multi_total"] = ("missing", "未找到标明“投标总价 / 投标报价”的金额", [])

    total = fact["total"]
    # 出现多个总价时逐个与限价比较，任何一个超出都报
    manual = fact.get("manual_total")
    candidates = [manual] if manual is not None else distinct or ([rounded(total)] if total is not None else [])
    file_total = fact.get("file_total")
    if (manual is not None and file_total is not None and fact.get("file_currency") in (None, fact["currency"])
            and manual != file_total):
        difference = money(manual - file_total).replace("-", "−")
        if manual > file_total:
            difference = "+" + difference
        found = [at(totals[0][2])] if totals else []
        issues.append(_issue("manual_total_diff", "general", f"文件总价与唱标价不一致（差额 {difference}）",
                             [{"label": "唱标价 · 人工录入", "value": money(manual)},
                              {"label": "文件总价", "value": money(file_total), "places": {b: found}}]))
    # 唱标价的大写：本机比对大小写（V34-B-06e）
    upper = fact.get("manual_upper")
    if manual is not None and upper and (value := upper_value(upper)) is not None and rounded(value) != rounded(manual):
        issues.append(_issue("uppercase", "general", "大写与小写金额不一致",
                             [{"label": "唱标价 · 小写", "value": money(manual)},
                              {"label": "唱标价 · 大写", "value": f"{upper}（{money(value)}）"}], kind="uppercase"))
    # 分项合计与投标总价
    tax_totals = {rounded(other[0]) for net, _tax, gross, _expected in fact.get("tax_totals", [])
                  if distinct and distinct[0] in (rounded(net[0]), rounded(gross[0])) for other in (net, gross)}
    if (len(distinct) == 1 and fact["table_total"] is not None
            and rounded(fact["table_total"]) not in (tax_totals | {distinct[0]})):
        main = fact["main"]
        issues.append(_issue("sum_total", "general", f"报价表合计 {money(fact['table_total'])} 与投标总价 {money(distinct[0])} 不一致",
                             [{"label": "报价表", "value": main[0].name, "places": {b: [at(main[2][0][4])] if main[2] else []}},
                              {"label": "投标总价", "value": money(distinct[0]), "places": {b: [at(totals[0][2])]}}]))
    # 超最高限价（第五十一条（五））
    if ceiling is None:
        verdicts["ceiling"] = ("unclear", "招标文件未写明最高投标限价（可在关键参数中补填）；是否低于成本见报价分析", [])
    elif manual is None and fact.get('separate_scopes'):
        verdicts["ceiling"] = ("unclear", "报价范围不同，需统一比较口径后核对最高限价", [])
    elif not candidates:
        verdicts["ceiling"] = ("missing", "未能确定投标总价", [])
    elif fact["currency"] not in (None, params.get("currency")):
        verdicts["ceiling"] = ("unclear", f"投标总价币种为 {fact['currency']}，与最高限价币种不同，未比较", [])
    elif (over := max(candidates)) > rounded(ceiling):
        title = f"投标总价 {money(over)} 高于最高限价 {money(ceiling)}，超出 {money(over - ceiling)}"
        where = [at(n) for v, _c, n in totals if rounded(v) == over][:1] if manual is None else []
        issues.append(_issue("ceiling", "veto", title, [{"label": "投标总价", "value": money(over), "places": {b: where}}],
                             ARTICLE + "（五）", "ceiling"))
        verdicts["ceiling"] = ("no", title, where)
    else:
        verdicts["ceiling"] = ("yes", f"投标总价 {money(max(candidates))}，未超过最高限价 {money(ceiling)}",
                               [at(totals[0][2])] if totals else [])

    # 异常低价（参照第五十一条（五）低于成本）：只提示，由评标委员会要求说明（V34Q-27）
    if low := fact.get("low_price"):
        where = [at(totals[0][2])] if totals else []
        issues.append(_issue("low_price", "hint", f"投标总价 {money(total)} 比其余 {low['others']} 家平均 {low['others_mean']} 低 "
                             f"{low['below_pct']}，请核实是否低于成本",
                             [{"label": "投标总价", "value": money(total), "places": {b: where}},
                              {"label": f"其余 {low['others']} 家平均", "value": low["others_mean"]}], ARTICLE + "（五）"))

    # 大小写（L-04）与分项计算（L-01、L-02）
    # 数字来自扫描件识读的（r.unsure，V34-D-02）只作提示、请核对原件，不参与修正后总价
    found_case = check_uppercase(nodes)
    wrong_case = [r for r in found_case if not r.unsure]
    unsure_case = [r for r in found_case if r.unsure]
    for r in found_case:
        n = by_loc[r.locs[0]]
        title = "大写金额与小写金额可能不一致（扫描件识读，请核对原件）" if r.unsure else "大写金额与小写金额不一致"
        issues.append(_issue("uppercase", "hint" if r.unsure else "general", title,
                             [{"label": "位置", "value": r.detail.split("：", 1)[-1], "places": {b: [at(n)]}}], kind="uppercase"))
    if wrong_case:
        verdicts["uppercase"] = ("no", f"{len(wrong_case)} 处大写与小写金额不一致", [at(by_loc[r.locs[0]]) for r in wrong_case[:4]])
    elif unsure_case:
        verdicts["uppercase"] = ("unclear", f"{len(unsure_case)} 处大写与小写金额可能不一致，数字来自扫描件识读，请核对原件",
                                 [at(by_loc[r.locs[0]]) for r in unsure_case[:4]])
    else:
        verdicts["uppercase"] = ("yes", "未发现大写与小写金额不一致", [])
    for r in fact["results"]:
        n = by_loc.get(r.locs[0])
        code = r.unsure or r.code
        title = "报价表分项金额与数量 × 单价不一致" if code == "L-01" else "报价表各行金额之和与合计不一致"
        if r.unsure:
            title = title.replace("不一致", "可能不一致（扫描件识读，请核对原件）")
        issues.append(_issue("row_calc" if code == "L-01" else "sum_rows", "hint" if r.unsure else "general", title,
                             [{"label": "计算", "value": r.detail, "places": {b: [at(n)] if n else []}}]))
    for net, tax, gross, expected in fact.get("tax_pairs", []):
        if abs(rounded(gross[0]) - expected) <= CENT:
            continue
        nodes_found = {(n.file_seq, n.loc): n for _amount, _currency, n in (net, tax, gross)}
        evidence = [at(n) for n in nodes_found.values()]
        unsure = any(n.ocr_conf is not None for n in nodes_found.values())
        title = "含税价与不含税价按税率换算不一致"
        if unsure:
            title = "税价换算待核对（扫描件识读）"
        detail = f"{money(net[0])} ×（1 + {rate_text(tax[0] * 100)}）= {money(expected)}，所报含税价 {money(gross[0])}"
        issues.append(_issue("tax_calc", "hint" if unsure else "general", title,
                             [{"label": "税价换算", "value": detail, "places": {b: evidence}}]))
    fact["corrected"] = corrected_total(fact, wrong_case, ceiling)


def evaluate(bidder_id, nodes, fact, params, opening: date, place, *, partial=False):
    """返回一家投标人的符合项问题与各检查判断。"""
    issues, verdicts = [], {}
    b = str(bidder_id)
    by_loc = {n.loc: n for n in nodes}

    def at(n, s=0, e=None):
        return place(n, s, len(n.text) if e is None else e)

    if params.get("quote_mode", "fixed") == "fixed":
        _evaluate_fixed_price(b, nodes, fact, params, at, issues, verdicts)
    else:
        _evaluate_rate(b, fact, params, at, issues, verdicts)

    # 投标有效期
    required = params.get("validity_days")
    found = validity_days(nodes)
    if not required:
        verdicts["validity"] = ("unclear", "关键参数未填写投标有效期", [])
    elif not found:
        verdicts["validity"] = ("missing", "未找到投标有效期的天数", [])
    else:
        days, n, s, e = min(found, key=lambda x: x[0])
        if days < required:
            title = f"投标有效期 {days} 天，短于招标文件要求的 {required} 天"
            issues.append(_issue("validity", "veto", title, [{"label": "投标有效期", "value": f"{days} 天", "places": {b: [at(n, s, e)]}}],
                                 ARTICLE + "（六）", "validity"))
            verdicts["validity"] = ("no", title, [at(n, s, e)])
        else:
            verdicts["validity"] = ("yes", f"投标有效期 {days} 天，不短于要求的 {required} 天", [at(n, s, e)])

    # 投标保证金
    need = Decimal(params["deposit"]) if params.get("deposit") else None
    paid = deposits(nodes)
    if need is None:
        verdicts["deposit"] = ("unclear", "关键参数未填写保证金金额", [])
    elif not paid:
        verdicts["deposit"] = ("missing", "未找到投标保证金金额", [])
    else:
        value, n, s, e = max(paid, key=lambda x: x[0])
        if rounded(value) < rounded(need):
            title = f"投标保证金 {money(value)}，少于要求的 {money(need)}"
            issues.append(_issue("deposit", "veto", title, [{"label": "投标保证金", "value": money(value), "places": {b: [at(n, s, e)]}}],
                                 ARTICLE + "（六）", "deposit"))
            verdicts["deposit"] = ("no", title, [at(n, s, e)])
        else:
            verdicts["deposit"] = ("yes", f"投标保证金 {money(value)}，不少于要求的 {money(need)}", [at(n, s, e)])

    # 证书有效期早于开标日（OCR 低置信只提示人工核对）
    for value, n, s, e, low in expired_certificates(nodes, opening):
        if low:
            issues.append(_issue("cert_expired", "hint", f"扫描页上的证书有效期似为 {value.isoformat()}，识别置信度低，请人工核对",
                                 [{"label": "证书有效期", "value": value.isoformat(), "places": {b: [at(n, s, e)]}}]))
        else:
            issues.append(_issue("cert_expired", "general", f"证书有效期至 {value.isoformat()}，早于开标日 {opening.isoformat()}",
                                 [{"label": "证书有效期", "value": value.isoformat(), "places": {b: [at(n, s, e)]}}]))

    # 写给 AI 的指令（L-21）
    for r in check_content(nodes):
        if r.code != "L-21":
            continue
        n = by_loc[r.locs[0]]
        issues.append(_issue("ai_instruction", "hint", "投标文件中有疑似写给 AI 的指令",
                             [{"label": "原文", "value": r.detail.split("：", 1)[-1][:300], "places": {b: [at(n)]}}]))
    extra_issues, extra_verdicts = _new_compliance(nodes, params, b, at, partial=partial)
    issues.extend(extra_issues)
    verdicts.update(extra_verdicts)
    return issues, verdicts
