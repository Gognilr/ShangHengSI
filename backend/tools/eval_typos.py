"""E-07 本地错字评测：原始诊断只存仓库外，数字报告不包含样本文字。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
from app.checks.model import CheckNode
from app.checks.typos import check_typos
from app.parsing.docx import parse_docx
from app.parsing.xlsx import parse_xlsx
from app.parsing.model import strip_invisible

ROOT = Path(__file__).resolve().parents[1]

def evaluate():
    nodes = []
    files = 0
    for path in sorted((ROOT / 'tests/fixtures/redaction_golden').glob('*.txt')):
        files += 1
        ann = yaml.safe_load(path.with_suffix('.yaml').read_text(encoding='utf-8'))
        for i, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            text = line.split(' ', 1)[1] if ann.get('loc_prefixed') else line
            if text.strip():
                nodes.append(CheckNode(f'F{files}-P{i}', strip_invisible(text), files, None))
    for path in sorted((ROOT / 'tests/fixtures/eval_fidelity').rglob('*')):
        if path.suffix not in ('.docx', '.xlsx'):
            continue
        files += 1
        parsed = (parse_docx if path.suffix == '.docx' else parse_xlsx)(path.read_bytes(), files)
        nodes += [CheckNode(n.loc, n.text, files, n.clause_no) for n in parsed.nodes if n.part == 'body']
    hits = check_typos(nodes)
    # 每节点合并展示，但评测按固定词对数计，避免多处错字合成一条后低估误报。
    false_positives = sum(h.detail.count('应为') for h in hits)
    chars = sum(len(n.text) for n in nodes)
    probe_path = ROOT / 'tests/fixtures/eval_fidelity/typo_probe.yaml'
    probe_bytes = probe_path.read_bytes()
    probes = yaml.safe_load(probe_bytes)
    assert len(probes) == 50
    recalled = 0
    missed = []
    for i, item in enumerate(probes, 1):
        clean = f"请核对本合同的{item['correct']}。"
        text = clean.replace(item['correct'], item['wrong'], 1)
        found = check_typos([CheckNode(f'F1-P{i}', text, 1, None)])
        expected = f"‘{item['wrong']}’应为‘{item['correct']}’"
        if any(expected in h.detail for h in found):
            recalled += 1
        else:
            missed.append(i)
    data = dict(files=files, nodes=len(nodes), chars=chars, false_positives=false_positives,
                false_positives_per_10000=false_positives * 10000 / chars if chars else None,
                planted=50, recalled=recalled, recall=recalled / 50, missed_indices=missed,
                probe_sha256=hashlib.sha256(probe_bytes).hexdigest())
    data['retain'] = data['false_positives_per_10000'] <= .5 and data['recall'] >= .7
    return data, [dict(locs=h.locs, detail=h.detail) for h in hits]

def main():
    parser = argparse.ArgumentParser(description='本地固定样本错字评测，不调用模型')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--diagnostics', type=Path, required=True, help='必须在仓库外')
    args = parser.parse_args()
    if args.diagnostics.resolve().is_relative_to(ROOT.parent.resolve()):
        parser.error('原始诊断必须保存在仓库外')
    data, hits = evaluate()
    with args.output.open('x', encoding='utf-8') as out:
        json.dump(data, out, ensure_ascii=False, indent=2)
    with args.diagnostics.open('x', encoding='utf-8') as out:
        json.dump(hits, out, ensure_ascii=False, indent=2)
    print(json.dumps(data, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
