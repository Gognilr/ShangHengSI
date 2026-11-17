"""确定性符合项（bid.md 3.1）：大小写、合计、多个总价、超最高限价、异常低价提示、投标有效期、保证金、证书有效期与写给 AI 的指令。

金额、天数与日期都在本机按关键参数比较（Decimal，ROUND_HALF_UP）；复用审核的 L-01、L-02、L-04、L-21。
“本地规则”类要求按文字归到某项检查，每家写一个单元格（bid_cells，source local）：
yes 响应、no 疑似不响应、missing 未找到、unclear 无法本地判断。
"""
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.checks.amounts import check_uppercase
from app.checks.content import check_content
from app.checks.dates import find_dates
from app.checks.model import CheckNode
from app.checks.numbers import money, rounded
from app.parsing.model import LOW_OCR_CONF
from app.redaction.detect import find_amounts


@dataclass(frozen=True)
class BidNode(CheckNode):
    """审核校验的节点加上标书需要的出处：文件、页码、节点类别与 OCR 置信度。"""
    kind: str = "paragraph"
    ocr_conf: float | None = None
    file_id: object = None
    page: int | None = None

ARTICLE = "第五十一条"
_VALIDITY = re.compile(r"投标有效期[^。；;\n]{0,30}?(\d{1,4})\s*(个月|日历天|天|日)")
_DEPOSIT = re.compile(r"投标保证金")
_CERT = re.compile(r"有效(?:期|日期)限?[^。；;\n]{0,25}?(?:至|截止(?:日期)?|到期日?)\s*[:：]?\s*")
_NOT_CERT = re.compile(r"投标|报价|响应|合同")
_UPPER = re.compile(r"[零壹贰叁肆伍陆柒捌玖拾佰仟]")


def requirement_kind(text):
    """“本地规则”要求对应的检查；对不上的返回 None（由人工核对）。"""
    if re.search(r"限价|控制价|拦标价", text):
        return "ceiling"
    if re.search(r"两个以上|两份以上|多个(?:不同的)?(?:投标)?报价|备选", text):
        return "multi_total"
    if re.search(r"大写|小写", text):
        return "uppercase"
    if "保证金" in text:
        return "deposit"
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
                out.append((found[0][2], n, m.start(), found[0][1], n.kind == "ocr_block" and (n.ocr_conf or 1) < LOW_OCR_CONF))
    return out


def evaluate(bidder_id, nodes, fact, params, opening: date, place):
    """一家投标人的符合项问题与各检查的判断。nodes 为 CheckNode 子类（带 kind、ocr_conf）；
    fact 为 pricing.analyze 的该家结果；place(node, start, end) 给出证据位置。返回 (问题, {检查: (结论, 理由, [位置])})。"""
    issues, verdicts = [], {}
    b = str(bidder_id)
    ceiling = Decimal(params["ceiling"]) if params.get("ceiling") else None
    by_loc = {n.loc: n for n in nodes}

    def at(n, s=0, e=None):
        return place(n, s, len(n.text) if e is None else e)

    # 多个不同总价（第五十一条：同一投标人提交两个以上不同的投标报价）
    distinct, totals = fact["distinct"], fact["totals"]
    if len(distinct) > 1:
        rows = [{"label": "投标总价", "value": money(v), "places": {b: [at(n)]}} for v, _c, n in totals]
        title = f"出现 {len(distinct)} 个不同的投标总价：" + "、".join(money(v) for v in distinct)
        issues.append(_issue("multi_total", "veto", title, rows[:8], ARTICLE + "（四）", "multi_total"))
        verdicts["multi_total"] = ("no", title, [r["places"][b][0] for r in rows[:4]])
    elif distinct:
        verdicts["multi_total"] = ("yes", f"投标总价 {money(distinct[0])}，未见其他不同总价", [at(totals[0][2])])
    else:
        verdicts["multi_total"] = ("missing", "未找到标明“投标总价 / 投标报价”的金额", [])

    total = fact["total"]
    # 出现多个总价时逐个与限价比较，任何一个超出都报
    candidates = distinct or ([rounded(total)] if total is not None else [])
    # 分项合计与投标总价
    if distinct and len(distinct) == 1 and fact["table_total"] is not None and rounded(fact["table_total"]) != distinct[0]:
        main = fact["main"]
        issues.append(_issue("sum_total", "general", f"报价表合计 {money(fact['table_total'])} 与投标总价 {money(distinct[0])} 不一致",
                             [{"label": "报价表", "value": main[0].name, "places": {b: [at(main[2][0][4])] if main[2] else []}},
                              {"label": "投标总价", "value": money(distinct[0]), "places": {b: [at(totals[0][2])]}}]))
    # 超最高限价（第五十一条（五））
    if ceiling is None:
        verdicts["ceiling"] = ("unclear", "关键参数未填写最高限价，无法本地比较", [])
    elif not candidates:
        verdicts["ceiling"] = ("missing", "未能确定投标总价", [])
    elif fact["currency"] not in (None, params.get("currency")):
        verdicts["ceiling"] = ("unclear", f"投标总价币种为 {fact['currency']}，与最高限价币种不同，未比较", [])
    elif (over := max(candidates)) > rounded(ceiling):
        title = f"投标总价 {money(over)} 高于最高限价 {money(ceiling)}，超出 {money(over - ceiling)}"
        where = [at(n) for v, _c, n in totals if rounded(v) == over][:1]
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
    wrong_case = check_uppercase(nodes)
    for r in wrong_case:
        n = by_loc[r.locs[0]]
        issues.append(_issue("uppercase", "general", "大写金额与小写金额不一致", [{"label": "位置", "value": r.detail.split("：", 1)[-1],
                                                                                "places": {b: [at(n)]}}], kind="uppercase"))
    verdicts["uppercase"] = (("no", f"{len(wrong_case)} 处大写与小写金额不一致", [at(by_loc[r.locs[0]]) for r in wrong_case[:4]])
                             if wrong_case else ("yes", "未发现大写与小写金额不一致", []))
    for r in fact["results"]:
        n = by_loc.get(r.locs[0])
        title = "报价表分项金额与数量 × 单价不一致" if r.code == "L-01" else "报价表各行金额之和与合计不一致"
        issues.append(_issue("row_calc" if r.code == "L-01" else "sum_rows", "general", title,
                             [{"label": "计算", "value": r.detail, "places": {b: [at(n)] if n else []}}]))

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
    return issues, verdicts
