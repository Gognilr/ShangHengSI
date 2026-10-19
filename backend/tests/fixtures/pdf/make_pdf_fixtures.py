"""生成 PDF 虚构样本和 manifest.yaml（M3-01）。

用法（在测试镜像中，backend 目录挂载为 /srv/backend）：
    python tests/fixtures/pdf/make_pdf_fixtures.py
依赖 reportlab、Pillow、PyYAML 和中文字体 fonts-wqy-microhei（只在测试镜像中安装，运行镜像没有）。
reportlab 使用 invariant 模式（固定时间戳和文件标识），扫描页的噪点用固定种子，因此重复生成得到逐字节相同的文件。

内容全部虚构：公司、人名、电话、邮箱、账号、SWIFT 均为编造，不对应任何真实对象。

生成的样本：
  text_contract.pdf    文字版合同，2 页：签约方、联系方式、金额、条款编号、表格（含横向合并的合计行）、页码行
  scanned_contract.pdf 扫描版合同，2 页，只有图片没有文字：第 1 页清晰，第 2 页故意模糊、加噪点（低置信）
  mixed.pdf            3 页：文字页、扫描页（清晰）、文字页
  rotated.pdf          2 页：第 1 页正常；第 2 页 /Rotate 90，内容在显示方向上是正的（横向页面）
  encrypted.pdf        设置了打开密码的 PDF（解析应失败）
超过 200 页的 PDF 由测试临时生成，不提交。

manifest.yaml 由本脚本根据同一份数据生成，写明每页是文字页还是扫描页、关键文本块、表格单元格、
必须出现的警告和必须被替换的敏感项。文字页的 bbox 为 [x0, top, x1, bottom]（原点在左上角、PDF 点、
显示方向），只是近似值（字体度量不同会有几个点的差别），测试按 bbox_tolerance 比较。
文字页的块文字与解析结果比较时忽略空白差异；OCR 页只断言关键词（ocr_keywords，已去掉空格，比较前先去掉识别结果中的空白）、置信度高低和警告。
"""
import io
import random
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib import pdfencrypt
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT_DIR = Path(__file__).resolve().parent
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
FONT = "WQY"
W, H = A4  # 595.27 x 841.89
BBOX_TOLERANCE = 8
ENCRYPT_USER_PASSWORD = "fictional-user-pass"

pdfmetrics.registerFont(TTFont(FONT, FONT_PATH, subfontIndex=0))


# ---------------------------------------------------------------- 文字页布局


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿" or ch in "，。；：、？！（）“”‘’《》"


def join_lines(lines: list[str]) -> str:
    """与设计 M3.md 第四节一致：两侧都是汉字或中文标点时直接相连，否则以一个空格相连。"""
    out = lines[0]
    for nxt in lines[1:]:
        out += ("" if _is_cjk(out[-1]) and _is_cjk(nxt[0]) else " ") + nxt
    return out


class TextPage:
    """按“页面显示方向、原点在左上角”的坐标绘制一页文字，并记录每个块的预期 bbox。"""

    def __init__(self, c: canvas.Canvas, width: float, height: float, number: int):
        self.c, self.width, self.height, self.number = c, width, height, number
        self.y = 72.0
        self.blocks: list[dict] = []

    def block(self, lines: list[str], size: float = 11, clause_no: str | None = None, x: float = 72, gap: float = 16):
        """一个文本块；多行时行距 size+5（同一块），块之间留出 gap 的空隙。"""
        top = self.y
        width = 0.0
        for ln in lines:
            self.c.setFont(FONT, size)
            self.c.drawString(x, self.height - self.y - size, ln)
            width = max(width, pdfmetrics.stringWidth(ln, FONT, size))
            self.y += size + 5
        bottom = self.y - 5
        self.y += gap
        self.blocks.append({
            "page": self.number,
            "kind": "paragraph",
            "text": join_lines(lines),
            "clause_no": clause_no,
            "bbox": [round(x, 1), round(top, 1), round(x + width, 1), round(bottom, 1)],
        })

    def footer(self, text: str):
        self.y = self.height - 54
        self.block([text], size=9, x=self.width / 2 - 40)


def table(page: TextPage, top: float, col_edges: list[float], rows: list[list[str | None]], merged_total: bool) -> dict:
    """画带完整网格线的表格（pdfplumber 的 lines 策略能识别）。
    merged_total=True 时最后一行的前几列合并：其内部竖线不画，值在合并区域左上角。
    返回 manifest 用的表格描述。"""
    c, row_h = page.c, 24
    n_rows = len(rows)
    bottom = top + n_rows * row_h
    to_y = lambda v: page.height - v  # noqa: E731
    for i in range(n_rows + 1):
        c.line(col_edges[0], to_y(top + i * row_h), col_edges[-1], to_y(top + i * row_h))
    for j, x in enumerate(col_edges):
        last_row_merged_inner = merged_total and 0 < j < len(col_edges) - 2
        y_end = bottom - row_h if last_row_merged_inner else bottom
        c.line(x, to_y(top), x, to_y(y_end))
    cells = []
    for i, row in enumerate(rows):
        for j, text in enumerate(row):
            if text is None:
                continue
            c.setFont(FONT, 9)
            c.drawString(col_edges[j] + 4, to_y(top + i * row_h + 16), text)
            cells.append({"row": i + 1, "col": j + 1, "text": text})
    page.y = bottom + 20
    return {"page": page.number, "table": 1, "bbox": [col_edges[0], top, col_edges[-1], bottom], "cells": cells}


# ---------------------------------------------------------------- 扫描页（Pillow 画字）


def draw_scan_page(lines: list[tuple[str, int]], blur: float = 0.0, noise: int = 0, seed: int = 0) -> Image.Image:
    """lines: (文字, 字号 px)。页面 1240x1754（A4 在 150 DPI 下的大小），灰度。"""
    img = Image.new("L", (1240, 1754), 255)
    d = ImageDraw.Draw(img)
    y = 140
    for text, size in lines:
        font = ImageFont.truetype(FONT_PATH, size, index=0)
        d.text((110, y), text, fill=20, font=font)
        y += int(size * 1.9) + (30 if size > 40 else 0)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        rng = random.Random(seed)
        px = img.load()
        for yy in range(img.height):
            for xx in range(img.width):
                v = px[xx, yy] + rng.randint(-noise, noise)
                px[xx, yy] = max(0, min(255, v))
        # 降低对比度，模拟拍摄不清的扫描件
        img = img.point(lambda v: 40 + int(v * 0.65))
    return img


def add_scan_page(c: canvas.Canvas, img: Image.Image, jpeg: bool):
    if jpeg:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=60)
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, W, H)
    else:
        c.drawImage(ImageReader(img), 0, 0, W, H)
    c.showPage()


SCAN_CLEAR_LINES = [
    ("国际销售合同", 56),
    ("合同编号：HT-2026-0301", 38),
    ("卖方：星河虚构进出口有限公司", 38),
    ("买方：Fictional Metals Trading FZE", 38),
    ("第1条 货物与价格", 38),
    ("货物为煅后石油焦，数量 320 吨，", 38),
    ("合同总价 USD 400,000.00。", 38),
    ("第2条 付款方式", 38),
    ("买方在合同签订后 5 个工作日内支付", 38),
    ("30% 预付款。", 38),
]
SCAN_BLURRY_LINES = [
    ("第3条 交货期", 38),
    ("卖方应在 2026年4月30日前完成装运。", 38),
    ("第4条 违约责任", 38),
    ("逾期交货每周按迟交部分货款的 0.5%", 38),
    ("计违约金。", 38),
    ("第5条 适用法律与争议解决", 38),
    ("本合同适用中华人民共和国法律。", 38),
    ("法定代表人（签字）：吴虚构", 38),
    ("收款账号：6222 0000 1111 2222 333", 38),
]
MIXED_SCAN_LINES = [
    ("第2条 价格与付款", 40),
    ("合同总价为 USD 610,000.00，", 40),
    ("买方应在合同签订后 5 个工作日内", 40),
    ("支付 30% 预付款。", 40),
    ("收款银行 SWIFT：FICTAEAD", 40),
]


# ---------------------------------------------------------------- 各样本


def make_text_contract() -> dict:
    c = canvas.Canvas(str(OUT_DIR / "text_contract.pdf"), pagesize=A4, invariant=1)
    p1 = TextPage(c, W, H, 1)
    p1.block(["国际销售合同"], size=18, gap=20)
    p1.block(["合同编号：HT-2026-0201"])
    p1.block(["签订日期：2026年3月15日"])
    p1.block(["卖方：星河虚构进出口有限公司（以下简称“星河公司”）"])
    p1.block(["买方：Fictional Metals Trading FZE（以下简称“FMT”）"])
    p1.block(["联系人：周示例，电话：13800138000，邮箱：zhou.example@example.com"])
    p1.block(["第1条 货物名称、规格和数量"], clause_no="第1条")
    p1.block(["货物明细见下表。"])
    rows = [
        ["品名", "规格", "数量(MT)", "单价(USD/MT)", "金额(USD)"],
        ["煅后石油焦", "低硫 0-3mm", "320", "USD 1,250.00", "USD 400,000.00"],
        ["针状焦", "超低硫 1-5mm", "100", "USD 2,100.00", "USD 210,000.00"],
        ["合计 Total", None, None, None, "USD 610,000.00"],
    ]
    tbl = table(p1, p1.y, [72, 172, 282, 342, 432, 523], rows, merged_total=True)
    p1.footer("第 1 页 共 2 页")
    c.showPage()

    p2 = TextPage(c, W, H, 2)
    p2.block(["第2条 价格与付款"], clause_no="第2条")
    p2.block([
        "合同总价为 USD 610,000.00，买方应在合同签订后 5 个工作日内支付 30% 预付款，",
        "余款在见提单副本后 5 个工作日内支付。",
    ])
    p2.block(["第3条 收款账户"], clause_no="第3条")
    p2.block(["收款银行 SWIFT：FICTAEAD，账号：6222 0000 1111 2222 333"])
    p2.block(["第4条 违约责任"], clause_no="第4条")
    p2.block(["逾期交货每周按迟交部分货款的 0.5% 计违约金，违约金总额最高不超过合同总价的 5%。"])
    p2.block(["第5条 适用法律与争议解决"], clause_no="第5条")
    p2.block(["本合同适用中华人民共和国法律。"])
    p2.block(["法定代表人（签字）：吴虚构"])
    p2.footer("第 2 页 共 2 页")
    c.showPage()
    c.save()

    blocks = p1.blocks + p2.blocks
    return {
        "file": "text_contract.pdf",
        "description": "文字版销售合同，2 页，全部为文字页。表格区域内的文字只应出现在表格单元格节点中（Q-16）；页码行是普通的文本块（不识别页眉页脚）。",
        "page_count": 2,
        "pages": [{"page": 1, "type": "text", "rotate": 0}, {"page": 2, "type": "text", "rotate": 0}],
        "ocr_pages": 0,
        "warnings": [],
        "bbox_tolerance": BBOX_TOLERANCE,
        "blocks": blocks,
        "tables": [tbl],
        "no_node_texts": [],
        "must_replace": [
            {"text": "星河虚构进出口有限公司", "category": "ORG"},
            {"text": "星河公司", "category": "ORG"},
            {"text": "Fictional Metals Trading FZE", "category": "ORG"},
            {"text": "FMT", "category": "ORG"},
            {"text": "HT-2026-0201", "category": "REFNO"},
            {"text": "周示例", "category": "PERSON"},
            {"text": "吴虚构", "category": "PERSON"},
            {"text": "13800138000", "category": "PHONE"},
            {"text": "zhou.example@example.com", "category": "EMAIL"},
            {"text": "6222 0000 1111 2222 333", "category": "ACCOUNT"},
            {"text": "FICTAEAD", "category": "SWIFT"},
            {"text": "USD 1,250.00", "category": "PRICE"},
            {"text": "USD 2,100.00", "category": "PRICE"},
            {"text": "USD 400,000.00", "category": "AMOUNT"},
            {"text": "USD 210,000.00", "category": "AMOUNT"},
            {"text": "USD 610,000.00", "category": "AMOUNT"},
        ],
        "must_keep": ["2026年3月15日", "煅后石油焦", "针状焦", "320", "100", "30%", "5 个工作日", "0.5%", "5%", "中华人民共和国法律"],
    }


def make_scanned_contract() -> dict:
    c = canvas.Canvas(str(OUT_DIR / "scanned_contract.pdf"), pagesize=A4, invariant=1)
    add_scan_page(c, draw_scan_page(SCAN_CLEAR_LINES), jpeg=False)
    add_scan_page(c, draw_scan_page(SCAN_BLURRY_LINES, blur=3.0, noise=80, seed=20260101), jpeg=True)
    c.save()
    return {
        "file": "scanned_contract.pdf",
        "description": "扫描版合同，2 页，页面里只有图片、没有文字对象。第 1 页清晰（OCR 置信度高）；第 2 页故意模糊、加噪点（应出现低置信警告）。",
        "page_count": 2,
        "pages": [
            {"page": 1, "type": "scanned", "rotate": 0, "ocr_conf": "high",
             "ocr_keywords": ["销售合同", "HT-2026-0301", "FictionalMetalsTradingFZE", "USD400,000.00", "预付款"]},
            {"page": 2, "type": "scanned", "rotate": 0, "ocr_conf": "low",
             "ocr_keywords": []},
        ],
        "ocr_pages": 2,
        "warnings": ["第 1、2 页为扫描页，已用 OCR 识别", "第 2 页 OCR 识别置信度低，请核对原件"],
        "must_replace": [
            {"text": "星河虚构进出口有限公司", "category": "ORG"},
            {"text": "Fictional Metals Trading FZE", "category": "ORG"},
            {"text": "HT-2026-0301", "category": "REFNO"},
            {"text": "USD 400,000.00", "category": "AMOUNT"},
        ],
        "must_keep": [],
    }


def make_mixed() -> dict:
    c = canvas.Canvas(str(OUT_DIR / "mixed.pdf"), pagesize=A4, invariant=1)
    p1 = TextPage(c, W, H, 1)
    p1.block(["销售合同补充说明"], size=18, gap=20)
    p1.block(["合同编号：HT-2026-0301"])
    p1.block(["卖方：星河虚构进出口有限公司"])
    p1.block(["买方：Fictional Metals Trading FZE"])
    p1.block(["第1条 货物"], clause_no="第1条")
    p1.block(["货物为煅后石油焦，数量 320 吨。"])
    c.showPage()
    add_scan_page(c, draw_scan_page(MIXED_SCAN_LINES), jpeg=False)
    p3 = TextPage(c, W, H, 3)
    p3.block(["第3条 确认"], clause_no="第3条")
    p3.block(["本补充说明经双方签字后生效。"])
    p3.block(["法定代表人（签字）：吴虚构"])
    c.showPage()
    c.save()
    return {
        "file": "mixed.pdf",
        "description": "3 页混合 PDF：第 1、3 页为文字页，第 2 页为清晰的扫描页。节点顺序按页码、再按页内位置。",
        "page_count": 3,
        "pages": [
            {"page": 1, "type": "text", "rotate": 0},
            {"page": 2, "type": "scanned", "rotate": 0, "ocr_conf": "high",
             "ocr_keywords": ["价格与付款", "USD610,000.00", "预付款", "FICTAEAD"]},
            {"page": 3, "type": "text", "rotate": 0},
        ],
        "ocr_pages": 1,
        "warnings": ["第 2 页为扫描页，已用 OCR 识别"],
        "bbox_tolerance": BBOX_TOLERANCE,
        "blocks": p1.blocks + p3.blocks,
        "tables": [],
        "no_node_texts": [],
        "must_replace": [
            {"text": "星河虚构进出口有限公司", "category": "ORG"},
            {"text": "Fictional Metals Trading FZE", "category": "ORG"},
            {"text": "HT-2026-0301", "category": "REFNO"},
            {"text": "吴虚构", "category": "PERSON"},
            {"text": "FICTAEAD", "category": "SWIFT"},
            {"text": "USD 610,000.00", "category": "AMOUNT"},
        ],
        "must_keep": [],
    }


def make_rotated() -> dict:
    c = canvas.Canvas(str(OUT_DIR / "rotated.pdf"), pagesize=A4, invariant=1)
    p1 = TextPage(c, W, H, 1)
    p1.block(["收货确认函"], size=18, gap=20)
    p1.block(["合同编号：HT-2026-0201"])
    p1.block(["兹确认已收到星河虚构进出口有限公司按合同交付的全部货物。"])
    c.showPage()

    # 第 2 页：页面本身是纵向的 MediaBox，设置 /Rotate 90 后显示为横向（宽 H、高 W）。
    # 先把坐标系转成“显示方向、左下角原点”，这样内容在显示时是正的。
    c.setPageRotation(90)
    c.translate(W, 0)
    c.rotate(90)
    p2 = TextPage(c, H, W, 2)  # 显示宽 841.89、显示高 595.27
    p2.block(["旋转页：货物验收记录"], size=18, gap=20)
    p2.block(["第6条 验收标准"], clause_no="第6条")
    p2.block(["买方应在货物到港后 7 日内完成验收，逾期视为验收合格。"])
    p2.block(["验收联系人：周示例，电话：13800138000"])
    c.showPage()
    c.save()
    return {
        "file": "rotated.pdf",
        "description": "2 页：第 1 页正常；第 2 页 MediaBox 为纵向、/Rotate 90，显示方向为横向且文字是正的。bbox 以显示方向（旋转之后）为准，第 2 页显示尺寸为 841.89 x 595.27（pdfplumber 的 page.width / height 不随 /Rotate 交换，但字符坐标已在显示方向上）。",
        "page_count": 2,
        "pages": [
            {"page": 1, "type": "text", "rotate": 0, "display_size": [round(W, 2), round(H, 2)]},
            {"page": 2, "type": "text", "rotate": 90, "display_size": [round(H, 2), round(W, 2)]},
        ],
        "ocr_pages": 0,
        "warnings": [],
        "bbox_tolerance": BBOX_TOLERANCE,
        "blocks": p1.blocks + p2.blocks,
        "tables": [],
        "no_node_texts": [],
        "must_replace": [
            {"text": "星河虚构进出口有限公司", "category": "ORG"},
            {"text": "HT-2026-0201", "category": "REFNO"},
            {"text": "周示例", "category": "PERSON"},
            {"text": "13800138000", "category": "PHONE"},
        ],
        "must_keep": ["7 日"],
    }


def make_encrypted() -> dict:
    enc = pdfencrypt.StandardEncryption(
        ENCRYPT_USER_PASSWORD, ownerPassword="fictional-owner-pass", canPrint=1, canModify=0, canCopy=0, canAnnotate=0
    )
    c = canvas.Canvas(str(OUT_DIR / "encrypted.pdf"), pagesize=A4, invariant=1, encrypt=enc)
    p = TextPage(c, W, H, 1)
    p.block(["加密文件（虚构样本）"], size=18, gap=20)
    p.block(["本文件设置了打开密码，解析时应失败。"])
    c.showPage()
    c.save()
    return {
        "file": "encrypted.pdf",
        "description": "设置了打开密码的 PDF。解析应抛出 ParseError（消息：PDF 文件无法读取，可能已损坏或已加密），消息不含文件内容。",
        "page_count": 1,
        "expect_parse_error": "PDF 文件无法读取，可能已损坏或已加密",
        "user_password": ENCRYPT_USER_PASSWORD,
    }


def main():
    samples = {
        "text_contract": make_text_contract(),
        "scanned_contract": make_scanned_contract(),
        "mixed": make_mixed(),
        "rotated": make_rotated(),
        "encrypted": make_encrypted(),
    }
    manifest = {
        "generator": "make_pdf_fixtures.py",
        "note": "全部内容虚构。文字页的块文字比较时忽略空白差异；bbox 为显示方向、左上角原点、PDF 点的近似值，按 bbox_tolerance 比较；OCR 页只断言 ocr_keywords、置信度高低和警告。",
        "samples": samples,
    }
    (OUT_DIR / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8"
    )
    print("已生成", ", ".join(s["file"] for s in samples.values()))


if __name__ == "__main__":
    main()
