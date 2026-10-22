"""出口复检（M2-09，SPEC 9.8，Q-19）。"""
import itertools
import json
import os
from pathlib import Path

import pytest
import yaml

from app.config import get_settings
from app.db.models import RedactionMap, RedactionTerm, Task
from app.parsing.docx import parse_docx
from app.redaction import outbound
from app.redaction.engine import NodeIn, compute, outbound_text
from app.redaction.recheck import BlockReason, RecheckContext, recheck, recheck_context
from app.review.requirements import load_system_template, select_builtin
from app.security import crypto

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"
MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))["samples"]


def check(text: str, values=(), terms=()) -> list[tuple[str, int]]:
    return [(r.category, r.position) for r in recheck(text, real_values=values, terms=terms)]


def cats(text: str, values=(), terms=()) -> set[str]:
    return {c for c, _ in check(text, values, terms)}


# ---------------------------------------------------------------- 对照表真实值与词条


def test_real_values_block_with_position_only():
    text = "客户_A 即 Fictional  metals TRADING fze，简称 FMT。"
    reasons = recheck(text, real_values=["Fictional Metals Trading FZE", "FMT"], terms=[])
    assert reasons == [BlockReason("MAP", text.index("Fictional")), BlockReason("MAP", text.index("FMT"))]
    # 拦截原因只有类别和位置
    assert set(vars(reasons[0])) == {"category", "position"}
    assert "Fictional" not in repr(reasons)


def test_chinese_names_and_terms():
    assert check("由星河公司负责", ["星河公司"]) == [("MAP", 1)]
    assert check("项目代号凤凰计划", terms=["凤凰计划"]) == [("TERM", 4)]
    assert check("secret   PROJECT phoenix 已启动", terms=["Secret Project Phoenix"]) == [("TERM", 0)]


def test_short_caps_alias_is_case_sensitive_and_word_bounded():
    assert check("the format is fine", ["FMT"]) == []
    assert check("FMTX 与 fmt", ["FMT"]) == []
    assert check("交给 FMT 处理", ["FMT"]) == [("MAP", 3)]


def test_short_numeric_values_need_currency():
    # Q-19：短数字只在前后不是数字、且紧挨币种或单位时才算
    assert check("数量 100 MT，期限 100 天", ["100"]) == []
    assert check("编号 21000 与 1100", ["100"]) == []
    assert check("预付 USD 100", ["100"]) == [("AMOUNT", 7), ("MAP", 7)]
    assert check("共 100 元", ["100"]) == [("AMOUNT", 2), ("MAP", 2)]


def test_fill_brackets_count_as_currency():
    """币种与数字之间隔着填空括号仍算“带币种”（B-01、Q-134 ①）；代号的序号不算数字真实值。"""
    assert ("MAP", 4) in check("人民币【100】元", ["100"])
    assert ("MAP", 4) in check("服务款【62500】元", ["62500"])
    assert "AMOUNT" in cats("即人民币【93750】元")
    assert check("即人民币[金额_10]元（=合同总价的 30.0%）", ["10"]) == []
    assert check("第【10】期、一式【4】份", ["10", "4"]) == []


def test_placeholders_masked_only_for_real_values():
    """B-04、Q-134 ②：只遮盖传入的确切代号；格式化与金额重扫仍在原文上做；默认不遮盖。"""
    text = "人员_1：【[电话]】；[地址：中国]"
    assert check(text, ["电话", "中国"]) == [("MAP", 7), ("MAP", 16)]
    codes = ["人员_1", "[电话]", "[地址：中国]"]
    assert recheck(text, real_values=["电话", "中国"], terms=["电话"], placeholders=codes) == []
    # 代号以外的出现照常拦截；未传入的代号不遮盖
    reasons = recheck("联系电话：【[电话]】，[邮箱]", real_values=["电话", "邮箱"], terms=[], placeholders=codes)
    assert [(r.category, r.position) for r in reasons] == [("MAP", 2), ("MAP", 13)]
    # 重扫不受遮盖影响
    assert {r.category for r in recheck("[电话] 13912345678", real_values=[], terms=[], placeholders=codes)} == {"PHONE"}


def test_postcode_real_values_block_and_code_passes():
    """R1-11（Q-139 ①）：邮编真实值在对照表中时拦截；代号“[邮编]”本身不拦截；重扫不加邮编（无标签的 6 位数字太常见）。"""
    text = "补充：邮编 251500，Post Code sw1a  1aa"
    assert check(text, ["251500", "SW1A 1AA"]) == [("MAP", text.index("251500")), ("MAP", text.index("sw1a"))]
    sent = "通讯地址：[地址：中国] 邮政编码：【[邮编]】"
    assert recheck(sent, real_values=["251500", "邮编"], terms=[], placeholders=["[地址：中国]", "[邮编]"]) == []
    assert check(sent, ["251500"]) == []
    assert check("邮编：251500，批次 330512") == []


def test_numeric_values_rewritten_forms():
    # 长数字前后不是数字即拦截；去掉空格、短横线后相同也拦截
    assert ("MAP", 3) in check("账号：6222000011112222333", ["6222 0000 1111 2222 333"])
    assert check("编号 1,250.00 号", ["1,250.00"]) == [("MAP", 3)]
    assert check("编号 11,250.00 号", ["1,250.00"]) == []
    # 千分位写法不同、但带币种且数值相同
    assert ("MAP", 7) in check("总价 USD 400000", ["400,000.00"])
    # Q-134 ③：≥ 10000 或带小数的金额，不带币种也按数值拦截（宁可误拦）；小整数仍须带币种
    assert "MAP" in cats("数量 400000 吨", ["400,000.00"])
    assert check("[F1-T1-R128-C5] 1,150,000.00", ["1150000"]) == [("MAP", 16)]
    assert check("小计 440000", ["440,000.00"]) == [("MAP", 3)]
    assert check("即 66618.90", ["66618.9"]) == [("MAP", 2)]
    assert check("付款 22206.3", ["22,206.30"]) == [("MAP", 3)]
    assert check("2023 年，期限 30 天，第 9999 号", ["2023", "30", "9999"]) == []
    # 小于 10000 的带小数金额仍须带币种：条款编号“1.8”“2.3”不拦截
    assert check("1.8 每个视频头必须接地；2.3 线路地下敷设", ["1.80", "2.30"]) == []
    assert check("单价 USD 1.8", ["1.80"]) == [("AMOUNT", 7), ("MAP", 7)]
    assert check("[金额_10000]", ["10000"]) == []


def test_rescan_unreplaced_formatted_info():
    assert cats("电话 13912345678") == {"PHONE"}
    assert cats("Tel: +86 21 5555 1234") == {"PHONE"}
    assert cats("邮箱 a.b@example.cn") == {"EMAIL"}
    assert cats("IBAN: GB82WEST12345698765432") == {"ACCOUNT"}
    assert cats("账号：1234 5678 9012") == {"ACCOUNT"}
    assert cats("统一社会信用代码 91310000MA1BCDEF2K") == {"IDNO"}
    assert cats("金额 USD 1,250.00") == {"AMOUNT"}
    assert cats("人民币捌拾万元整") == {"AMOUNT"}
    assert cats("50万元") == {"AMOUNT"}
    # 9.8 只列电话、邮箱、账号、证件号、带币种金额：网址、SWIFT、编号、无币种数字不拦截
    assert check("网址 www.example.com，SWIFT: ABCDCNBJ，合同号：HT-1，数量 320 MT，硫含量≤0.5%，HS 27131200") == []


def test_placeholders_and_prompts_pass():
    text = ("【主体说明】我方=卖方；客户_A：买方，阿联酋，贸易商\n[F1-P15] 2.1 单价为 USD [单价_1]/MT，总价 [金额_1] 美元，"
            "电话 [电话]，账号 [账号_1]，SWIFT: [SWIFT_2]，地址 [地址：阿联酋]，数量 [数量_3] MT，人员_12，编号_3，＜/document＞，"
            "预付 USD [金额_2]（=合同总价的 30.0%）")
    assert check(text) == []
    # 系统提示词和全部内置审核要求本身不能触发拦截
    _version, template = load_system_template()
    assert check(template) == []
    for doc_type, stance, source in itertools.product(
            ("contract", "quotation", "order", "inquiry", "letter"), ("sales", "purchase", "other"),
            ("outgoing", "incoming")):
        for req in select_builtin(doc_type, stance, source):
            assert check(req.text) == [], req.id


@pytest.mark.parametrize("name", list(MANIFEST))
def test_redacted_samples_pass_and_leaks_block(name):
    parsed = parse_docx((FIXTURES / MANIFEST[name]["file"]).read_bytes(), 1)
    nodes = [NodeIn(n.loc, n.text, n.clause_no) for n in parsed.nodes if n.part == "body"]
    r = compute(nodes, "sales")
    text = outbound.build(
        [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in r.parties],
        [(n.loc, n.clause_no, r.redacted[n.loc]) for n in nodes],
    )
    values = list(r.mapping.values())
    assert check(text, values) == []
    # 混入任何一个真实值都拦截
    for value in values:
        assert check(f"{text}\n补充：{value}", values), value


GOLDEN = Path(__file__).resolve().parent / "fixtures" / "redaction_golden"


@pytest.mark.parametrize("name", sorted(p.stem for p in GOLDEN.glob("*.txt")))
def test_golden_persons_addresses_accounts_block(name):
    """M4-04：人名、地址、带序号的账号和 SWIFT、邮编（R1-11）的真实值同样进入出口复检；脱敏后的外发文本（含金额比例、报价汇总）可以发送。"""
    ann = yaml.safe_load((GOLDEN / f"{name}.yaml").read_text(encoding="utf-8"))
    lines = (GOLDEN / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    if ann.get("loc_prefixed"):
        nodes = [NodeIn(*line.split(" ", 1)) for line in lines if line.strip()]
    else:
        nodes = [NodeIn(f"F1-P{i}", line) for i, line in enumerate(lines, start=1) if line.strip()]
    r = compute(nodes, ann["stance"], main_prefix="F1-")
    parties = [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in r.parties]
    text = outbound_text(nodes, parties, r.redacted)
    values = list(r.mapping.values())
    assert check(text, values) == []
    for item in ann["must_replace"]:
        if item["category"] in ("PERSON", "ADDR", "ACCOUNT", "SWIFT", "POSTCODE"):
            assert "MAP" in cats(text + "\n补充说明：" + item["text"], values), item


def test_recheck_context_repr_hides_values():
    ctx = RecheckContext(real_values=["星河虚构进出口有限公司"], terms=["凤凰计划"])
    assert "星河" not in repr(ctx) and "凤凰" not in repr(ctx)


# ---------------------------------------------------------------- 数据库


@pytest.fixture
def master_key(tmp_path, monkeypatch):
    key = tmp_path / "master_key"
    key.write_bytes(os.urandom(32))
    monkeypatch.setenv("APP_MASTER_KEY_FILE", str(key))
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()
    yield
    get_settings.cache_clear()
    crypto.get_master_key.cache_clear()


def test_recheck_context_from_db(db_sessionmaker, make_user, master_key):
    owner = make_user("alice")
    with db_sessionmaker() as db:
        task = Task(owner_id=owner.id, title="t.docx", stance="sales", source="outgoing", policy="ai",
                    status="awaiting_confirm")
        db.add(task)
        db.flush()
        mapping = {"客户_A": "Fictional Metals Trading FZE", "客户_A#1": "FMT", "[电话]#1": "13800138000"}
        db.add(RedactionMap(task_id=task.id, map_enc=crypto.encrypt_text(json.dumps(mapping, ensure_ascii=False))))
        db.add(RedactionTerm(kind="learned", term_enc=crypto.encrypt_text("星河虚构进出口有限公司"), term_hash="a" * 64,
                             category="ORG"))
        db.add(RedactionTerm(kind="custom", term_enc=crypto.encrypt_text("停用词"), term_hash="b" * 64,
                             category="CUSTOM", enabled=False))
        db.commit()
        ctx = recheck_context(db, task.id)
        assert sorted(ctx.real_values) == sorted(mapping.values())  # 含“代号#序号”的值
        assert ctx.terms == ["星河虚构进出口有限公司"]
        assert recheck_context(db, None).real_values == []
        assert recheck("FMT 与星河虚构进出口有限公司", real_values=ctx.real_values, terms=ctx.terms) == [
            BlockReason("MAP", 0), BlockReason("TERM", 5)]
