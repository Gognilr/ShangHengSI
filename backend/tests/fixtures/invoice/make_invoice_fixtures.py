"""生成发票管家的虚构样本（V36-I-01，设计第八节）。

用法（在测试镜像中，backend 目录挂载为 /srv/backend）：
    python tests/fixtures/invoice/make_invoice_fixtures.py
依赖 reportlab（含二维码）、Pillow、pypdfium2、opencv 与中文字体 fonts-wqy-microhei（只在测试镜像中）。

内容全部虚构：单位名称、税号、号码、金额均为编造，税号的地区码 999999 不存在。每张票面带浅红色
“虚构样本”水印（画成图片，不进文字层；OCR 前随红章一起去掉）。XML 与 OFD 带“虚构样本”注释或文字对象。

生成的样本：
  vat_special.pdf   数电票（增值税专用发票）文字层，二维码金额等于金额（不含税），号码值先于标签写入内容流
  vat_special.xml   同一张票的 XML（合并同票）
  vat_ordinary.ofd  传统增值税电子普通发票的 OFD：CustomTag 指向文字对象，代码与号码带前导零
  scan.png          数电票（普通发票）的 200 DPI 扫描图
  photo.jpg         同版式另一张票的手机照片：透视变形、灰底、轻微模糊
"""
import io
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
FONT = "WQY"
W, H = 683.0, 397.0  # 数电票常见幅面 241 × 140 毫米
pdfmetrics.registerFont(TTFont(FONT, FONT_PATH, subfontIndex=0))

BUYER = ("虚构甲方贸易有限公司", "91999999MA00000002")
SELLER = ("虚构示例科技有限公司", "91999999MA00000001")

SPECIAL = {
    "title": "电子发票（增值税专用发票）", "number": "26999000000000000001", "date": ("2026", "11", "03"),
    "buyer": BUYER, "seller": SELLER,
    "items": [("*信息技术服务*软件维护费", "V2", "项", "1", "600.00", "600.00", "6%", "36.00"),
              ("*现代服务*技术咨询费", "", "次", "1", "400.00", "400.00", "6%", "24.00")],
    "amount": "1000.00", "tax": "60.00", "total": "1060.00", "total_cn": "壹仟零陆拾圆整",
    "remark": "虚构项目编号 XG-001", "qr": "01,31,,26999000000000000001,1000.00,20261103,,A1B2",
}
ORDINARY_SCAN = {
    "title": "电子发票（普通发票）", "number": "26999000000000000002", "date": ("2026", "11", "05"),
    "buyer": BUYER, "seller": ("虚构乙方餐饮管理有限公司", "91999999MA00000003"),
    "items": [("*餐饮服务*餐饮费", "", "次", "1", "283.02", "283.02", "6%", "16.98")],
    "amount": "283.02", "tax": "16.98", "total": "300.00", "total_cn": "叁佰圆整",
    "remark": "", "qr": "01,32,,26999000000000000002,300.00,20261105,,C3D4",
}
ORDINARY_PHOTO = {
    "title": "电子发票（普通发票）", "number": "26999000000000000003", "date": ("2026", "11", "08"),
    "buyer": BUYER, "seller": ("虚构丙方文具有限公司", "91999999MA00000004"),
    "items": [("*纸制品*打印纸", "A4", "箱", "2", "88.50", "177.00", "13%", "23.01")],
    "amount": "177.00", "tax": "23.01", "total": "200.01", "total_cn": "贰佰圆零壹分",
    "remark": "", "qr": "01,32,,26999000000000000003,200.01,20261108,,E5F6",
}


def _watermark_png(width: int, height: int) -> Image.Image:
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    font = ImageFont.truetype(FONT_PATH, int(height / 5), index=0)
    draw = ImageDraw.Draw(layer)
    draw.text((width * 0.18, height * 0.38), "虚构样本", font=font, fill=(235, 120, 120, 110))
    return Image.alpha_composite(img, layer.rotate(18, resample=Image.BICUBIC))


def _text(c, x, top, s, size=9):
    c.setFont(FONT, size)
    c.drawString(x, H - top - size, s)


def draw_invoice(c: canvas.Canvas, d: dict) -> None:
    c.setStrokeColorRGB(0.55, 0.35, 0.2)
    c.setLineWidth(0.8)
    # 号码的值先于标签写入内容流：取值只能看坐标（设计第三节）
    _text(c, 520, 25, d["number"])
    _text(c, 470, 25, "发票号码：")
    _text(c, 470, 42, f"开票日期：{d['date'][0]}年{d['date'][1]}月{d['date'][2]}日")
    _text(c, 230, 30, d["title"], 16)
    qr = QrCodeWidget(d["qr"], barLevel="M")
    x0, y0, x1, y1 = qr.getBounds()
    size = 62
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(qr)
    renderPDF.draw(drawing, c, 24, H - 14 - size)
    # 购买方、销售方：左右两栏，竖排“购买方信息”“销售方信息”
    c.rect(20, H - 160, 643, 76)
    c.line(340, H - 160, 340, H - 84)
    c.line(40, H - 160, 40, H - 84)
    c.line(360, H - 160, 360, H - 84)
    for i, ch in enumerate("购买方信息"):
        _text(c, 26, 90 + i * 13, ch, 9)
    for i, ch in enumerate("销售方信息"):
        _text(c, 346, 90 + i * 13, ch, 9)
    _text(c, 48, 102, f"名称：{d['buyer'][0]}")
    _text(c, 48, 130, f"统一社会信用代码/纳税人识别号：{d['buyer'][1]}")
    _text(c, 368, 102, f"名称：{d['seller'][0]}")
    _text(c, 368, 130, f"统一社会信用代码/纳税人识别号：{d['seller'][1]}")
    # 明细表
    c.rect(20, H - 300, 643, 140)
    cols = (60, 200, 262, 300, 350, 430, 505, 590)
    for x, head in zip(cols, ("项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额")):
        _text(c, x, 168, head)
    for r, item in enumerate(d["items"]):
        for x, v in zip((26, *cols[1:]), item):
            if v:
                _text(c, x, 186 + r * 15, v)
    _text(c, 60, 270, "合")
    _text(c, 90, 270, "计")
    _text(c, 430, 270, f"¥{d['amount']}")
    _text(c, 590, 270, f"¥{d['tax']}")
    c.line(20, H - 284, 663, H - 284)
    _text(c, 30, 290, "价税合计（大写）")
    _text(c, 160, 290, d["total_cn"])
    _text(c, 470, 290, f"（小写）¥{d['total']}")
    c.rect(20, H - 355, 643, 55)
    c.line(40, H - 355, 40, H - 300)
    _text(c, 26, 312, "备")
    _text(c, 26, 330, "注")
    if d["remark"]:
        _text(c, 50, 314, d["remark"])
    _text(c, 30, 365, "开票人：虚构")
    wm = _watermark_png(1366, 794)
    c.drawImage(ImageReader(wm), 0, 0, W, H, mask="auto")


def invoice_pdf(d: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H), invariant=1)
    c.setTitle("虚构样本")
    draw_invoice(c, d)
    c.showPage()
    c.save()
    return buf.getvalue()


def render(pdf: bytes, dpi: int = 200) -> Image.Image:
    import pypdfium2

    doc = pypdfium2.PdfDocument(pdf)
    try:
        return doc[0].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        doc.close()


def photo(img: Image.Image) -> Image.Image:
    """贴到灰底上做透视变形，轻微模糊，模拟手机拍照。"""
    import cv2
    import numpy as np
    from PIL import ImageFilter

    src = np.asarray(img)
    h, w = src.shape[:2]
    bw, bh = int(w * 1.25), int(h * 1.35)
    dst = np.float32([[bw * 0.10, bh * 0.14], [bw * 0.90, bh * 0.10], [bw * 0.93, bh * 0.86], [bw * 0.08, bh * 0.90]])
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), dst)
    out = cv2.warpPerspective(src, m, (bw, bh), borderMode=cv2.BORDER_CONSTANT, borderValue=(96, 98, 104))
    return Image.fromarray(out).filter(ImageFilter.GaussianBlur(0.8))


XML = """<?xml version="1.0" encoding="UTF-8"?>
<!-- 虚构样本：内容全部编造 -->
<EInvoice>
  <Header>
    <EIid>{number}</EIid>
    <InherentLabel><EInvoiceType><LabelCode>01</LabelCode><LabelName>增值税专用发票</LabelName></EInvoiceType></InherentLabel>
  </Header>
  <EInvoiceData>
    <SellerInformation><SellerIdNum>{seller_tax}</SellerIdNum><SellerName>{seller}</SellerName></SellerInformation>
    <BuyerInformation><BuyerIdNum>{buyer_tax}</BuyerIdNum><BuyerName>{buyer}</BuyerName></BuyerInformation>
    <BasicInformation>
      <TotalAmWithoutTax>1000.00</TotalAmWithoutTax><TotalTaxAm>60.00</TotalTaxAm>
      <TotalTax-includedAmount>1060.00</TotalTax-includedAmount>
      <TotalTax-includedAmountInChinese>壹仟零陆拾圆整</TotalTax-includedAmountInChinese>
    </BasicInformation>
    <IssuItemInformation><ItemName>*信息技术服务*软件维护费</ItemName><Amount>600.00</Amount><TaxRate>0.06</TaxRate></IssuItemInformation>
    <IssuItemInformation><ItemName>*现代服务*技术咨询费</ItemName><Amount>400.00</Amount><TaxRate>0.06</TaxRate></IssuItemInformation>
    <AdditionalInformation><Remark>虚构项目编号 XG-001</Remark></AdditionalInformation>
  </EInvoiceData>
  <TaxSupervisionInfo><InvoiceNumber>{number}</InvoiceNumber><IssueTime>2026-11-03 10:20:30</IssueTime></TaxSupervisionInfo>
</EInvoice>
"""

_OFD_NS = 'xmlns:ofd="http://www.ofdspec.org/2016"'


def ofd() -> bytes:
    texts = {
        "1": "增值税电子普通发票", "2": "011999000111", "3": "00012345", "4": "2026年10月15日",
        "5": "虚构丁方咨询有限公司", "6": "91999999MA00000005", "7": "虚构戊方印务有限公司", "8": "91999999MA00000006",
        "9": "200.00", "10": "26.00", "11": "226.00", "12": "虚构样本",
    }
    objs = "".join(f'<ofd:TextObject ID="{k}" Boundary="10 {int(k) * 6} 60 5" Font="1" Size="3.5">'
                   f'<ofd:TextCode X="0" Y="4">{v}</ofd:TextCode></ofd:TextObject>' for k, v in texts.items())
    tags = {"InvoiceCode": "2", "InvoiceNo": "3", "IssueDate": "4", "BuyerName": "5", "BuyerTaxID": "6",
            "SellerName": "7", "SellerTaxID": "8", "TaxExclusiveTotalAmount": "9", "TaxTotalAmount": "10",
            "TaxInclusiveTotalAmount": "11"}
    tag_xml = "".join(f'<fp:{k}><fp:ObjectRef PageRef="1">{v}</fp:ObjectRef></fp:{k}>' for k, v in tags.items())
    files = {
        "OFD.xml": f'<?xml version="1.0" encoding="UTF-8"?><ofd:OFD {_OFD_NS} Version="1.1" DocType="OFD">'
                   '<ofd:DocBody><ofd:DocRoot>Doc_0/Document.xml</ofd:DocRoot></ofd:DocBody></ofd:OFD>',
        "Doc_0/Document.xml": f'<?xml version="1.0" encoding="UTF-8"?><ofd:Document {_OFD_NS}><ofd:Pages>'
                              '<ofd:Page ID="1" BaseLoc="Pages/Page_0/Content.xml"/></ofd:Pages>'
                              '<ofd:CustomTags>Tags/CustomTags.xml</ofd:CustomTags></ofd:Document>',
        "Doc_0/Pages/Page_0/Content.xml": f'<?xml version="1.0" encoding="UTF-8"?><ofd:Page {_OFD_NS}><ofd:Content>'
                                          f'<ofd:Layer ID="20">{objs}</ofd:Layer></ofd:Content></ofd:Page>',
        "Doc_0/Tags/CustomTags.xml": f'<?xml version="1.0" encoding="UTF-8"?><ofd:CustomTags {_OFD_NS}>'
                                     '<ofd:CustomTag TypeID="invoice"><ofd:FileLoc>CustomTag.xml</ofd:FileLoc>'
                                     '</ofd:CustomTag></ofd:CustomTags>',
        "Doc_0/Tags/CustomTag.xml": '<?xml version="1.0" encoding="UTF-8"?>'
                                    f'<fp:eInvoice xmlns:fp="http://www.edrm.org.cn/schema/e-invoice/2019">{tag_xml}</fp:eInvoice>',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body.encode())
    return buf.getvalue()


def main() -> None:
    (OUT / "vat_special.pdf").write_bytes(invoice_pdf(SPECIAL))
    (OUT / "vat_special.xml").write_text(XML.format(number=SPECIAL["number"], seller=SELLER[0], seller_tax=SELLER[1],
                                                    buyer=BUYER[0], buyer_tax=BUYER[1]), encoding="utf-8")
    (OUT / "vat_ordinary.ofd").write_bytes(ofd())
    render(invoice_pdf(ORDINARY_SCAN)).save(OUT / "scan.png", optimize=True)
    photo(render(invoice_pdf(ORDINARY_PHOTO))).save(OUT / "photo.jpg", quality=85)


if __name__ == "__main__":
    main()
