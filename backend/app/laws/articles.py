"""从正文节点拆出法条，保留条内段落与单元格顺序。"""
import re
import unicodedata
from dataclasses import dataclass

_NUM = '零〇一二三四五六七八九十百千'
_ARTICLE = re.compile(rf'^(第[{_NUM}]+条(?:之[一二三四五六七八九十]+)?)[　 \t]+')
_HEADING = re.compile(rf'^第[{_NUM}]+(?:编|分编|章|节)[　 ]')
_PART = re.compile(r'^[一二三四五六七八九十]+、')
_LOOSE_ARTICLE = re.compile(rf'^(第([{_NUM}]+)条(?:之[一二三四五六七八九十]+)?)')
_ITEM = re.compile(r'^(?:[一二三四五六七八九十]+、|[0-9０-９]{1,3}[．.、])')
_DATE = re.compile(r'^[〇○零一二三四五六七八九十\d]+年[〇○零一二三四五六七八九十\d]+月[〇○零一二三四五六七八九十\d]+日$')


@dataclass
class Article:
    article_no: str | None
    seq: int
    text: str


def normalize_title(value: str) -> str:
    """仅比较时规范化；存储与显示保留原文。"""
    return re.sub(r'\s', '', unicodedata.normalize('NFKC', value))


def _number(value):
    digits = dict(zip('零〇一二三四五六七八九', [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))
    total = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        else:
            total += (current or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            current = 0
    return total + current


def _preamble(value, title):
    compact = re.sub(r'\s', '', value)
    return ((bool(title) and normalize_title(value) == normalize_title(title)) or
            (value.startswith(('（', '(')) and value.endswith(('）', ')'))) or
            (len(value) <= 40 and re.search(r'〔\d{4}〕[^。]*号$', value) is not None) or
            _DATE.fullmatch(compact) is not None or
            (len(value) <= 20 and value.endswith('公告') and '。' not in value))


def split(nodes, title: str = '') -> list[Article]:
    nodes = list(nodes)
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
    if out:
        return out
    # 仅严格规则整份零命中时放宽条号；跳号引用仍归上一条。
    previous = 0
    for node in nodes:
        if node.part != 'body' or not node.text.strip():
            continue
        value = node.text.strip()
        match = _LOOSE_ARTICLE.match(value)
        number = _number(match[2]) if match else None
        if match and ((previous == 0 and match[1] == '第一条') or
                      (previous > 0 and (number == previous + 1 or ('之' in match[1] and number == previous)))):
            out.append(Article(match[1], len(out) + 1, value[match.end():].lstrip()))
            previous = number
        elif (_HEADING.match(value) or re.sub(r'\s', '', value) in ('总则', '分则', '附则') or
              (_PART.match(value) and len(value) <= 30 and '。' not in value)):
            continue
        elif out:
            out[-1].text += '\n' + value
    if out:
        return out
    started = False
    for node in nodes:
        if node.part != 'body' or not node.text.strip():
            continue
        value = node.text.strip()
        if not started and _preamble(value, title):
            continue
        started = True
        # 600 字按原段落文字计数，不把拼接的换行计为正文字符。
        size = sum(len(line) for line in out[-1].text.split('\n')) if out else 0
        if not out or _ITEM.match(value) or size + len(value) > 600:
            out.append(Article(None, len(out) + 1, value))
        else:
            out[-1].text += '\n' + value
    # 标题式短段向后合并；末尾短段向前合并，连续标题保持原顺序。
    merged = []
    pending = []
    for article in out:
        value = article.text
        if '\n' not in value and len(value) <= 30 and '。' not in value:
            pending.append(value)
        else:
            merged.append(Article(None, len(merged) + 1, '\n'.join(pending + [value])))
            pending = []
    if pending:
        if merged:
            merged[-1].text += '\n' + '\n'.join(pending)
        else:
            merged.append(Article(None, 1, '\n'.join(pending)))
    return merged
