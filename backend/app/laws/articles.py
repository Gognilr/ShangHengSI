"""从正文节点拆出法条，保留条内段落与单元格顺序。"""
import re
from dataclasses import dataclass

_NUM = '零〇一二三四五六七八九十百千'
_ARTICLE = re.compile(rf'^(第[{_NUM}]+条(?:之[一二三四五六七八九十]+)?)[　 \t]+')
_HEADING = re.compile(rf'^第[{_NUM}]+(?:编|分编|章|节)[　 ]')
_PART = re.compile(r'^[一二三四五六七八九十]+、')


@dataclass
class Article:
    article_no: str
    seq: int
    text: str


def split(nodes) -> list[Article]:
    out = []
    for node in nodes:
        if node.part != 'body':
            continue
        value = node.text.strip()
        if not value:
            continue
        match = _ARTICLE.match(value)
        if match:
            out.append(Article(match[1], len(out)+1, value[match.end():]))
        elif (_HEADING.match(value) or re.sub(r'\s', '', value) in ('总则', '分则', '附则') or
              (_PART.match(value) and len(value) <= 30 and '。' not in value)):
            continue
        elif out:
            out[-1].text += '\n' + value
    return out
