"""普通词解释器：仅给不确定项与标签人名取值使用，不产生出口豁免。"""
from functools import lru_cache
import re

from app.redaction.dictionaries import RESOURCES_DIR

WORDS_FILE = RESOURCES_DIR / 'zh_plain_words.txt'
_HAN = re.compile(r'[一-鿿]+')
_ROLE_SUFFIXES = ('委员会', '机构', '部门', '平台', '人员', '工程师', '顾问', '专家', '师傅', '班组长', '班长', '队长', '组长',
                  '方', '人', '者', '员', '组', '站', '工')
_COMPANY = re.compile(r'(?:公司|厂|集团)$')
_MODIFIERS = re.compile(r'^(?:不同|全|各|权威|合理的)+')
# 设备与部件名称（“汽阀总成”“透平控制器”）：产品名称按 9.2 保留，只用于实体识别的中置信结果
_EQUIPMENT = re.compile(r'[一-鿿]{2,6}(?:总成|装置|系统|设备|机组|组件|部件|阀门|门|阀|泵|电机|电动缸|器|机|炉|塔|罐|槽|仪表|'
                        r'控制柜|柜|屏|模块|热电阻|热电偶|管道|管线|电缆|桥架|支架|法兰|平台|软件|终端|网关|云)')


def equipment_name(text: str, start: int, end: int) -> bool:
    return bool(_EQUIPMENT.fullmatch(text[start:end])) and not name_slot(text, start, end)


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


def short_name_slot(text: str, start: int, end: int) -> bool:
    """短值须独占名称标签到行尾，后续仍须实体识别的同区间证据。"""
    return bool(re.search(r'(?:名称|姓名|联系人|代表|经办人|负责人|签字|签名|抄送|收件人|单位|公司|厂名)'
                          r'(?:[（(][^）)\n]{0,12}[）)])?[ \t]*[:：][ \t]*$', text[:start])
                and re.fullmatch(r'[一-鿿]{2,4}', text[start:end])
                and not text[end:].strip(' \t。.;；\r\n'))


def independent_org(text: str) -> tuple[int, int] | None:
    """整行地名加短机构名是强槽位；不把叙述句或公共机构登记成私有名称。"""
    from app.redaction import parties as P
    surface = text.strip(' \t\r\n。.;；')
    place = P._place_prefix(surface)
    if not place:
        match = re.match(r'[一-鿿]{2,7}?(?:市|县|区)', surface)
        place = match.group() if match else ''
    tail = surface[len(place):]
    suffixes = '|'.join(sorted((*P._INSTITUTION_SUFFIXES, '厂'), key=len, reverse=True))
    if (place and re.fullmatch(r'[一-鿿]{2,10}(?:' + suffixes + ')', tail)
            and not re.search(r'的|在|于|应|须|负责|提供|按照|根据', tail)
            and not P.public_preservation_spans(surface)):
        start = text.index(surface)
        return start, start + len(surface)
    return None


def name_slot(text: str, start: int, end: int) -> bool:
    """名称标签、签字落款、地名与独立机构名的证据，优先于普通词解释。"""
    if (span := independent_org(text)) and span[0] <= start < end <= span[1]:
        return True
    head = text[:start]
    if re.search(r'(?:名称|姓名|联系人|代表|经办人|负责人|签字|签名|抄送|收件人|单位|公司|厂名)'
                 r'(?:[（(][^）)\n]{0,12}[）)])?\s*[:：]\s*$', head):
        return True
    if re.search(r'(?:先生|女士|经理|总监|主任|老师)', text[end:end + 3]):
        return True
    surface = text[start:end]
    # 完整企业后缀本身就是名称证据，在叙述中也不能按“有限｜公司”等普通词拆开保留。
    if re.search(r'(?:股份有限公司|有限责任公司|集团有限公司|有限公司|股份公司)$', surface):
        return True
    # 地名开头、完整机构后缀与整段独立的名称不能被词典中的行业词保留。
    from app.redaction import parties as P
    if P._place_prefix(surface):
        return True
    return bool(re.search(r'(?:研究院|研究所)$', surface)
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


def surname_in_word(text: str, start: int) -> bool:
    """“姓 + 称谓”的姓字与后文连成普通词时不是称呼：“施工图纸”的“施工”，“方经理”仍是称呼。"""
    left, right = start, start + 1
    while left and _HAN.fullmatch(text[left - 1]):
        left -= 1
    while right < len(text) and _HAN.fullmatch(text[right]):
        right += 1
    a = start - left
    return any(w and x <= a and y - x >= 2 and y > a + 1 for x, y, w in _tokens(text[left:right]))


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
    # “接受电厂”：后缀与前字组成普通词（电厂、工厂），前面是动词等叙述成分
    tokens = _tokens(surface)
    if (len(tokens) >= 2 and all(w for _, _, w in tokens) and tokens[-1][0] < suffix.start()
            and any(pos.startswith(('v', 'p', 'd')) for pos in words()[tokens[-2][2]])):
        return 'clause_before_company'
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
