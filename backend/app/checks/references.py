"""L-10 / L-11：版本化清单匹配，不推断其他法律或税率是否有效。"""
import re
from functools import lru_cache
from pathlib import Path
from decimal import Decimal

import yaml

from app.checks.amounts import _scan_tables
from app.checks.model import CheckNode, CheckResult

_RESOURCES = Path(__file__).resolve().parents[2] / 'resources'


@lru_cache
def _resource(name):
    return yaml.safe_load((_RESOURCES / name).read_text(encoding='utf-8'))


def check_references(nodes: list[CheckNode]) -> list[CheckResult]:
    results = []
    laws = _resource('abolished_laws.yaml')['laws']
    matches = {}
    for node in nodes:
        for title in re.findall(r'《([^《》]+)》', node.text):
            law = next((law for law in laws if title in [law['name'], *law['aliases']]), None)
            if law:
                key = (node.file_seq, law['name'])
                if key not in matches:
                    text = f"引用《{title}》，该法已于 {law['abolished']} 废止，现行规定见《{law['replaced_by']}》"
                    matches[key] = CheckResult('L-10', 'medium', [], node.file_seq, text, text)
                if node.loc not in matches[key].locs:
                    matches[key].locs.append(node.loc)
    results += list(matches.values())
    outdated = {Decimal(rate) for rate in _resource('vat_rates.yaml')['outdated']}
    found = set()

    def add(node, rate):
        key = (node.loc, rate)
        if rate not in outdated or key in found:
            return
        found.add(key)
        text = f'写明的增值税税率 {rate:f}% 为已调整的旧税率（现行一般为 13%、9%、6%），请人工确认适用税率'
        results.append(CheckResult('L-11', 'medium', [node.loc], node.file_seq, text, text))

    for node in nodes:
        for sentence in re.split(r'[。；;！？\n]', node.text):
            if re.search('增值税|税率|税点|专用发票|普通发票|含税|开票', sentence) and not re.search('关税|消费税|所得税|预提', sentence):
                for m in re.finditer(r'(\d+(?:\.\d+)?)\s*[%％]', sentence):
                    # “合同总价的 10%”是付款或违约金比例，不是税率（V2Q-30）
                    if not sentence[:m.start()].rstrip().endswith('的'):
                        add(node, Decimal(m[1]))
    for table in _scan_tables(nodes):
        for row in table.rows:
            if row > table.header and (node := table.node(row, 'rate')):
                for rate in re.findall(r'(\d+(?:\.\d+)?)\s*[%％]', node.text):
                    add(node, Decimal(rate))
    return results
