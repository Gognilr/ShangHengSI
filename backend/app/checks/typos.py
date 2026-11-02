"""开发方起草的合同错写法；不做分词、模糊纠正或法律近义词判断。"""
from functools import lru_cache
from pathlib import Path
import re

from app.checks.model import CheckNode, CheckResult

_RESOURCES = Path(__file__).resolve().parents[2] / 'resources'


@lru_cache(maxsize=1)
def _data():
    pairs = dict(line.split('→') for line in (_RESOURCES / 'typo_pairs.txt').read_text(encoding='utf-8').splitlines()
                 if line and not line.startswith('#'))
    whitelist = set((_RESOURCES / 'typo_whitelist.txt').read_text(encoding='utf-8').split())
    # 长词优先，同一处不重复报告短词；白名单的完整词不参与纠正。
    pattern = re.compile('|'.join(re.escape(word) for word in sorted(pairs, key=len, reverse=True)))
    return pairs, whitelist, pattern


def check_typos(nodes: list[CheckNode]) -> list[CheckResult]:
    pairs, whitelist, pattern = _data()
    results = []
    for node in nodes:
        protected = [(m.start(), m.end()) for word in whitelist for m in re.finditer(re.escape(word), node.text)]
        found = dict.fromkeys(m.group() for m in pattern.finditer(node.text)
                              if not any(m.start() < end and m.end() > start for start, end in protected))
        if not found:
            continue
        # 每节点一条；只写固定词对，不带周边原文，避免摘要泄露名称或金额。
        detail = f'{node.loc} 疑似错别字：' + '；'.join(f'‘{word}’应为‘{pairs[word]}’' for word in found)
        results.append(CheckResult('L-22', 'low', [node.loc], node.file_seq, detail, detail))
    return results
