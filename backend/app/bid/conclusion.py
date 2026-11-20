"""标书初审结论（V35-E-01）：严重、提示两档，页面与报告共用。

严重只收两类：否决类（★ / 否决条款不响应，本机能确定的法定否决情形：多个报价、证书过期、签章页标为缺章）与串标强证据；
其余都算提示。“不是问题”记在项目结论的 dismissed 中，可撤销；条目键为 item: / cell: / case: / sign: 加标识。
"""
import re
from collections import defaultdict
from itertools import combinations

from app.bid.compliance import TERM_LABELS

KEY_CLAUSES = {"veto", "substantive"}  # ★ 与否决条款
VETO_TYPES = {"multi_total", *TERM_LABELS, "performance"}
PRICE_ISSUES = {"multi_total", "sum_total", "low_price", "uppercase", "row_calc", "sum_rows", "tax_calc", "manual_total_diff"}
TIER = {"certain": 0, "verify": 1, "reference": 2}  # 单条串标证据的强弱
DECLARATION = "系统只指出问题并定位原件，不作否决或串通投标的认定。"
_ORG = re.compile(r"(集团)?(股份)?有限(责任)?公司$|(集团)?股份公司$|集团$|公司$|中心$|研究院$|研究所$|设计院$|事务所$")
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_PROVINCE = re.compile(r"^(?:北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川"
                       r"|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆|香港|澳门)(?:省|市|自治区|特别行政区)?")
_REGION = re.compile(r"^(北京|上海|天津|重庆)市?|^[^\s]{2,3}?[省市]")
_TRADE = re.compile(r"(科技|技术|设备|仪器|工程|建设|建筑|贸易|实业|发展|信息|电子|电气|机电|环保|咨询|服务|智能|系统|网络|软件|医疗|器械|物资|供应链"
                    r"|安全生产|安全|生产|评价|检测|泵业|阀门|机械|制造|化工|新材料|材料|能源|工业|自动化|流体|环境)+$")
_ARTICLE = re.compile(r"（([一二三四五六])）")


def short_names(bidders):
    cores = {}
    for bid, name in bidders.items():
        core = _ORG.sub("", _PAREN.sub("", name))  # “德帕姆（杭州）泵业科技”去掉括号中的地名
        local = _REGION.sub("", _PROVINCE.sub("", core))
        core = local if len(local) >= 2 else core
        simple = _TRADE.sub("", core)
        cores[bid] = (simple if len(simple) >= 2 else core) or name
    for size in (4, 6, 10, 99):
        names = {bid: name[-size:] for bid, name in cores.items()}
        if len(set(names.values())) == len(bidders):
            return names
    return bidders


def clue_strength(item, row):
    """用具体证据行判定，避免把作者或软件随同文档标识一起升为确凿。"""
    kind, label = item["type"], row["label"]
    if kind == "props":
        if label in {"PDF 原始文件标识", "XMP 文档标识", "XMP 原始文档标识"}:
            return "certain", "（一）"
        return ("reference" if label.startswith("生成软件") else "verify"), "（一）"
    if kind == "contact":
        article = row.get("article")
        if label in {"银行账号", "证件号", "项目经理证号"}:
            return "certain", article or ("（六）" if label == "银行账号" else "（二）")
        return "verify", article or {"项目经理": "（三）", "地址": "（一）"}.get(label, "（二）")
    if kind == "mixed":
        return ("certain" if row.get("mixed_page") else "verify"), "（五）"
    level = row.get("level", item["level"])
    return ("verify" if level in {"strong", "medium"} else "reference"), item.get("article")


def _fact(item, row):
    kind, label = item["type"], row["label"]
    if kind in {"props", "contact"}:
        return f"{label}相同"
    if kind == "mixed" and row.get("mixed_page"):
        return item["title"].removesuffix("的名称") + "的封面"
    if kind == "similar" and row.get("chars"):
        return f"相似段落约 {row['chars']} 字，共 {row['sentences']} 句"
    return item["title"]


def _cases(items, order, shorts, dismissed, baseline):
    """串通信号按投标人对归并，并查集连成案件：任一强证据即“疑似串通投标”（严重），两类以上独立中等证据为提示。"""
    pairs = defaultdict(list)
    for item in items:
        if item["kind"] != "clue" or f"item:{item['id']}" in dismissed:
            continue
        members = sorted(set(item["bidder_ids"]) & order.keys(), key=order.get)
        for row_index, row in enumerate(item["rows"]):
            strength, article = clue_strength(item, row)
            for a, b in combinations(members, 2):
                pairs[a, b].append({"item_id": str(item["id"]), "row": row_index, "type": item["type"],
                                    "tier": strength, "article": article, "fact": _fact(item, row),
                                    "bidder_ids": [a, b], "normal": item.get("normal")})

    parent = {bid: bid for bid in order}

    def root(bid):
        while parent[bid] != bid:
            parent[bid] = parent[parent[bid]]
            bid = parent[bid]
        return bid

    qualified = {}
    for pair, rows in pairs.items():
        hard = any(r["tier"] == "certain" for r in rows)
        medium = {r["type"] for r in rows if r["tier"] == "verify"}
        if hard or len(medium) >= 2:
            a, b = pair
            parent[root(b)] = root(a)
            qualified[pair] = hard

    groups = defaultdict(set)
    for a, b in qualified:
        groups[root(a)].update((a, b))
    cases = []
    for members in groups.values():
        ids = sorted(members, key=order.get)
        evidence = [r for pair, rows in pairs.items() if set(pair) <= members for r in rows]
        evidence.sort(key=lambda r: (TIER[r["tier"]], order[r["bidder_ids"][0]], order[r["bidder_ids"][1]], r["type"], r["fact"]))
        # 同一条多方证据只列一次，保留实际涉及的投标人。
        chain = {}
        for row in evidence:
            key = (row["item_id"], row["row"])
            if key in chain:
                linked = chain[key]["bidder_ids"]
                chain[key]["bidder_ids"] = sorted(set(linked + row["bidder_ids"]), key=order.get)
            else:
                chain[key] = dict(row)
        own = {p for p in qualified if set(p) <= members}
        strong = [list(p) for p in sorted(own, key=lambda p: (order[p[0]], order[p[1]])) if qualified[p]]
        known = [r for r in evidence if r["tier"] == "certain"] if strong else [r for r in evidence if r["tier"] != "reference"]
        article = "".join(f"（{a}）" for a in "一二三四五六" if any(a in _ARTICLE.findall(r["article"] or "") for r in known))
        label = "围标：" if len(ids) >= 3 else ""
        title = f"{label}{'、'.join(shorts[i] for i in ids)} 疑似串通投标"
        if article:
            title += f" · 第四十条{article}"
        cases.append({"id": "case:" + ":".join(ids), "bidder_ids": ids, "title": title, "article": article,
                      "level": "severe" if strong else "hint", "strong_pairs": strong,
                      "evidence": list(chain.values()), "normal": list(dict.fromkeys(r["normal"] for r in evidence if r["normal"])),
                      "baseline": baseline})
    return sorted(cases, key=lambda c: (c["level"] != "severe", [order[i] for i in c["bidder_ids"]]))


def _short_requirement(req):
    text = " ".join(req["text"].split())
    # 开头是多个位号（“P-201A/B、P-101A/B防爆等级……”）时只留第一个，否则几条要求的简称一模一样
    text = _TAG_LIST.sub(lambda m: m.group(1) + "等", text, count=1)
    return text[:16] + ("…" if len(text) > 16 else "")


_TAG = r"[A-Z]{1,3}-?\d+[A-Z]?(?:/[A-Z])*"
_TAG_LIST = re.compile(rf"^({_TAG})(?:[、，, ]+(?:和|及)?{_TAG})+")


def _low_ocr(item):
    return any(p.get("low_ocr") for row in item["rows"] for places in row.get("places", {}).values() for p in places)


def _issue_level(item):
    """否决类：★ / 否决条款不响应，以及本机能确定的法定否决情形（多个报价、证书过期）；扫描件识读低置信的降为提示。"""
    if _low_ocr(item):
        return "hint"
    if item["type"] in VETO_TYPES and item["level"] == "veto":
        return "severe"
    if item["type"] == "cert_expired" and item["level"] != "hint":
        return "severe"
    return "hint"


def build(view, bidders, *, local_only=False, model_state=None, dismissed=(), baseline=None):
    dismissed = set(dismissed)
    order = {bid: i for i, bid in enumerate(bidders)}
    shorts = short_names(bidders)
    reqs = {str(r["id"]): r for r in view["requirements"]}
    items = view["items"]
    price_data = view.get("pricing") or {}
    pricing = price_data.get("bidders", {})
    cases = _cases(items, order, shorts, dismissed, baseline or {})
    case_items = {e["item_id"] for case in cases for e in case["evidence"]}
    out = {"severe": [], "hint": [], "dismissed": []}

    def add(key, ids, title, category, level, *, article=None, item_id=None, requirement_id=None, case_id=None, source="local"):
        entry = {"id": key, "bidder_ids": ids, "title": title, "category": category, "level": level, "article": article,
                 "item_id": item_id, "requirement_id": requirement_id, "case_id": case_id, "source": source}
        out["dismissed" if key in dismissed else level].append(entry)

    linked, covered = {}, set()
    for item in items:
        iid, ids, kind = str(item["id"]), item["bidder_ids"], item["type"]
        if not ids or any(i not in order for i in ids) or item["kind"] == "normal_dup":
            continue
        req_id = str(item["requirement_id"]) if item["requirement_id"] else None
        if kind == "model_response" and req_id:
            linked[req_id, ids[0]] = item
            continue
        if item["kind"] != "issue":
            if iid not in case_items:
                add("item:" + iid, ids, item["title"], "串标线索", "hint", item_id=iid)
            continue
        title = item["title"]
        if kind == "row_calc":
            title = "报价计算待核对 1 处（扫描件识读）" if item["level"] == "hint" else "报价计算错误 1 处"
        elif kind == "low_price" and (low := pricing.get(ids[0], {}).get("low_price")):
            title = f"低于其余各家平均 {low.get('below_pct_display', low['below_pct'])}，请核实是否低于成本"
        elif kind == "multi_total" and item["level"] == "veto":
            count = len(pricing.get(ids[0], {}).get("totals_found", []))
            if count > 1:
                title = f"出现 {count} 个不同的{price_data.get('quote_label', '投标总价')}"
        elif kind == "uppercase":
            title = "大写与小写金额不一致" if item["level"] != "hint" else title
        elif kind == "sum_total":
            title = "报价表合计与投标总价不符"
        level = _issue_level(item)
        category = "否决" if level == "severe" else "报价" if kind in PRICE_ISSUES else "其他"
        add("item:" + iid, ids, title, category, level, article=item["article"], item_id=iid, source=item["source"])
        if req_id:
            covered.add((req_id, ids[0]))

    for cell in view["cells"]:
        rid, bid = str(cell["requirement_id"]), str(cell["bidder_id"])
        req = reqs.get(rid)
        if req is None or bid not in order or req["group"] == "format" or (rid, bid) in covered or cell["verdict"] in ("yes", "manual"):
            continue
        key_clause = req["group"] in KEY_CLAUSES
        term = _short_requirement(req)
        item = linked.get((rid, bid))
        verdict = cell["verdict"]
        if verdict == "no":
            title = f"未响应“{term}”（条款 {req['seq']}）"
            # 模型判断：引文核实且没有“响应待核实”的证据缺口才算严重
            sure = cell["source"] == "local" or (cell["cite"] != "unverified" and (item is None or item["level"] == "veto"))
            level = "severe" if key_clause and sure else "hint"
            if not sure:
                title += "，引文待核实"
        elif verdict == "missing":
            title, level = f"本机未找到“{term}”的响应内容（条款 {req['seq']}）", "hint"
        else:
            title, level = f"“{term}”无法判断，请看原件（条款 {req['seq']}）", "hint"
        add(f"cell:{rid}:{bid}", [bid], title, "否决" if level == "severe" else "条款", level,
            article=item["article"] if item else None, item_id=str(item["id"]) if item else None,
            requirement_id=rid, source=cell["source"])

    for page in view.get("signing", []):
        if page.get("mark") == "missing" and page["bidder_id"] in order:
            where = f"第 {page['page']} 页" if page.get("page") else page.get("title") or "格式页"
            add(f"sign:{page['file_id']}:{page.get('page') or page.get('loc')}", [page["bidder_id"]],
                f"{page.get('title') or '签章页'}缺章或缺签字（{page.get('file') or '文件'} · {where}）", "否决", "severe")

    for case in cases:
        add(case["id"], case["bidder_ids"], case["title"], "串通", case["level"], article=case["article"], case_id=case["id"])

    rank = {"否决": 0, "串通": 1, "条款": 2, "报价": 3, "串标线索": 4, "其他": 5}
    for level in out:
        out[level].sort(key=lambda e: (rank.get(e["category"], 9), order[e["bidder_ids"][0]], e["title"]))
    rows = []
    for bid, name in bidders.items():
        rows.append({"id": bid, "name": name, "short": shorts[bid],
                     "severe": sum(bid in e["bidder_ids"] for e in out["severe"]),
                     "hint": sum(bid in e["bidder_ids"] for e in out["hint"]),
                     "total": pricing.get(bid, {}).get("total"), "manual_total": pricing.get(bid, {}).get("manual_total", False)})
    serious = [r for r in rows if r["severe"]]
    lede = f"{len(rows)} 家投标人中，{len(serious)} 家有严重问题" if serious else f"{len(rows)} 家投标人均未发现严重问题"
    strong_cases = [c for c in cases if c["level"] == "severe" and c["id"] not in dismissed]
    if strong_cases:
        lede += f"；发现 {len(strong_cases)} 组疑似串通投标"
    # 交模型补查的 ★ / 否决条款中，本机没有结论（尚无模型判断）的家数
    have = {(str(c["requirement_id"]), str(c["bidder_id"])) for c in view["cells"]}
    lookup = sum((rid, bid) not in have for rid, r in reqs.items() if r["group"] in KEY_CLAUSES and r["method"] == "model"
                 for bid in order)
    sub = ("本项目只做本地检查，★ 与否决条款中本机未找到的不交模型补查。" if local_only else
           "模型补查进行中，结果出来后自动更新。" if model_state in ("preparing", "awaiting", "running") else
           f"★ 与否决条款中本机未找到的 {lookup} 处，可交模型补查。" if lookup and model_state != "done" else None)
    return {"lede": lede + "。", "sub": sub, "bidders": rows, "cases": cases, **out}
