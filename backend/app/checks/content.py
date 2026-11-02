"""L-20、L-21：隐藏文字与疑似写给 AI 的指令，只提示，不修改正文。"""
import re
from functools import lru_cache
from pathlib import Path

from app.checks.model import CheckNode, CheckResult, SEVERITY


@lru_cache(maxsize=1)
def _patterns():
    path = Path(__file__).resolve().parents[2] / 'resources' / 'ai_instruction_patterns.txt'
    return tuple(re.compile(line, re.I) for raw in path.read_text(encoding='utf-8').splitlines()
                 if (line := raw.strip()) and not line.startswith('#'))


def check_content(nodes: list[CheckNode]) -> list[CheckResult]:
    results = []
    for node in nodes:
        if node.hidden_spans:
            excerpt = ''.join(node.text[a:b] for a, b in node.hidden_spans)[:60]
            summary = f'{node.loc} 含 Word 中不显示的文字（隐藏、白色或极小字号）'
            results.append(CheckResult('L-20', SEVERITY['L-20'], [node.loc], node.file_seq,
                                       summary + '：' + excerpt, summary))
        matches = sorted({m.span() for pattern in _patterns() for m in pattern.finditer(node.text)})
        if not matches:
            continue
        sentences = []
        for start, end in matches:
            left = max((m.end() for m in re.finditer(r'[。！？!?\n；;]', node.text[:start])), default=0)
            following = re.search(r'[。！？!?\n；;]', node.text[end:])
            right = end + following.end() if following else len(node.text)
            sentence = node.text[left:right].strip()
            if any(a < end and start < b for a, b in (node.hidden_spans or [])):
                sentence += '（位于隐藏文字中）'
            if sentence not in sentences:
                sentences.append(sentence)
        summary = f'{node.loc} 含疑似写给 AI 的指令，按文件数据处理'
        results.append(CheckResult('L-21', SEVERITY['L-21'], [node.loc], node.file_seq,
                                   summary + '：' + '\n'.join(sentences), summary))
    return results
