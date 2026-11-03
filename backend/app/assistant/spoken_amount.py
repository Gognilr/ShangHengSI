"""仅逐字稿启用的口语金额；不猜币种或换算模糊数值。"""
import re

from app.redaction.detect import Hit

_CN = '一二三四五六七八九十百千两零〇'
_NUM = rf'(?:\d+(?:\.\d+)?|[{_CN}]+(?:点[{_CN}]+)?)'
# 万、亿可以连写（“一亿两千万”“三万五千”“一万一千块”），整串作为一个金额，不留下半截数字。
PATTERN = re.compile(rf'(?<![\d{_CN}万亿.点])(?:'
                     rf'(?:{_NUM}多?(?:千万|万|个亿|亿))+(?:{_NUM})?多?(?:块钱|块)?'
                     rf'|(?P<core>{_NUM})多?(?P<kuai>块钱|块))')
_COUNTED = re.compile(r'(?:人|次|个(?:人|台|件)|台|件|吨|米|平方米|区域|地方|土地|石头|蛋糕)')


def _pieces(text, m):
    """“这一块”“一块儿去”“分成三块屏幕”是量词或副词，不是金额。"""
    if m['kuai'] == '块钱':
        return False
    before, after = text[m.start() - 1:m.start()], text[m.end():m.end() + 1]
    if before in tuple('这那哪每第') or after == '儿' or m['core'] == '一':
        return True
    core = m['core']
    small = float(core) < 100 if core[0].isdigit() else not re.search('[百千]', core)
    return small and not (after == '' or after == '的' or not re.match(r'\w', after))


def find_amounts(text):
    # 只增不减：文档规则照常生效（出口复检也用文档规则），本规则不撤销它的命中。
    out = []
    for m in PATTERN.finditer(text):
        after = text[m.end():]
        if _COUNTED.match(after):
            continue
        if m['kuai'] is not None and _pieces(text, m):
            continue
        # “千万别”“千万要”是副词；“上千万”“几千万”仍是金额。
        if m.group().startswith('千万') and text[m.start() - 1:m.start()] not in tuple('上几数近好'):
            continue
        out.append(Hit(m.start(), m.end(), 'AMOUNT', m.group()))
    return out
