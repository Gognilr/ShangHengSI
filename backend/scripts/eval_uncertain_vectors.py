"""V31-S-06：固定 k=5、按样本留一交叉验证，只使用离线 bge-m3。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.embedding import engine as embedding
from app.redaction import engine, keep_terms, parties, suspects

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / 'tests/fixtures/redaction_golden'


def dataset():
    rows, digest = [], hashlib.sha256()
    for sample, path in enumerate(sorted(GOLDEN.glob('*.yaml'))):
        txt = path.with_suffix('.txt')
        digest.update(path.name.encode() + path.read_bytes() + txt.read_bytes())
        ann = yaml.safe_load(path.read_text(encoding='utf-8'))
        lines = txt.read_text(encoding='utf-8').splitlines()
        nodes = [engine.NodeIn(*line.split(' ', 1)) if ann.get('loc_prefixed') else engine.NodeIn(f'F1-P{i}', line)
                 for i, line in enumerate(lines, 1) if line.strip()]
        result = engine.compute(nodes, ann['stance'], main_prefix='F1-')
        flat = '\n'.join(n.text for n in nodes)
        seen = set()
        labels = [(item['text'], False) for item in ann.get('must_replace', [])]
        labels += [(word, False) for word in ann.get('must_suspect', [])]
        labels += [(word, True) for word in ann.get('plain_uncertain', [])]
        for word, ordinary in labels:
            identity = (parties.normalize(word), ordinary)
            if identity in seen:
                continue
            seen.add(identity)
            start = flat.index(word)
            reason = 'long_digits' if re.fullmatch(r'\d[\d -]*', word) and sum(c.isdigit() for c in word) >= 8 else 'ner'
            for n in nodes:
                pos = n.text.find(word)
                if pos >= 0:
                    hit = next((s for s in result.suspects if s.loc == n.loc and s.char_start <= pos
                                and s.char_end >= pos + len(word)), None)
                    if hit:
                        reason = hit.reason
                    break
            category = suspects.default_category(word, reason)
            eligible = category == 'TERM' or category == 'NAME' and keep_terms.eligible(word, reason)
            rows.append({'sample': sample, 'ordinary': ordinary, 'eligible': eligible,
                         'context': flat[max(0, start - 40):start] + '【' + word + '】' + flat[start + len(word):start + len(word) + 40]})
    return rows, digest.hexdigest()


def evaluate(rows):
    vectors = []
    for start in range(0, len(rows), 50):
        vectors.extend(embedding.embed([r['context'] for r in rows[start:start + 50]], 'passage'))
        print(f'已编码 {min(start + 50, len(rows))}/{len(rows)}', flush=True)
    vectors = np.asarray(vectors, dtype=np.float32)
    ordinary = np.array([r['ordinary'] for r in rows], dtype=bool)
    eligible = np.array([r['eligible'] for r in rows], dtype=bool)
    samples = np.array([r['sample'] for r in rows])
    # 与产品同用逐个查询的 float32 矩阵-向量乘法，避免批量矩阵乘法舍入差异越过最小阈值。
    scores = np.stack([np.clip(vectors @ query, -1, 1) for query in vectors])
    candidates = np.zeros(len(rows), dtype=bool)
    best = np.full(len(rows), -1.0)
    for i in range(len(rows)):
        pool = np.flatnonzero(samples != samples[i])
        if not eligible[i] or len(pool) < 5:
            continue
        near = pool[np.argsort(-scores[i, pool], kind='stable')[:5]]
        candidates[i] = ordinary[near].all()
        best[i] = float(scores[i, near[0]])
    bad = best[candidates & ~ordinary]
    # 余弦的下界是 -1；须严格高于全部错误候选的最大值，才满足零真名称误判。
    tau = float(np.nextafter(float(bad.max()), float('inf'))) if len(bad) else -1.0
    predicted = candidates & (best >= tau)
    true_count = int((~ordinary & eligible).sum())
    plain_count = int((ordinary & eligible).sum())
    kept = int((predicted & ordinary).sum())
    total_plain = int(ordinary.sum())
    rate = kept / total_plain if total_plain else 0
    metrics = dict(samples=len(set(samples)), items=len(rows), true_items=int((~ordinary).sum()), plain_items=total_plain,
                   eligible_true=true_count, eligible_plain=plain_count, k=5, threshold=tau,
                   false_keeps=int((predicted & ~ordinary).sum()), plain_kept=kept, plain_keep_ratio=rate,
                   enabled=bool(true_count >= 30 and plain_count >= 60 and rate >= 0.3))
    return metrics, vectors, ordinary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reference', type=Path)
    args = parser.parse_args()
    began = time.perf_counter()
    rows, digest = dataset()
    print(f'样本候选 {len(rows)}，开始离线向量交叉验证', flush=True)
    metrics, vectors, ordinary = evaluate(rows)
    metrics.update(model=embedding.model_id(), dataset_sha256=digest, seconds=round(time.perf_counter() - began, 3))
    args.out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if metrics['enabled'] and args.reference:
        np.savez_compressed(args.reference, vectors=vectors, ordinary=ordinary)
        metrics['reference_sha256'] = hashlib.sha256(args.reference.read_bytes()).hexdigest()
        args.out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(metrics, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
