"""生成 OCR 质量基线样本与真值 manifest.yaml（V34-D-01，D-02 沿用）。

用法（在测试镜像中，backend 目录挂载为 /srv/backend）：
    python tests/fixtures/ocr_eval/make_ocr_eval.py
依赖 Pillow、PyYAML 和中文字体 fonts-wqy-microhei（只在测试镜像中安装）。噪点、倾斜用固定种子，重复生成结果相同。
内容全部虚构。

每页按 200 DPI 的 A4（1654×2339 像素，与 PDF 扫描页的渲染一致）绘制，再模拟扫描：轻微倾斜、纸色、模糊、噪点、JPEG。
真值 lines 按“从上到下、同一行从左到右”的阅读顺序给出（表格一行的各单元格按列拼成一行，单元格之间不加分隔），
评测时两侧都做 NFKC 并去掉空白后比较。fields 为金额、账号、日期等须逐字正确的字段，评测只看它们是否原样出现。
"""
import random
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT_DIR = Path(__file__).resolve().parent
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
W, H = 1654, 2339
PT = 200 / 72  # 1 pt = 2.78 px（200 DPI）


def font(pt: float):
    return ImageFont.truetype(FONT_PATH, int(pt * PT), index=0)


class Page:
    def __init__(self):
        self.img = Image.new("L", (W, H), 255)
        self.d = ImageDraw.Draw(self.img)
        self.lines: list[str] = []
        self.y = 180

    def text(self, s: str, pt: float = 12, x: int = 170, gap: float = 1.75, center: bool = False):
        f = font(pt)
        if center:
            x = (W - int(self.d.textlength(s, font=f))) // 2
        self.d.text((x, self.y), s, fill=15, font=f)
        self.lines.append(s)
        self.y += int(pt * PT * gap)

    def row(self, cells: list[tuple[int, str]], pt: float = 12, gap: float = 1.75):
        f = font(pt)
        for x, s in cells:
            self.d.text((x, self.y), s, fill=15, font=f)
        self.lines.append("".join(s for _, s in cells))
        self.y += int(pt * PT * gap)

    def table(self, cols: list[int], rows: list[list[str]], pt: float = 10.5, row_h: int = 78,
              ruled: bool = True, merged_last: bool = False):
        """表格；cols 为各列左边界加最后的右边界。ruled=False 不画线（无线框表，D-02）；
        merged_last=True 时最后一行除末列外合并为一格（“合计”行，D-02）。"""
        f = font(pt)
        top = self.y
        for r, cells in enumerate(rows):
            y0 = top + r * row_h
            for c, s in enumerate(cells):
                tw = self.d.textlength(s, font=f)
                x = cols[c] + 14 if c < 2 else cols[c + 1] - 14 - tw  # 前两列左对齐，数字列右对齐
                self.d.text((x, y0 + (row_h - pt * PT) / 2 - 4), s, fill=15, font=f)
            self.lines.append("".join(cells))
        bottom = top + len(rows) * row_h
        if ruled:
            for r in range(len(rows) + 1):
                self.d.line([(cols[0], top + r * row_h), (cols[-1], top + r * row_h)], fill=30, width=3)
            for i, x in enumerate(cols):
                inner = 0 < i < len(cols) - 2
                self.d.line([(x, top), (x, bottom - (row_h if merged_last and inner else 0))], fill=30, width=3)
        self.y = bottom + 60


def stamp(img: Image.Image, cx: int, cy: int, name: str) -> Image.Image:
    """红色圆形印章（压在文字上），只画外圈、五角星与环形单位名称的简化版。"""
    rgb = img.convert("RGB")
    layer = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    red = (205, 35, 40, 170)
    r = 190
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=red, width=12)
    d.regular_polygon((cx, cy, 52), 5, rotation=180, fill=red)
    f = font(13)
    import math

    n = len(name)
    for i, ch in enumerate(name):
        a = math.radians(-150 + 300 * i / max(n - 1, 1)) - math.pi / 2
        tx, ty = cx + (r - 52) * math.cos(a), cy + (r - 52) * math.sin(a)
        ch_img = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        ImageDraw.Draw(ch_img).text((8, 4), ch, fill=red, font=f)
        ch_img = ch_img.rotate(-math.degrees(a) - 90, resample=Image.BICUBIC)
        layer.alpha_composite(ch_img, (int(tx - 30), int(ty - 30)))
    return Image.alpha_composite(rgb.convert("RGBA"), layer).convert("RGB")


def scan(img: Image.Image, seed: int, quality: str = "normal") -> Image.Image:
    rng = random.Random(seed)
    img = img.convert("RGB")
    angle = rng.uniform(-0.8, 0.8)
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(250, 249, 244))
    if quality == "poor":  # 传真 / 复印多次：先降到约 100 DPI 再放大，字迹变淡
        img = img.resize((W // 2, H // 2), Image.BILINEAR).resize((W, H), Image.BILINEAR)
        img = img.point(lambda v: 70 + int(v * 0.70))
    img = img.filter(ImageFilter.GaussianBlur(0.7 if quality == "normal" else 1.2))
    # 纸色与噪点：按块加噪（逐像素在纯 Python 中太慢）
    noise_amp = 18 if quality == "normal" else 50
    noise = Image.effect_noise((W // 2, H // 2), noise_amp).resize((W, H)).convert("RGB")
    img = Image.blend(img, noise, 0.10 if quality == "normal" else 0.25)
    img = img.point(lambda v: min(255, int(v * 0.92 + 16)))
    return img


def contract_body():
    p = Page()
    p.text("设备采购合同", 18, center=True, gap=2.4)
    p.text("合同编号：HT-2026-SH-0417", 12)
    p.text("甲方（买方）：青岚虚构科技股份有限公司", 12)
    p.text("乙方（卖方）：远帆示例机电设备有限公司", 12)
    p.text("签订日期：2026年3月18日    签订地点：上海市虚构区", 12, gap=2.2)
    body = [
        "第一条 标的物",
        "1.1 乙方向甲方提供数控加工中心3台，型号XK-7150B，技术参数详见附件一。",
        "1.2 设备须为全新未使用的合格产品，出厂日期不早于2026年1月1日。",
        "第二条 合同价款",
        "2.1 合同总价为人民币1,286,500.00元（大写：壹佰贰拾捌万陆仟伍佰元整），",
        "含13%增值税、运输费、安装调试费及培训费。",
        "2.2 除本合同另有约定外，合同价款在履行期间不作调整。",
        "第三条 付款方式",
        "3.1 合同生效后10个工作日内，甲方支付合同总价的30%，即385,950.00元；",
        "3.2 设备到货验收合格后，甲方支付合同总价的60%，即771,900.00元；",
        "3.3 剩余10%即128,650.00元作为质量保证金，质保期满后无息支付。",
        "第四条 交货与验收",
        "4.1 交货期限：合同生效后45日内，交货地点为甲方指定厂房。",
        "4.2 甲方应在设备到货后7日内组织验收，逾期未提出书面异议的视为验收合格。",
        "第五条 违约责任",
        "5.1 乙方逾期交货的，每逾期一日按合同总价的0.5‰支付违约金，",
        "累计不超过合同总价的10%。",
        "5.2 因乙方原因导致设备不能正常使用的，甲方有权解除合同。",
    ]
    for s in body:
        p.text(s, 12, x=170 if s.startswith("第") else 200)
    fields = [
        "HT-2026-SH-0417", "2026年3月18日", "XK-7150B", "2026年1月1日", "1,286,500.00",
        "壹佰贰拾捌万陆仟伍佰元整", "385,950.00", "771,900.00", "128,650.00", "0.5‰",
    ]
    return p, fields


def quotation_table():
    p = Page()
    p.text("报价一览表", 18, center=True, gap=2.4)
    p.text("项目名称：某虚构园区智能化改造采购项目    招标编号：ZB-2026-0389", 11)
    p.text("投标人：远帆示例机电设备有限公司            货币单位：人民币元", 11, gap=2.0)
    cols = [130, 230, 640, 870, 1050, 1300, 1530]
    rows = [
        ["序号", "货物名称", "规格型号", "数量", "单价", "合价"],
        ["1", "网络交换机", "SW-5548X", "12", "18,600.00", "223,200.00"],
        ["2", "视频监控摄像机", "IPC-T260", "86", "1,450.00", "124,700.00"],
        ["3", "门禁控制器", "AC-04N", "24", "3,280.00", "78,720.00"],
        ["4", "综合布线", "Cat6A", "1", "96,385.50", "96,385.50"],
        ["5", "安装调试费", "—", "1", "41,000.00", "41,000.00"],
        ["6", "备品备件", "SP-01", "10", "607.80", "6,078.00"],
    ]
    p.table(cols, rows)
    p.text("合计：564,083.50", 12)
    p.text("投标总价（大写）：伍拾陆万肆仟零捌拾叁元伍角", 12)
    p.text("交货期：合同签订后30日历天    质保期：36个月", 12)
    p.text("报价有效期：投标截止之日起90日历天", 12, gap=2.6)
    p.text("投标人（盖章）：远帆示例机电设备有限公司", 12)
    p.text("日期：2026年5月20日", 12)
    fields = [
        "ZB-2026-0389", "SW-5548X", "IPC-T260", "AC-04N", "18,600.00", "223,200.00", "1,450.00", "124,700.00",
        "3,280.00", "78,720.00", "96,385.50", "41,000.00", "607.80", "6,078.00", "564,083.50",
        "伍拾陆万肆仟零捌拾叁元伍角", "2026年5月20日",
    ]
    return p, fields


_QUOTE_ROWS = [
    ["序号", "货物名称", "规格型号", "数量", "单价", "合价"],
    ["1", "网络交换机", "SW-5548X", "12", "18,600.00", "223,200.00"],
    ["2", "视频监控摄像机", "IPC-T260", "86", "1,450.00", "124,700.00"],
    ["3", "门禁控制器", "AC-04N", "24", "3,280.00", "78,720.00"],
    ["4", "综合布线", "Cat6A", "1", "96,385.50", "96,385.50"],
    ["5", "安装调试费", "—", "1", "41,000.00", "41,000.00"],
    ["6", "备品备件", "SP-01", "10", "607.80", "6,078.00"],
]


def quotation_stamp():
    """D-02：合计行在表内（前五列合并），落款印章压在表格右下的金额列上。"""
    p = Page()
    p.text("开标一览表", 18, center=True, gap=2.4)
    p.text("项目名称：某虚构园区智能化改造采购项目    招标编号：ZB-2026-0389", 11)
    p.text("投标人：远帆示例机电设备有限公司            货币单位：人民币元", 11, gap=2.0)
    cols = [130, 230, 640, 870, 1050, 1300, 1530]
    table_top = p.y
    p.table(cols, [*_QUOTE_ROWS, ["合计", "", "", "", "", "564,083.50"]], merged_last=True)
    p.lines[-1] = "合计564,083.50"
    p.text("投标总价（大写）：伍拾陆万肆仟零捌拾叁元伍角", 12)
    p.text("投标人（盖章）：远帆示例机电设备有限公司", 12)
    p.text("日期：2026年5月20日", 12)
    fields = ["18,600.00", "223,200.00", "1,450.00", "124,700.00", "3,280.00", "78,720.00", "96,385.50",
              "41,000.00", "607.80", "6,078.00", "564,083.50", "伍拾陆万肆仟零捌拾叁元伍角"]
    return p, fields, (1330, table_top + 78 * 6)


def quotation_noline():
    """D-02：无线框的报价表（只有对齐的列），应仍走 ocr_block。"""
    p = Page()
    p.text("分项报价表", 18, center=True, gap=2.4)
    p.text("投标人：远帆示例机电设备有限公司            货币单位：人民币元", 11, gap=2.0)
    p.table([130, 230, 640, 870, 1050, 1300, 1530], _QUOTE_ROWS, ruled=False)
    p.text("合计：564,083.50", 12)
    fields = ["18,600.00", "223,200.00", "1,450.00", "124,700.00", "3,280.00", "78,720.00", "96,385.50",
              "41,000.00", "607.80", "6,078.00", "564,083.50"]
    return p, fields


def account_info():
    p = Page()
    p.text("投标人基本情况表", 18, center=True, gap=2.4)
    items = [
        ("投标人名称", "远帆示例机电设备有限公司"),
        ("统一社会信用代码", "91310115MA1H7Q2K0B"),
        ("注册地址", "上海市虚构区示范路1088号3幢501室"),
        ("成立日期", "2011年10月08日"),
        ("注册资本", "人民币5,000万元"),
        ("法定代表人", "陈示例"),
        ("联系电话", "021-58801234"),
        ("移动电话", "13916025871"),
        ("电子邮箱", "bid@yuanfan-example.cn"),
        ("开户银行", "虚构银行上海浦东支行"),
        ("银行账号", "6217 0012 3450 0789 016"),
        ("纳税人识别号", "91310115MA1H7Q2K0B"),
        ("投标保证金", "人民币20,000.00元"),
        ("保证金递交日期", "2026-05-18"),
        ("备注", "账号尾号016，户名同投标人名称"),
    ]
    for k, v in items:
        p.row([(170, k + "："), (640, v)], 12, gap=2.0)
    fields = [
        "91310115MA1H7Q2K0B", "2011年10月08日", "5,000万元", "021-58801234", "13916025871",
        "bid@yuanfan-example.cn", "6217001234500789016", "20,000.00", "2026-05-18", "1088号3幢501室",
    ]
    return p, fields


def stamp_page():
    p = Page()
    p.text("投标函", 18, center=True, gap=2.4)
    p.text("致：某虚构园区管理委员会", 12, gap=2.0)
    body = [
        "根据贵方招标编号为ZB-2026-0389的招标文件，签字代表陈示例经正式授权并代表",
        "投标人远帆示例机电设备有限公司提交下述文件正本一份、副本四份。",
        "1. 我方投标总价为人民币伍拾陆万肆仟零捌拾叁元伍角（¥564,083.50）。",
        "2. 我方承诺在合同签订后30日历天内完成交货，质保期36个月。",
        "3. 本投标自投标截止之日起90日历天内有效。",
        "4. 我方已详细审核全部招标文件，同意放弃对这方面有不明及误解的权利。",
        "5. 我方同意提供贵方可能要求的与本投标有关的一切数据或资料。",
        "6. 与本投标有关的一切正式往来通讯请寄：上海市虚构区示范路1088号，",
        "邮政编码：201206，电话：021-58801234，传真：021-58801235。",
    ]
    for s in body:
        p.text(s, 12)
    p.y += 160
    p.row([(900, "投标人：远帆示例机电设备有限公司")], 12, gap=2.2)
    p.row([(900, "法定代表人或授权代表：陈示例")], 12, gap=2.2)
    p.row([(900, "日期：2026年5月20日")], 12)
    fields = ["ZB-2026-0389", "¥564,083.50", "201206", "021-58801234", "021-58801235", "2026年5月20日"]
    return p, fields, (1180, p.y - 190)


def small_font():
    p = Page()
    p.text("供货及售后服务承诺", 15, center=True, gap=2.4)
    body = [
        "一、我公司承诺所投设备均为原厂全新产品，提供生产厂家出具的授权书及质保承诺函，",
        "授权书编号SQ-20260512-07，有效期至2027年5月11日。",
        "二、设备到货后由我公司工程师现场安装调试，安装周期不超过15个工作日；",
        "调试合格后提交测试记录一式三份，经采购人签字确认后进入试运行，试运行期为30天。",
        "三、质保期内设备出现故障，我公司在接到通知后2小时内响应，24小时内到达现场，",
        "48小时内修复；不能修复的，72小时内提供同型号备用设备。",
        "四、质保期满后继续提供有偿维保服务，年度维保费用不高于合同总价的3.5%，",
        "备品备件按投标报价的85折供应，价格锁定期为5年。",
        "五、免费培训：为采购人培训操作人员不少于8人次、维护人员不少于4人次，",
        "培训时长累计不少于16学时，并提供中文操作手册和维护手册各2套。",
        "六、服务热线：400-820-6688（7×24小时），服务邮箱：service@yuanfan-example.cn。",
        "七、本承诺作为投标文件的组成部分，与合同具有同等法律效力。",
    ]
    for s in body:
        p.text(s, 9, gap=1.9)
    fields = [
        "SQ-20260512-07", "2027年5月11日", "3.5%", "85折", "400-820-6688", "7×24", "service@yuanfan-example.cn",
    ]
    return p, fields


SAMPLES = [
    ("contract_body", "合同正文：12 号字，含编号、日期、金额与大写金额", "normal"),
    ("quotation_table", "有线框的报价一览表：10.5 号字，数字列右对齐，含合计与大写", "normal"),
    ("account_info", "基本情况表：信用代码、银行账号（分组空格）、电话、邮箱", "normal"),
    ("stamp_page", "投标函：落款处红色圆形印章压在单位名称与日期上", "normal"),
    ("small_font", "承诺书：9 号小字，密排", "normal"),
    ("quotation_poor", "报价一览表的低质量扫描：约 100 DPI 放大、字迹发淡、较重噪点", "poor"),
    ("account_poor", "基本情况表的低质量扫描（同上）", "poor"),
    ("quotation_stamp", "开标一览表：有线框，合计行在表内（前五列合并），印章压在金额列上（D-02）", "normal"),
    ("quotation_noline", "无线框的分项报价表：只有对齐的列（D-02）", "normal"),
]


def main():
    out = {}
    for i, (name, desc, quality) in enumerate(SAMPLES):
        stamp_at = None
        if name in ("stamp_page", "quotation_stamp"):
            page, fields, stamp_at = globals()[name]()
        elif name.endswith("_poor"):
            page, fields = globals()[name.replace("_poor", "_table" if name.startswith("quotation") else "_info")]()
        else:
            page, fields = globals()[name]()
        img = page.img
        if stamp_at:
            img = stamp(img, *stamp_at, "远帆示例机电设备有限公司")
        img = scan(img, 20260519 + i, quality)
        fname = f"{name}.jpg"
        img.save(OUT_DIR / fname, "JPEG", quality=70 if quality == "normal" else 40)
        out[name] = {"file": fname, "description": desc, "quality": quality, "lines": page.lines, "fields": fields}
    manifest = {
        "generator": "make_ocr_eval.py",
        "note": "全部内容虚构。200 DPI A4 模拟扫描页。lines 为阅读顺序真值；fields 须逐字识别正确（比较前去空白、NFKC）。",
        "samples": out,
    }
    (OUT_DIR / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8"
    )
    print("已生成", len(out), "页")


if __name__ == "__main__":
    main()
