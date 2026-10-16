"""脱敏回归测试集（SPEC 23.3，M2-17）。

tests/fixtures/redaction_golden/ 下每份虚构样本（.txt，每行一个正文节点）配一份同名 .yaml 标注：
  must_replace —— 必须被替换的文字和类别，召回率要求 100%；
  known_gaps   —— 已知尚未识别的写法（记入 open_questions）；仍漏替换时测试通过，修好后测试失败，提醒移入 must_replace；
  must_keep    —— 必须保留的内容（国家、港口、贸易术语、百分比、日期、HS 编码、产品名、规格指标等），
                  既要出现在外发文本里，也不能被任何替换项覆盖。
M2 只标注 9.3 第 1–5 步的类别；人名、地址留到 M4，不在标注中。每次修改识别规则或词典都必须通过。
"""
from pathlib import Path

import pytest
import yaml

from app.redaction import outbound
from app.redaction.engine import NodeIn, compute

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "redaction_golden"
SAMPLES = sorted(p.stem for p in GOLDEN.glob("*.txt"))
# 公司名称替换后，我方记为 OUR，其他方记为 ORG，标注里统一写 ORG
COMPATIBLE = {"ORG": {"ORG", "OUR"}}


def load(name: str):
    lines = (GOLDEN / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    nodes = [NodeIn(f"F1-P{i}", line) for i, line in enumerate(lines, start=1) if line.strip()]
    ann = yaml.safe_load((GOLDEN / f"{name}.yaml").read_text(encoding="utf-8"))
    result = compute(nodes, ann["stance"])
    text = outbound.build(
        [outbound.PartyInfo(p.code, p.role_label, p.country, p.org_type, p.is_our_party) for p in result.parties],
        [(n.loc, n.clause_no, result.redacted[n.loc]) for n in nodes],
    )
    return nodes, ann, result, text


def occurrences(nodes: list[NodeIn], needle: str):
    for n in nodes:
        start = n.text.find(needle)
        while start >= 0:
            yield n.loc, start, start + len(needle)
            start = n.text.find(needle, start + 1)


def test_golden_set_covers_all_doc_types():
    assert len(SAMPLES) >= 5
    assert {"contract", "quotation", "order", "inquiry", "letter"} <= set(SAMPLES)


@pytest.mark.parametrize("name", SAMPLES)
def test_must_replace_recall(name):
    nodes, ann, result, text = load(name)
    for item in ann["must_replace"]:
        found = list(occurrences(nodes, item["text"]))
        assert found, f"标注文字不在样本中：{item['text']}"  # 标注本身要正确
        allowed = COMPATIBLE.get(item["category"], {item["category"]})
        for loc, start, end in found:
            covering = [i for i in result.items if i.loc == loc and i.char_start <= start and i.char_end >= end]
            assert covering, f"漏替换：{item['text']}（{loc}）"
            assert covering[0].category in allowed, f"类别不符：{item['text']} → {covering[0].category}"
        assert item["text"] not in text, f"外发文本仍含：{item['text']}"


@pytest.mark.parametrize("name", SAMPLES)
def test_known_gaps_still_open(name):
    nodes, ann, result, text = load(name)
    for gap in ann.get("known_gaps", []):
        assert gap["text"] in text, f"已知缺口已修复，请把“{gap['text']}”移入 must_replace 并删除本条"


@pytest.mark.parametrize("name", SAMPLES)
def test_must_keep_not_replaced(name):
    nodes, ann, result, text = load(name)
    for keep in ann["must_keep"]:
        found = list(occurrences(nodes, keep))
        assert found, f"标注文字不在样本中：{keep}"
        assert keep in text, f"外发文本丢失：{keep}"
        for loc, start, end in found:
            hit = [i for i in result.items if i.loc == loc and i.char_start < end and i.char_end > start]
            assert not hit, f"误替换：{keep}（{loc}）→ {hit[0].placeholder}"


@pytest.mark.parametrize("name", SAMPLES)
def test_mapping_restores_every_replacement(name):
    """对照表包含每个被替换的真实值（还原的前提）。"""
    nodes, _, result, _ = load(name)
    values = set(result.mapping.values())
    by_loc = {n.loc: n.text for n in nodes}
    for it in result.items:
        assert by_loc[it.loc][it.char_start:it.char_end] in values
