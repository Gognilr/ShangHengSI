"""R-02：生成六份完全虚构的回程体检样本；在 Docker 内执行。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.fixtures.docx.make_docx_fixtures import Doc


def main():
    root = Path(__file__).resolve().parent
    for language in ("cn", "en"):
        for n in range(1, 4):
            d = Doc(f"{language}_{n:02d}", "全部公司、人名、项目、编号及联系方式均为虚构。")
            if language == "cn":
                d.para("设备销售合同（虚构测试）", style="Heading1")
                d.para("卖方：星河虚构进出口有限公司（我方）")
                d.para(f"买方：青岚虚构设备{n}有限公司")
                d.para(f"合同编号：HT-2026-110{n}；项目名称：星海虚构{n}号设备项目。")
                d.para("卖方联系人：周示例；电话：021-12345678；办公地点：中国上海市虚构路100号。")
                d.para("买方联系人：吴虚构；电话：010-23456789；办公地点：中国北京市示例路200号。")
                d.para("第一条 合同总价为人民币100,000.00元，其中设备价款人民币60,000.00元，安装费人民币40,000.00元。")
                d.para("第二条 买方应于签约后支付人民币30,000.00元，余款在买方认可设备后支付；认可期限未约定。")
                d.para("第三条 卖方负责将设备送至买方地址，运输风险和验收标准由双方另行商定。")
                d.para("第四条 如卖方迟延交付，应赔偿买方全部直接及间接损失，赔偿责任不设上限。")
                d.para("第五条 买方可随时取消订单，卖方应退还全部款项；本合同未约定争议解决方式。")
                if n == 2:
                    d.para("第六条 卖方提供终身免费保修，包括因买方操作不当造成的损坏；买方可从任何应付款中直接抵扣费用。")
                if n == 3:
                    d.table([["品名", "数量", "单价", "金额"],
                             ["示例设备", "2", "人民币30,000.00元", "人民币60,000.00元"],
                             ["示例安装服务", "1", "人民币30,000.00元", "人民币30,000.00元"],
                             ["示例调试服务", "1", "人民币10,000.00元", "人民币10,000.00元"],
                             ["合计", "", "", "人民币100,000.00元"]])
            else:
                d.para("Equipment Sales Contract (Fictitious Test)", style="Heading1")
                d.para("Seller: Stellar Fictive Export Co., Ltd. (our company)")
                d.para(f"Buyer: Orion Fictive Equipment {n} Ltd.")
                d.para(f"Contract No.: HT-2026-220{n}; Project Name: Orion Fictive {n} Equipment Project.")
                d.para("Seller Contact: John Example; Tel: +44 20 7946 0123; Address: 100 Fictive Road, London, United Kingdom.")
                d.para("Buyer Contact: Alice Sample; Tel: +971 4 123 4567; Address: Plot 200, Fictive Park, Dubai, United Arab Emirates.")
                d.para("1. The total contract price is USD 100,000.00, including equipment at USD 60,000.00 and installation at USD 40,000.00.")
                d.para("2. Buyer shall pay USD 30,000.00 after signing and the balance after Buyer approves the equipment. No approval deadline is specified.")
                d.para("3. Seller shall deliver to Buyer's address. Transfer of risk and acceptance criteria shall be agreed later.")
                d.para("4. Seller shall compensate Buyer for all direct and indirect losses caused by delay, without any liability cap.")
                d.para("5. Buyer may cancel at any time and Seller shall refund all payments. No dispute resolution procedure is specified.")
                if n == 2:
                    d.para("6. Seller provides a free lifetime warranty including damage caused by Buyer's misuse. Buyer may deduct any costs from any payment due.")
                if n == 3:
                    d.table([["Product", "Quantity", "Unit Price", "Amount"],
                             ["Example equipment", "2", "USD 30,000.00", "USD 60,000.00"],
                             ["Example installation", "1", "USD 30,000.00", "USD 30,000.00"],
                             ["Example commissioning", "1", "USD 10,000.00", "USD 10,000.00"],
                             ["Total", "", "", "USD 100,000.00"]])
            target = root / f"{d.name}.docx"
            target.write_bytes(d.build())
    print("生成 6 份虚构 DOCX")


if __name__ == "__main__":
    main()
