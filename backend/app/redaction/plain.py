"""普通词解释器：仅给不确定项与标签人名取值使用，不产生出口豁免。"""
from functools import lru_cache
import re

from app.redaction.dictionaries import RESOURCES_DIR

WORDS_FILE = RESOURCES_DIR / 'zh_plain_words.txt'
_HAN = re.compile(r'[一-鿿]+')
_ROLE_SUFFIXES = ('委员会', '机构', '部门', '平台', '人员', '方', '人', '者', '员', '组', '站')
_COMPANY = re.compile(r'(?:公司|厂|集团)$')
_MODIFIERS = re.compile(r'^(?:不同|全|各|权威|合理的)+')


@lru_cache(maxsize=1)
def words() -> dict[str, frozenset[str]]:
    entries: dict[str, set[str]] = {}
    for line in WORDS_FILE.read_text(encoding='utf-8').splitlines():
        word, pos = line.split('\t')
        entries.setdefault(word, set()).add(pos)
    return {w: frozenset(tags) for w, tags in entries.items()}


@lru_cache(maxsize=8192)
def _tokens(sentence: str) -> tuple[tuple[int, int, str], ...]:
    """整句先最少未知字，再最少词数；避免“全集团”贪心切成“全集｜团”。"""
    lexicon = words()
    size = len(sentence)
    scores = [(0, 0)] * (size + 1)
    edges = [None] * size
    for start in range(size - 1, -1, -1):
        options = [(start + 1, '')]
        options += [(e, sentence[start:e]) for e in range(min(size, start + 8), start, -1)
                    if sentence[start:e] in lexicon]
        end, word = min(options, key=lambda pair: (scores[pair[0]][0] + (not pair[1]),
                                                  scores[pair[0]][1] + 1, -pair[0]))
        scores[start] = (scores[end][0] + (not word), scores[end][1] + 1)
        edges[start] = (end, word)
    tokens = []
    start = 0
    while start < size:
        end, word = edges[start]
        tokens.append((start, end, word))
        start = end
    return tuple(tokens)


def _covered(surface: str) -> bool:
    return bool(surface) and bool(_HAN.fullmatch(surface)) and all(w for _, _, w in _tokens(surface))


def name_slot(text: str, start: int, end: int) -> bool:
    """B-02 最小名称槽位：名称标签、签字落款与独立企业名；B-04 再扩充。"""
    head = text[:start]
    if re.search(r'(?:名称|姓名|联系人|代表|经办人|负责人|签字|签名|抄送|收件人|单位|公司|厂名)'
                 r'(?:[（(][^）)\n]{0,12}[）)])?\s*[:：]\s*$', head):
        return True
    if re.search(r'(?:先生|女士|经理|总监|主任|老师)', text[end:end + 3]):
        return True
    surface = text[start:end]
    # 地名开头、完整机构后缀与整段独立的名称不能被词典中的行业词保留。
    from app.redaction import parties as P
    if P._place_prefix(surface):
        return True
    return bool(re.search(r'(?:有限公司|股份公司|研究院|研究所)$', surface)
                and not text[:start].strip() and not text[end:].strip(' 。；;\n'))


def explanation(text: str, start: int, end: int, *, slot: bool = True) -> str | None:
    if not 0 <= start < end <= len(text) or not _HAN.fullmatch(text[start:end]):
        return None
    if slot and name_slot(text, start, end):
        return None
    # 汉字连续段即切分窗口；标点、数字、拉丁字母自然隔断，候选仍用原文偏移。
    left, right = start, end
    while left and _HAN.fullmatch(text[left - 1]):
        left -= 1
    while right < len(text) and _HAN.fullmatch(text[right]):
        right += 1
    tokens = _tokens(text[left:right])
    a, b = start - left, end - left
    if any(w and y - x >= 2 and (x < a < y or x < b < y) for x, y, w in tokens):
        return 'word_boundary'
    if all(w for x, y, w in tokens if x < b and a < y):
        return 'ordinary_words'
    surface = text[start:end]
    for suffix in _ROLE_SUFFIXES:
        if surface.endswith(suffix) and _covered(_MODIFIERS.sub('', surface[:-len(suffix)])):
            return 'generic_role'
    return None


def explains(text: str, start: int, end: int) -> bool:
    return explanation(text, start, end) is not None


def company_explanation(text: str, start: int, end: int) -> str | None:
    """公司前的叙述成分必须可解释，末词为动、形、副、介词；普通名词不据此放行。"""
    if name_slot(text, start, end):
        return None
    surface = text[start:end]
    suffix = _COMPANY.search(surface)
    if not suffix:
        return None
    head = surface[:suffix.start()]
    # 截取名称时可能从“系统”的“统”开始，前段是否普通必须读整句边界。
    if head and explanation(text, start, start + suffix.start(), slot=False):
        last = _tokens(head)[-1][2]
        if not last:
            return None
        if any(pos.startswith(('v', 'a', 'd', 'p')) for pos in words()[last]):
            return 'clause_before_company'
        # “设计｜单体”“独立｜法人”是动词加普通名词的描述，不是纯名词字号。
        tokens = _tokens(head)
        if len(tokens) >= 2 and any(pos.startswith(('v', 'a')) for pos in words().get(tokens[-2][2], ())):
            return 'clause_before_company'
    return None
