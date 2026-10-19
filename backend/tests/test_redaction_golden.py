"""脱敏回归测试集（SPEC 23.3，M2-17、M4-04）。

tests/fixtures/redaction_golden/ 下每份虚构样本（.txt，每行一个正文节点）配一份同名 .yaml 标注：
  loc_prefixed —— 为 true 时每行写成“loc 文字”（如 XLSX 单元格“F1-S1-E8 USD 1250”、OCR 文字块“F1-PG1-B4 …”），
                  按该 loc 构造节点，用于检验表格列提示；否则节点依次编为 F1-P1、F1-P2……
  must_replace —— 必须被替换的文字和类别（含人名 PERSON、地址 ADDR），召回率要求 100%；
  known_gaps   —— 已知尚未识别的写法（记入 open_questions）；仍漏替换时测试通过，修好后测试失败，提醒移入 must_replace；
  must_keep    —— 必须保留的内容（国家、港口、贸易术语、百分比、日期、HS 编码、产品名、规格指标等），
                  既要出现在外发文本里，也不能被任何替换项覆盖，也不能被列为疑似遗漏；
                  落在某个 must_replace 文字内部的出现（如地址中的国家名）不检查；
  must_suspect —— 必须列为疑似遗漏的文字（9.5、Q-69）：自动替换识别不了、需要用户在第二步处理的内容。
每次修改识别规则或词典都必须通过。
"""
from pathlib import Path

import pytest
import yaml

from app.redaction import outbound
from app.redaction.engine import NodeIn, compute, outbound_text

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "redaction_golden"
SAMPLES = sorted(p.stem for p in GOLDEN.glob("*.txt"))
# 公司名称替换后，我方记为 OUR，其他方记为 ORG，标注里统一写 ORG
COMPATIBLE = {"ORG": {"ORG", "OUR"}}


def load(name: str):
    lines = (GOLDEN / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    ann = yaml.safe_load((GOLDEN / f"{name}.yaml").read_text(encoding="utf-8"))
    if ann.get("loc_prefixed"):
        nodes = [NodeIn(*line.split(" ", 1)) for line in lines if line.strip()]
    else:
        nodes = [NodeIn(f"F1-P{i}", line) for i, line in enumerate(lines, start=1) if line.strip()]
    result = compute(nodes, ann["stance"], main_prefix="F1-")
    parties = [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in result.parties]
    # 逐节点的外发文本（不汇总报价明细表），用于逐条检查；另附实际外发的文本（报价明细表已汇总）
    text = outbound.build(parties, [(n.loc, n.clause_no, result.redacted[n.loc]) for n in nodes])
    sent = outbound_text(nodes, parties, result.redacted)
    return nodes, ann, result, text, sent


def occurrences(nodes: list[NodeIn], needle: str):
    for n in nodes:
        start = n.text.find(needle)
        while start >= 0:
            yield n.loc, start, start + len(needle)
            start = n.text.find(needle, start + 1)


def test_golden_set_covers_all_doc_types():
    assert len(SAMPLES) >= 5
    assert {"contract", "quotation", "order", "inquiry", "letter", "quotation_xlsx", "q56_boundary",
            "q69_ocr_account"} <= set(SAMPLES)
    categories = {i["category"] for name in SAMPLES for i in load(name)[1]["must_replace"]}
    assert {"PERSON", "ADDR"} <= categories


@pytest.mark.parametrize("name", SAMPLES)
def test_must_replace_recall(name):
    nodes, ann, result, text, sent = load(name)
    for item in ann["must_replace"]:
        found = list(occurrences(nodes, item["text"]))
        assert found, f"标注文字不在样本中：{item['text']}"  # 标注本身要正确
        allowed = COMPATIBLE.get(item["category"], {item["category"]})
        for loc, start, end in found:
            covering = [i for i in result.items if i.loc == loc and i.char_start <= start and i.char_end >= end]
            assert covering, f"漏替换：{item['text']}（{loc}）"
            assert covering[0].category in allowed, f"类别不符：{item['text']} → {covering[0].category}"
        assert item["text"] not in text, f"外发文本仍含：{item['text']}"
        assert item["text"] not in sent, f"外发文本仍含：{item['text']}"


@pytest.mark.parametrize("name", SAMPLES)
def test_known_gaps_still_open(name):
    nodes, ann, result, text, _ = load(name)
    for gap in ann.get("known_gaps", []):
        assert gap["text"] in text, f"已知缺口已修复，请把“{gap['text']}”移入 must_replace 并删除本条"


def _inside_must_replace(nodes, ann, loc, start, end) -> bool:
    return any(l == loc and s <= start and end <= e
               for item in ann["must_replace"] for l, s, e in occurrences(nodes, item["text"]))


@pytest.mark.parametrize("name", SAMPLES)
def test_must_keep_not_replaced(name):
    nodes, ann, result, text, _ = load(name)
    for keep in ann["must_keep"]:
        found = list(occurrences(nodes, keep))
        assert found, f"标注文字不在样本中：{keep}"
        assert keep in text, f"外发文本丢失：{keep}"
        for loc, start, end in found:
            if _inside_must_replace(nodes, ann, loc, start, end):
                continue
            hit = [i for i in result.items if i.loc == loc and i.char_start < end and i.char_end > start]
            assert not hit, f"误替换：{keep}（{loc}）→ {hit[0].placeholder}"
            listed = [s for s in result.suspects if s.loc == loc and s.char_start < end and s.char_end > start]
            assert not listed, f"误列为疑似遗漏：{keep}（{loc}，{listed[0].reason}）"


@pytest.mark.parametrize("name", SAMPLES)
def test_must_suspect_listed(name):
    nodes, ann, result, _, _ = load(name)
    for needle in ann.get("must_suspect", []):
        found = list(occurrences(nodes, needle))
        assert found, f"标注文字不在样本中：{needle}"
        for loc, start, end in found:
            assert any(s.loc == loc and s.char_start <= start and s.char_end >= end for s in result.suspects), \
                f"未列为疑似遗漏：{needle}（{loc}）"


@pytest.mark.parametrize("name", SAMPLES)
def test_mapping_restores_every_replacement(name):
    """对照表包含每个被替换的真实值（还原与出口复检的前提）。"""
    nodes, _, result, _, _ = load(name)
    values = set(result.mapping.values())
    by_loc = {n.loc: n.text for n in nodes}
    for it in result.items:
        assert by_loc[it.loc][it.char_start:it.char_end] in values
