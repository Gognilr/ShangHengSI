"""DOCX 解析（M2-07）：manifest.yaml 中的预期事实、T-01、T-02、损坏文件，以及若干构造的边界情况。"""
import io
import re
import zipfile
from pathlib import Path

import pytest
import yaml

from app.parsing.docx import (
    MSG_CORRUPT,
    MSG_ENCRYPTED,
    MSG_TOO_LARGE,
    WARN_EMBEDDED,
    WARN_EMPTY,
    WARN_NOTES,
    WARN_REVISIONS,
    format_number,
    manual_clause_no,
    parse_docx,
)
from app.parsing.model import ParseError, with_clause_no

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"
MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))["samples"]


def parse_sample(name: str, seq: int = 1):
    return parse_docx((FIXTURES / MANIFEST[name]["file"]).read_bytes(), seq)


# ---------------------------------------------------------------- manifest 中的预期事实


@pytest.mark.parametrize("name", list(MANIFEST))
def test_manifest_nodes(name):
    sample = MANIFEST[name]
    parsed = parse_sample(name)
    assert [n.loc for n in parsed.nodes] == [e["loc"] for e in sample["nodes"]]
    for node, expected in zip(parsed.nodes, sample["nodes"]):
        assert node.text == expected["text"], node.loc
        assert node.clause_no == expected["clause_no"], node.loc
        assert node.part == expected["part"], node.loc
        assert node.has_revision == expected["has_revision"], node.loc
        if "kind" in expected:
            assert node.kind == expected["kind"], node.loc
        elif node.part == "body":
            assert node.kind in ("paragraph", "list_item"), node.loc
    assert [n.ord for n in parsed.nodes] == list(range(1, len(parsed.nodes) + 1))
    assert parsed.has_revisions == sample["has_revisions"]
    assert parsed.page_count is None
    for text in sample.get("absent_texts", []):
        assert all(text not in n.text for n in parsed.nodes), text
    if "table_count" in sample:
        tables = {re.match(r"F1-T(\d+)-", n.loc).group(1) for n in parsed.nodes if n.kind == "table_cell"}
        assert len(tables) == sample["table_count"]
    for warning in sample.get("warnings_expected", []):
        assert warning in parsed.warnings
    if "warning" in sample:
        assert sample["warning"] in parsed.warnings
    if not sample["has_revisions"]:
        assert WARN_REVISIONS not in parsed.warnings


def test_file_seq_in_loc():
    parsed = parse_sample("parts_misc", seq=3)
    locs = [n.loc for n in parsed.nodes]
    assert all(loc.startswith("F3-") for loc in locs)
    assert {"F3-H1", "F3-FT1", "F3-CM1", "F3-TB1", "F3-T1-R4-C3-T1-R2-C2"} <= set(locs)


def test_parts_misc_parent_loc_and_warnings():
    nodes = {n.loc: n for n in parse_sample("parts_misc").nodes}
    assert nodes["F1-T1-R4-C3-T1-R1-C1"].parent_loc == "F1-T1-R4-C3"
    assert nodes["F1-T1-R1-C1"].parent_loc is None
    assert nodes["F1-CM1"].parent_loc == "F1-P2"
    assert nodes["F1-TB1"].parent_loc == "F1-P3"
    # 文本框文字不进入所在段落
    assert nodes["F1-P3"].text == "详见右侧备注。"
    assert sum("备注：本样本全部为虚构内容。" in n.text for n in nodes.values()) == 1


def test_clean_samples_have_no_extra_warnings():
    assert parse_sample("contract_basic").warnings == []
    assert parse_sample("numbering_auto").warnings == []


# ---------------------------------------------------------------- T-01、T-02


def test_t01_revisions_accepted():
    parsed = parse_sample("revisions")
    all_text = "\n".join(n.text for n in parsed.nodes)
    for deleted in ("预付 50%", "（本款已删除）", "60天", "1%"):
        assert deleted not in all_text
    assert "预付 30%" in all_text and "45天" in all_text and "0.5%" in all_text
    assert parsed.has_revisions is True
    assert parsed.warnings[0] == WARN_REVISIONS
    revised = {n.loc for n in parsed.nodes if n.has_revision}
    assert revised == {"F1-P2", "F1-P3", "F1-P5", "F1-T1-R1-C1"}


def test_t02_auto_numbering_clause_and_display():
    nodes = {n.loc: n for n in parse_sample("numbering_auto").nodes}
    first = nodes["F1-P2"]
    assert (first.clause_no, first.text, first.kind) == ("第1条", "总则", "list_item")
    assert "第1条" not in first.text
    # 显示和外发时编号在段落前；手写编号不重复添加
    assert with_clause_no(first.text, first.clause_no) == "第1条 总则"
    assert with_clause_no(nodes["F1-P6"].text, nodes["F1-P6"].clause_no) == "2.2.1 装船日期以提单签发日期为准。"
    handwritten = nodes["F1-P40"]
    assert with_clause_no(handwritten.text, handwritten.clause_no) == "第9条 手写编号条款"
    assert with_clause_no("普通段落", None) == "普通段落"
    # 项目符号段落是列表项，但没有 clause_no
    assert (nodes["F1-P38"].kind, nodes["F1-P38"].clause_no) == ("list_item", None)


# ---------------------------------------------------------------- 损坏和恶意文件


SECRET = "机密原文星河虚构123"


def _zip(parts: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_DECL = (
    f'xmlns:w="{W_NS}" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)
CT = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="xml" ContentType="application/xml"/></Types>')
REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def make_docx(body: str, *, numbering: str | None = None, styles: str | None = None,
              extra: dict[str, str] | None = None, rels: list[tuple[str, str, str]] | None = None) -> bytes:
    """用正文 XML 片段构造一个最小的 DOCX。"""
    parts: dict[str, str] = {
        "[Content_Types].xml": CT,
        "word/document.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:document {NS_DECL}><w:body>{body}</w:body></w:document>',
    }
    rel_list = list(rels or [])
    if numbering is not None:
        parts["word/numbering.xml"] = f'<w:numbering {NS_DECL}>{numbering}</w:numbering>'
        rel_list.append(("rIdN", "numbering", "numbering.xml"))
    if styles is not None:
        parts["word/styles.xml"] = f'<w:styles {NS_DECL}>{styles}</w:styles>'
        rel_list.append(("rIdS", "styles", "styles.xml"))
    parts.update(extra or {})
    parts["word/_rels/document.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="{i}" Type="{REL_BASE}{t}" Target="{tg}"/>' for i, t, tg in rel_list)
        + "</Relationships>"
    )
    return _zip(parts)


def P(text: str = "", ppr: str = "", runs: str | None = None) -> str:
    r = runs if runs is not None else (f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>' if text else "")
    return f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{r}</w:p>"


def R(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def texts(data: bytes) -> list[tuple[str, str]]:
    return [(n.loc, n.text) for n in parse_docx(data, 1).nodes]


def assert_parse_error(data: bytes, message: str = MSG_CORRUPT) -> None:
    with pytest.raises(ParseError) as exc:
        parse_docx(data, 1)
    assert str(exc.value) == message
    assert SECRET not in str(exc.value)
    # 不带原始异常链（异常消息里可能有原文片段）
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


def test_corrupt_inputs_raise_parse_error_without_text():
    good = (FIXTURES / "contract_basic.docx").read_bytes()
    assert_parse_error(b"")
    assert_parse_error(SECRET.encode("utf-8") * 10)
    assert_parse_error(good[: len(good) // 2])
    assert_parse_error(_zip({"[Content_Types].xml": CT, "other.xml": SECRET}))
    # document.xml 不是合法 XML，内容里有原文
    assert_parse_error(_zip({"[Content_Types].xml": CT, "word/document.xml": f"<w:document><w:body><w:p>{SECRET}"}))
    # 根元素不是 w:document
    assert_parse_error(_zip({"[Content_Types].xml": CT, "word/document.xml": f"<x>{SECRET}</x>"}))
    # numbering.xml 损坏同样按损坏处理
    assert_parse_error(make_docx(P(SECRET), extra={"word/numbering.xml": f"<w:numbering {NS_DECL}>{SECRET}"},
                                 rels=[("rIdN", "numbering", "numbering.xml")]))


def test_encrypted_or_legacy_word():
    assert_parse_error(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504, MSG_ENCRYPTED)


def test_dtd_and_entities_rejected():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "' + SECRET + '"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        f'<w:document {NS_DECL}><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>'
    )
    assert_parse_error(_zip({"[Content_Types].xml": CT, "word/document.xml": bomb}))
    xxe = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        f'<w:document {NS_DECL}><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>'
    )
    assert_parse_error(_zip({"[Content_Types].xml": CT, "word/document.xml": xxe}))
    # UTF-16 编码时字节检查看不到 DOCTYPE，靠解析后检查 docinfo
    utf16 = bomb.replace('encoding="UTF-8"', "").replace('<?xml version="1.0"?>', '<?xml version="1.0" encoding="UTF-16"?>')
    assert_parse_error(_zip({"[Content_Types].xml": CT, "word/document.xml": utf16.encode("utf-16")}))


def test_archive_too_large(monkeypatch):
    monkeypatch.setattr("app.parsing.docx.MAX_ARCHIVE_BYTES", 100)
    assert_parse_error(make_docx(P("x" * 200)), MSG_TOO_LARGE)


# ---------------------------------------------------------------- 构造的边界情况


def test_deleted_paragraph_mark_merges_into_next():
    body = (
        P(ppr='<w:rPr><w:del w:id="1" w:author="a" w:date="2026-01-01T00:00:00Z"/></w:rPr>', runs=R("前半句，"))
        + P("后半句。")
        + P("另一段。")
    )
    parsed = parse_docx(make_docx(body), 1)
    assert [(n.loc, n.text, n.has_revision) for n in parsed.nodes] == [
        ("F1-P1", "前半句，后半句。", True),
        ("F1-P2", "另一段。", False),
    ]
    assert parsed.has_revisions


def test_move_and_deleted_rows():
    body = (
        P(runs='<w:moveFrom w:id="1" w:author="a"><w:r><w:t>旧位置</w:t></w:r></w:moveFrom>' + R("保留"))
        + P(runs='<w:moveTo w:id="2" w:author="a"><w:r><w:t>新位置</w:t></w:r></w:moveTo>')
        + "<w:tbl>"
        + '<w:tr><w:trPr><w:del w:id="3" w:author="a"/></w:trPr><w:tc>' + P("删除的行") + "</w:tc></w:tr>"
        + "<w:tr><w:trPr><w:gridBefore w:val=\"1\"/></w:trPr><w:tc>" + P("第二列") + "</w:tc></w:tr>"
        + "</w:tbl>"
    )
    assert texts(make_docx(body)) == [("F1-P1", "保留"), ("F1-P2", "新位置"), ("F1-T1-R1-C2", "第二列")]


def test_fields_hyperlinks_sdt_and_math():
    body = P(runs=(
        R("见")
        + '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> HYPERLINK "http://x" </w:instrText></w:r>'
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + R("附件") + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        + '<w:hyperlink r:id="rId9">' + R("一") + "</w:hyperlink>"
        + "<w:sdt><w:sdtPr/><w:sdtContent>" + R("二") + "</w:sdtContent></w:sdt>"
        + '<w:r><w:tab/></w:r><m:oMath><m:r><m:t>x+1</m:t></m:r></m:oMath>'
    )) + "<w:sdt><w:sdtContent>" + P("块级内容控件") + "</w:sdtContent></w:sdt>"
    assert texts(make_docx(body)) == [("F1-P1", "见附件一二\tx+1"), ("F1-P2", "块级内容控件")]


def test_nested_cell_multi_paragraph_and_deep_nesting():
    def table(inner: str) -> str:
        return f"<w:tbl><w:tr><w:tc>{inner}</w:tc></w:tr></w:tbl>"

    # 单元格内多段用换行连接
    assert texts(make_docx(table(P("第一行") + P("") + P("第二行")))) == [("F1-T1-R1-C1", "第一行\n第二行")]
    # 嵌套很深时 loc 不超过 64 字符，更深的文字并入外层单元格
    inner = P("最深")
    for _ in range(8):
        inner = table(inner) + P()
    parsed = parse_docx(make_docx(inner), 1)
    assert all(len(n.loc) <= 64 for n in parsed.nodes)
    assert "最深" in "".join(n.text for n in parsed.nodes)


def test_numbering_from_style_restart_and_numid_zero():
    numbering = (
        '<w:abstractNum w:abstractNumId="0">'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="第%1条"/>'
        '<w:pStyle w:val="Clause"/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/>'
        '<w:lvlRestart w:val="0"/></w:lvl>'
        '</w:abstractNum><w:num w:numId="3"><w:abstractNumId w:val="0"/></w:num>'
    )
    styles = (
        '<w:style w:type="paragraph" w:styleId="Clause"><w:name w:val="Clause"/>'
        '<w:pPr><w:numPr><w:numId w:val="3"/></w:numPr></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="ClauseChild"><w:name w:val="Clause Child"/><w:basedOn w:val="Clause"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>'
    )
    body = (
        P("甲", '<w:pStyle w:val="Clause"/>')
        + P("乙", '<w:pStyle w:val="Clause"/><w:numPr><w:ilvl w:val="1"/></w:numPr>')
        + P("丙", '<w:pStyle w:val="ClauseChild"/>')
        + P("丁", '<w:pStyle w:val="Clause"/><w:numPr><w:ilvl w:val="1"/></w:numPr>')  # lvlRestart=0：不重新计数
        + P("取消编号", '<w:pStyle w:val="Clause"/><w:numPr><w:numId w:val="0"/></w:numPr>')
        + P("", '<w:pStyle w:val="Clause"/>')  # 空的编号段落不生成节点，但 Word 会显示编号，计数照常推进
        + P("戊", '<w:pStyle w:val="Clause"/>')
        + P("标题", '<w:pStyle w:val="1"/>')
        + P("未定义的编号", '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="99"/></w:numPr>')
    )
    nodes = parse_docx(make_docx(body, numbering=numbering, styles=styles), 1).nodes
    assert [(n.text, n.clause_no, n.kind) for n in nodes] == [
        ("甲", "第1条", "list_item"),
        ("乙", "1.1", "list_item"),
        ("丙", "第2条", "list_item"),
        ("丁", "2.2", "list_item"),
        ("取消编号", None, "paragraph"),
        ("戊", "第4条", "list_item"),
        ("标题", None, "heading"),
        ("未定义的编号", None, "paragraph"),
    ]


def test_footnotes_images_and_empty_warnings():
    footnotes = (
        f'<w:footnotes {NS_DECL}><w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:id="1"><w:p><w:r><w:t>脚注文字</w:t></w:r></w:p></w:footnote></w:footnotes>'
    )
    image = '<w:r><w:drawing><wp:inline><a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="rId5"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
    data = make_docx(P("正文") + P(runs=image), extra={"word/footnotes.xml": footnotes},
                     rels=[("rIdF", "footnotes", "footnotes.xml")])
    parsed = parse_docx(data, 1)
    assert [n.text for n in parsed.nodes] == ["正文"]
    assert parsed.warnings == [WARN_NOTES, WARN_EMBEDDED]
    assert parse_docx(make_docx(P()), 1).warnings == [WARN_EMPTY]


def test_manual_clause_no():
    assert manual_clause_no("第12条 付款") == "第12条"
    assert manual_clause_no("第十二条付款") == "第十二条"
    assert manual_clause_no("2.1 单价") == "2.1"
    assert manual_clause_no("2.1.3买方") == "2.1.3"
    assert manual_clause_no("2.1. 单价") == "2.1"
    assert manual_clause_no("1.5% 的违约金") is None
    assert manual_clause_no("2026.03.15 签订") is None
    assert manual_clause_no("1,250.00 美元") is None
    assert manual_clause_no("本合同第1条") is None


@pytest.mark.parametrize("fmt,n,expected", [
    ("decimal", 7, "7"), ("decimalZero", 3, "03"),
    ("chineseCounting", 1, "一"), ("chineseCounting", 10, "十"), ("chineseCounting", 11, "十一"),
    ("chineseCounting", 20, "二十"), ("chineseCounting", 101, "一百零一"), ("chineseCounting", 110, "一百一十"),
    ("chineseCountingThousand", 1010, "一千零一十"), ("chineseCountingThousand", 100000, "十万"),
    ("chineseLegalSimplified", 12, "壹拾贰"), ("ideographTraditional", 3, "丙"), ("ideographTraditional", 11, "甲"),
    ("upperLetter", 1, "A"), ("upperLetter", 27, "AA"), ("lowerLetter", 28, "bb"),
    ("upperRoman", 4, "IV"), ("lowerRoman", 14, "xiv"), ("decimalEnclosedCircle", 2, "②"),
    ("none", 5, ""), ("unknownFormat", 5, "5"),
])
def test_format_number(fmt, n, expected):
    assert format_number(fmt, n) == expected
