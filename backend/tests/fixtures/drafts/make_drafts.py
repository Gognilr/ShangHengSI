"""生成多稿对比的虚构合同三稿（V35-D-02，设计 docs/v35/design/diff.md 第七节）。

用法（在 backend 目录、测试镜像中）：python tests/fixtures/drafts/make_drafts.py
内容全部虚构。条款用自动编号（List Number），中间插入一条时后面的编号顺延、正文不变。

第 2 稿（对方改回）：
  * 开着修订改：付款期限 30 日 → 60 日、违约金 0.5% → 0.1%；
  * 关掉修订悄悄改：争议解决地“甲方所在地”→“乙方所在地”、删掉保密期限一条、在交货期限后插入质量保证一条。
第 3 稿：
  * 开着修订把付款期限改回 30 日；
  * 关掉修订悄悄把交货期限 45 日改为 60 日；
  * 最后一条只改标点（逗号改分号）。
"""
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

OUT = Path(__file__).resolve().parent
AUTHOR, DATE = "对方经办人", "2026-11-18T09:00:00Z"
TITLE = "设备采购合同"
PARTIES = ["甲方（买方）：华岳精工设备有限公司", "乙方（卖方）：临川远航贸易有限公司"]
BASE = [
    "合同标的：乙方向甲方供应数控车床 3 台，规格与配置见附件一《设备清单》。",
    "合同价款：合同总价为人民币 450,000 元（含增值税），包含运输、安装与调试费用。",
    "付款方式：甲方在设备验收合格后 30 日内支付全部货款。",
    "交货期限：乙方应在合同签订后 45 日内将设备交付至甲方指定地点。",
    "违约责任：乙方逾期交货的，每逾期一日按合同总价的 0.5% 向甲方支付违约金。",
    "保密条款：双方对本合同内容负有保密义务，保密期限为合同终止后三年。",
    "争议解决：因本合同引起的争议，双方应协商解决；协商不成的，提交甲方所在地人民法院诉讼解决。",
    "本合同一式两份，双方各执一份，自双方签字盖章之日起生效。",
]
TABLE = [["设备名称", "型号", "数量", "单价（元）"], ["数控车床", "CK6150", "3", "150,000"]]
QUALITY = "质量保证：乙方提供自验收合格之日起十二个月的免费质量保证。"
_id = [200]


def _rid():
    _id[0] += 1
    return str(_id[0])


def _run(text):
    r = etree.SubElement(etree.Element(qn("w:r")), qn("w:t"))
    r.text = text
    r.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r.getparent()


def _wrap(tag, text, deleted=False):
    el = etree.Element(qn(tag), {qn("w:id"): _rid(), qn("w:author"): AUTHOR, qn("w:date"): DATE})
    run = _run(text)
    if deleted:
        run[0].tag = qn("w:delText")
    el.append(run)
    return el


def tracked(paragraph, before, old, new, after):
    """把段落内容改为：before + <del>old</del><ins>new</ins> + after（留修订痕迹）。"""
    p = paragraph._p
    for r in list(p.findall(qn("w:r"))):
        p.remove(r)
    p.append(_run(before))
    p.append(_wrap("w:del", old, deleted=True))
    p.append(_wrap("w:ins", new))
    p.append(_run(after))


def build(clauses, edits=()):
    doc = Document()
    doc.add_heading(TITLE, level=1)
    for line in PARTIES:
        doc.add_paragraph(line)
    paragraphs = [doc.add_paragraph(text, style="List Number") for text in clauses]
    table = doc.add_table(rows=len(TABLE), cols=len(TABLE[0]))
    for r, row in enumerate(TABLE):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    for index, before, old, new, after in edits:
        tracked(paragraphs[index], before, old, new, after)
    return doc


def main():
    build(BASE).save(OUT / "设备采购合同_第1稿.docx")
    # 第 2 稿：暗改（直接写进正文）+ 留痕修改（edits）
    second = [c for c in BASE if not c.startswith("保密条款")]
    second.insert(4, QUALITY)
    second = [c.replace("甲方所在地人民法院", "乙方所在地人民法院") for c in second]
    pay, penalty = second.index(BASE[2]), next(i for i, c in enumerate(second) if c.startswith("违约责任"))
    build(second, [
        (pay, "付款方式：甲方在设备验收合格后 ", "30", "60", " 日内支付全部货款。"),
        (penalty, "违约责任：乙方逾期交货的，每逾期一日按合同总价的 ", "0.5%", "0.1%", " 向甲方支付违约金。"),
    ]).save(OUT / "设备采购合同_第2稿.docx")
    # 第 3 稿：以第 2 稿接受修订后的文字为底
    third = [c.replace(" 30 日内", " 60 日内") if c.startswith("付款方式") else c for c in second]
    third = [c.replace("0.5%", "0.1%") for c in third]
    third = [c.replace("45 日内", "60 日内") for c in third]  # 暗改交货期限
    third = [c.replace("一式两份，双方各执一份，", "一式两份；双方各执一份；") for c in third]  # 只改标点
    pay = next(i for i, c in enumerate(third) if c.startswith("付款方式"))
    build(third, [(pay, "付款方式：甲方在设备验收合格后 ", "60", "30", " 日内支付全部货款。")]).save(
        OUT / "设备采购合同_第3稿.docx")


if __name__ == "__main__":
    main()
