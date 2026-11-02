"""规范事实表格式；可用仅作对照的临时 iban.dat 检查全部长度。"""
import argparse
import hashlib
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--compare', type=Path)
args = parser.parse_args()
path = Path(__file__).resolve().parents[1] / 'resources/iban_lengths.json'
rows = json.loads(path.read_text('utf-8'))
if args.compare:
    data = args.compare.read_text('utf-8')
    other = {m[1]: 4 + sum(map(int, re.findall(r'(\d+)!', m[2])))
             for m in re.finditer(r'^([A-Z]{2}) .*bban="([^"]+)"', data, re.M)}
    differences = [(k, rows.get(k), other.get(k)) for k in sorted(rows.keys() | other.keys())
                   if rows.get(k) != other.get(k)]
    print('对照差异：', differences)
    assert not differences
path.write_text(json.dumps(rows, sort_keys=True, indent=2) + '\n', encoding='utf-8', newline='\n')
print(len(rows), hashlib.sha256(path.read_bytes()).hexdigest())
