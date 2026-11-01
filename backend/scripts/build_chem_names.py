"""从官方目录转换后的 DOCX 中仅提取品名、别名列（离线生成，不在产品运行）。"""
import argparse
import hashlib
import re
from pathlib import Path

from docx import Document


def build(source: Path, output: Path):
    table = Document(source / 'catalog.docx').tables[0]
    assert [c.text for c in table.rows[0].cells][:3] == ['序号', '品名', '别名']
    terms = set()
    rows = 0
    for row in table.rows[1:]:
        cells = [c.text for c in row.cells]
        if not cells[0].strip().isdigit():
            continue
        rows += 1
        for cell in cells[1:3]:
            for term in re.split('[；;\n]', cell):
                term = re.sub(r'\s+', '', term)
                if cells[0].strip() == '1674' and term.startswith('柴油'):
                    term = '柴油'  # 2022 年第 8 号公告唯一调整。
                if 2 <= len(term) <= 30:
                    terms.add(term)
    assert rows >= 2828
    data = ''.join(t + '\n' for t in sorted(terms)).encode()
    (output / 'chem_names.txt').write_bytes(data)
    originals = '\n'.join(f'- `{name}` SHA-256 `{hashlib.sha256((source / name).read_bytes()).hexdigest()}`'
                          for name in ('catalog.doc', 'adjustment.pdf'))
    (output / 'chem_names.README.md').write_text(f'''# 化工品名（V31-S-01a）

来源：[2015 年第 5 号公告（2015-02-27）](https://www.mem.gov.cn/gk/gwgg/xgxywj/wxhxp_228/201503/t20150309_232632.shtml)，
[官方 DOC 原件](https://www.mem.gov.cn/gk/gwgg/xgxywj/wxhxp_228/201503/W020240524642146440778.doc)。
调整：[2022 年第 8 号公告（2022-10-13）](https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202211/t20221107_426077.shtml)，
[官方 PDF 原件](https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202211/P020221107750004104671.pdf)。
下载由用户 2026-11-02 确认（设计 12.1）；政府规范性文件按著作权法第五条不受保护。

{originals}

用仓库现有 aireview-converter:dev 的 LibreOffice 将 catalog.doc 转为 catalog.docx，
然后在后端测试容器运行 `python scripts/build_chem_names.py --source <原件目录> --out resources`。
只读表格第 2、3 列，以分号或换行拆分、去空白，保留 2～30 字；不取 CAS、备注、序号。
按调整公告把 1674 项品名改为“柴油”，不移植闭杯闪点条件。
去重、Unicode 排序、UTF-8/LF，共 {len(terms)} 行；SHA-256 `{hashlib.sha256(data).hexdigest()}`。
只用于实体识别整段相等与不确定项最长匹配，不改变规则、企业后缀、签约方与已有替换。
''', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.out)
