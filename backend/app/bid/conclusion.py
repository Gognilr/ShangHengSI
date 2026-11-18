"""标书初审结论。页面与报告共用同一组分层、案件及理由。"""
import re
from collections import defaultdict
from itertools import combinations

SERIOUS_GROUPS = {"veto", "substantive", "qualification"}
LOCAL_CERTAIN = {"ceiling", "multi_total", "validity", "deposit"}
TIER = {"certain": 0, "verify": 1, "reference": 2}
DECLARATION = "系统只给出建议；否决与串通投标的认定由评标委员会作出。"
_ORG = re.compile(r"(集团)?(股份)?有限(责任)?公司$|(集团)?股份公司$|集团$|公司$")
_REGION = re.compile(r"^(北京|上海|天津|重庆)市?|^[^\s]{2,3}?[省市]")
_TRADE = re.compile(r"(科技|技术|设备|仪器|工程|建设|建筑|贸易|实业|发展|信息|电子|电气|机电|环保|咨询|服务|智能|系统|网络|软件|医疗|器械|物资|供应链)+$")
_ARTICLE = re.compile(r"（([一二三四五六])）")


def short_names(bidders):
    cores = {}
    for bid, name in bidders.items():
        core = _ORG.sub("", name)
        local = _REGION.sub("", core)
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


def _cases(items, order, shorts, reviewed, baseline):
    pairs = defaultdict(list)
    for item in items:
        if item["kind"] != "clue" or item["review_status"] == "excluded" or item.get("reference_reason"):
            continue
        members = sorted(set(item["bidder_ids"]) & order.keys(), key=order.get)
        for row_index, row in enumerate(item["rows"]):
            strength, article = clue_strength(item, row)
            for a, b in combinations(members, 2):
                pairs[a, b].append({"item_id": str(item["id"]), "row": row_index, "type": item["type"],
                                    "tier": strength, "article": article, "fact": _fact(item, row),
                                    "bidder_ids": [a, b], "normal": item.get("normal"),
                                    "verified": item["review_status"] == "verified"})

    parent = {bid: bid for bid in order}

    def root(bid):
        while parent[bid] != bid:
            parent[bid] = parent[parent[bid]]
            bid = parent[bid]
        return bid

    qualified = {}
    for pair, rows in pairs.items():
        hard = any(r["tier"] == "certain" or r["verified"] for r in rows)
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
        # 已确认的关联仍按真实关系分配，不因组内另一对有确凿证据而把全组判为确凿。
        own = {p for p in qualified if set(p) <= members}
        certain = [list(p) for p in sorted(own, key=lambda p: (order[p[0]], order[p[1]])) if qualified[p]]
        key = "case:" + ":".join(ids)
        decision = reviewed.get(key)
        support = {r["item_id"] for r in evidence if r["tier"] != "reference"}
        if decision and decision["status"] == "verified" and support == set(decision["evidence"]):
            certain = [list(p) for p in sorted(own, key=lambda p: (order[p[0]], order[p[1]]))]
        tier = "certain" if len(certain) == len(own) else "verify"
        known = [r for r in evidence if r["tier"] == "certain"] if tier == "certain" else [r for r in evidence if r["tier"] != "reference"]
        if not known:
            known = [r for r in evidence if r["tier"] != "reference"]
        article = "".join(f"（{a}）" for a in "一二三四五六" if any(a in _ARTICLE.findall(r["article"] or "") for r in known))
        label = "围标：" if len(ids) >= 3 else ""
        title = f"{label}{'、'.join(shorts[i] for i in ids)} 疑似串通投标"
        if article:
            title += f" · 第四十条{article}"
        cases.append({"id": key, "bidder_ids": ids, "title": title, "article": article, "tier": tier, "certain_pairs": certain,
                      "evidence": list(chain.values()), "normal": list(dict.fromkeys(r["normal"] for r in evidence if r["normal"])),
                      "baseline": baseline})
    return sorted(cases, key=lambda c: (TIER[c["tier"]], [order[i] for i in c["bidder_ids"]]))


PRICE_ISSUES = {"multi_total", "sum_total", "ceiling", "low_price", "uppercase", "row_calc", "sum_rows", "manual_total_diff"}
_CLUE_LABEL = (("分项单价比值恒定", "比值恒定"), ("分项单价完全相同", "分项单价相同"), ("分项单价差额相同", "分项差额相同"),
               ("尾数", "尾数习惯相同"), ("等差", "总价等差"), ("等比", "总价等比"))


def pricing_summary(view, order, shorts, cases, entries, reviewed):
    """报价分析页与报告共用（bid-pricing.md 二）：结论句、每家结论标签（按 确凿 → 需核实 → 参考）、报价方面需核实的条目。"""
    pricing = view.get("pricing") or {}
    prices = pricing.get("bidders", {})
    items = {str(i["id"]): i for i in view["items"]}
    case_of = {e["item_id"]: c for c in cases for e in c["evidence"]}
    tags = {bid: [] for bid in order}
    facts = []
    rows_calc = defaultdict(list)
    for e in entries:
        item = items.get(e["item_id"] or "")
        if (item is None or item["kind"] != "issue" or item["type"] not in PRICE_ISSUES or item["review_status"] == "excluded"
                or reviewed.get(e["id"], {}).get("status") == "excluded"):
            continue
        if item["type"] == "row_calc":
            rows_calc[e["bidder_ids"][0]].append(e)
            continue
        title = e["title"]
        if item["type"] == "multi_total":
            count = len(prices.get(e["bidder_ids"][0], {}).get("totals_found", []))
            title = f"出现 {count} 个不同的投标总价" + ("（报价范围不同）" if "报价范围不同" in item["title"] else "")
        for bid in e["bidder_ids"]:
            tags[bid].append({"key": e["id"], "text": title, "tier": e["tier"]})
        facts.append((TIER[e["tier"]], "、".join(shorts[b] for b in e["bidder_ids"]), title))
    for bid, rows in rows_calc.items():
        tier = min((e["tier"] for e in rows), key=TIER.get)
        text = f"报价计算错误 {len(rows)} 处"
        tags[bid].append({"key": rows[0]["id"], "text": text, "tier": tier})
        facts.append((TIER[tier], shorts[bid], text))
    for item in items.values():
        if item["kind"] != "clue" or item["type"] != "pricing" or item["review_status"] == "excluded":
            continue
        label = next((short for key, short in _CLUE_LABEL if key in item["title"]), "报价有规律")
        case = case_of.get(str(item["id"]))
        tier = case["tier"] if case else "reference"
        ids = [b for b in item["bidder_ids"] if b in tags]
        for bid in ids:
            peers = "、".join(shorts[i] for i in ids if i != bid)
            tags[bid].append({"key": case["id"] if case else "item:" + str(item["id"]), "text": f"与{peers}{label}", "tier": tier})
        facts.append((TIER[tier], "、".join(shorts[b] for b in ids), label))
    for bid in tags:
        tags[bid].sort(key=lambda t: TIER[t["tier"]])
    facts.sort(key=lambda f: f[0])
    # 未识别出总价、或有多个总价 / 币种不同而未参加比较的，如实写明（不得写“未见问题”）
    missing = [b for b in order if b in prices and not prices[b].get("total") and not prices[b].get("totals_found")]
    apart = [b for b in order if b in prices and b not in missing and prices[b].get("not_compared")]
    compared = (pricing.get("compare") or {}).get("n", 0)
    parts = []
    if missing or apart:
        what = "、".join(s for s in (f"{len(missing)} 家未识别出投标总价" if missing else "",
                                     f"{len(apart)} 家有多个总价或币种不同" if apart else "") if s)
        if compared < 2:
            parts.append(f"{what}，报价比较与规律检查未进行；可录入唱标价")
        else:
            names = "、".join(shorts[b] for b in missing + apart)
            parts.append(f"{what}（{names}），报价比较不完整；可录入唱标价")
    if facts:
        listed = "；".join(f"{who} {what}" for _t, who, what in facts[:4])
        parts.append(f"{len(facts)} 项需处理：{listed}" + ("等" if len(facts) > 4 else ""))
    elif not missing and not apart:
        parts.append(f"{len(prices)} 家报价均未见问题")
    keys = {t["key"] for rows in tags.values() for t in rows}
    return {"lede": "。".join(parts) + "。" if parts else "", "tags": tags,
            "verify": [e["id"] for e in entries if e["tier"] == "verify" and e["id"] in keys]}


def _short_requirement(req):
    text = " ".join(req["text"].split())
    return text[:16] + ("…" if len(text) > 16 else "")


def build(view, bidders, *, local_only=False, model_state=None, feedback=None, second=None, baseline=None):
    feedback, second = feedback or {}, second or {}
    reviewed = feedback.get("reviews", {})
    order = {bid: i for i, bid in enumerate(bidders)}
    shorts = short_names(bidders)
    reqs = {str(r["id"]): r for r in view["requirements"]}
    items = view["items"]
    cells = {(str(c["requirement_id"]), str(c["bidder_id"])): c for c in view["cells"]}
    pricing = (view.get("pricing") or {}).get("bidders", {})
    cases = _cases(items, order, shorts, reviewed, baseline or {})
    case_items = {e["item_id"] for case in cases for e in case["evidence"]}
    certain, verify, reference = [], [], []
    layers = {"certain": certain, "verify": verify, "reference": reference}

    def add(key, ids, title, category, tier, *, article=None, item_id=None, requirement_id=None, case_id=None, status=None):
        decision = reviewed.get(key)
        if decision and decision["status"] == "excluded" or status == "excluded":
            tier = "reference"
        elif (not case_id and decision and decision["status"] == "verified") or (status == "verified" and tier == "verify"):
            tier = "certain"
        entry = {"id": key, "bidder_ids": ids, "title": title, "category": category, "tier": tier, "article": article,
                 "item_id": item_id, "requirement_id": requirement_id, "case_id": case_id}
        layers[tier].append(entry)

    linked = {}
    covered = set()
    for item in items:
        iid, ids, kind = str(item["id"]), item["bidder_ids"], item["type"]
        if not ids or any(i not in order for i in ids):
            continue
        req_id = str(item["requirement_id"]) if item["requirement_id"] else None
        if req_id in reqs and reqs[req_id].get("not_applicable"):
            continue
        if kind == "model_response" and req_id:
            linked[req_id, ids[0]] = item
            continue
        if item["kind"] != "issue":
            if iid not in case_items:
                add("item:" + iid, ids, item["title"], "串标线索", "reference", item_id=iid, status=item["review_status"])
            continue
        title, level = item["title"], item["level"]
        tier = "reference"
        if item["source"] == "local" and kind in LOCAL_CERTAIN and level == "veto":
            weak_ocr = any(p.get("low_ocr") for row in item["rows"] for places in row.get("places", {}).values() for p in places)
            tier = "verify" if weak_ocr else "certain"
        if kind == "cert_expired":
            tier = "verify"
        if kind == "row_calc":
            title = "报价计算错误 1 处"
        if kind == "low_price":
            low = pricing.get(ids[0], {}).get("low_price")
            if low:
                title = f"低于其余各家平均 {low.get('below_pct_display', low['below_pct'])}，需澄清是否低于成本"
        if kind == "ceiling":
            title = pricing.get(ids[0], {}).get("ceiling_reason") or title
        elif kind == "multi_total" and level == "veto":
            count = len(pricing.get(ids[0], {}).get("totals_found", []))
            if count > 1:
                title = f"出现 {count} 个不同的投标总价"
        elif kind == "uppercase":
            title = "大写与小写金额不一致"
        elif kind == "sum_total":
            title = "报价表合计与投标总价不符"
        category = "否决" if level == "veto" else "其他"
        add("item:" + iid, ids, title, category, tier, article=item["article"], item_id=iid, status=item["review_status"])
        if req_id and tier != "reference":
            covered.add((req_id, ids[0]))

    for (rid, bid), cell in cells.items():
        req = reqs.get(rid)
        if req is None or bid not in order or req["method"] == "manual" or req["group"] == "format" or req.get("not_applicable"):
            continue
        verdict = cell["verdict"]
        key = f"cell:{rid}:{bid}"
        if (rid, bid) in covered:
            continue
        record = second.get(key, {})
        item = linked.get((rid, bid))
        term = _short_requirement(req)
        if record.get("outcome") == "found":
            title = "二次核验找到响应，已改为响应"
        elif verdict == "no":
            title = f"未响应“{term}”（要求 {req['seq']}）"
        elif verdict == "missing":
            title = f"未找到“{term}”的响应内容（要求 {req['seq']}）"
        elif verdict == "yes" and cell["cite"] != "unverified":
            title = f"已响应“{term}”（要求 {req['seq']}）"
        else:
            title = f"“{term}”的响应证据需核实（要求 {req['seq']}）"
        serious = req["group"] in SERIOUS_GROUPS
        uncertain = verdict in {"no", "missing", "unclear"} or cell["cite"] == "unverified"
        tier = "certain" if record.get("outcome") == "confirmed" and verdict == "no" else "verify" if serious and uncertain else "reference"
        add(key, [bid], title, "否决" if serious else "响应性", tier, article=item["article"] if item else None,
            item_id=str(item["id"]) if item else None, requirement_id=rid, status=cell["review_status"])

    for case in cases:
        add(case["id"], case["bidder_ids"], case["title"], "串通", case["tier"], article=case["article"], case_id=case["id"])
    verify.sort(key=lambda e: ({"否决": 0, "串通": 1}.get(e["category"], 2), order[e["bidder_ids"][0]], e["title"]))
    rows = []
    for bid, name in bidders.items():
        hard = [e for e in certain if bid in e["bidder_ids"]]
        open_ = [e for e in verify if bid in e["bidder_ids"]]
        for case in cases:
            if case["tier"] == "verify" and any(bid in p for p in case["certain_pairs"]):
                hard.extend(e for e in open_ if e["case_id"] == case["id"])
        reasons = []
        for e in hard + [e for e in open_ if e not in hard]:
            title = e["title"]
            if e["case_id"]:
                case = next(c for c in cases if c["id"] == e["case_id"])
                relations = {i for p in case["certain_pairs"] if bid in p for i in p} - {bid}
                peers = "、".join(shorts[i] for i in case["bidder_ids"] if i != bid and (not relations or i in relations))
                title = f"与{peers}疑似串通" + (f"（第四十条{e['article']}）" if e["article"] else "")
            reasons.append({"id": e["id"], "title": title})
        rows.append({"id": bid, "name": name, "short": shorts[bid], "tier": "certain" if hard else "verify" if open_ else None,
                     "reasons": reasons, "total": pricing.get(bid, {}).get("total"), "manual_total": pricing.get(bid, {}).get("manual_total", False)})
    a, b = (sum(r["tier"] == t for r in rows) for t in ("certain", "verify"))
    parts = [s for s in (f"{a} 家建议否决（证据确凿）" if a else "", f"{b} 家需核实" if b else "") if s]
    lede = f"{len(rows)} 家投标人中，" + "，".join(parts) if parts else ""
    if cases:
        lede += ("；" if lede else "") + f"发现 {len(cases)} 组疑似串通投标"
    lede = lede + "。" if lede else f"{len(rows)} 家投标人均未发现否决情形与串通线索。"
    sub = ("本项目只做本地检查，未核对响应性要求。" if local_only else
           "模型核对未完成，结论可能变化。" if model_state != "done" or second.get("pending") else None)
    signed = feedback.get("signed")
    price = pricing_summary(view, list(bidders), shorts, cases, certain + verify + reference, reviewed)
    return {"pricing": price, "lede": lede, "sub": sub, "bidders": rows, "cases": cases, "certain": certain, "verify": verify, "reference": reference,
            "site": [r for r in reqs.values() if r["method"] == "manual" and r["group"] != "format" and not r.get("not_applicable")],
            "signed": signed["at"] if signed and signed["items"] == sorted(e["id"] for e in certain) else None,
            "reviews": reviewed, "second_calls": second.get("calls", 0)}
