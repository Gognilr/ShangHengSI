"""代号格式与分配（SPEC 9.2）。

同一任务内同一对象始终使用同一代号；字母和序号按首次出现顺序分配。
"""
import re

OUR_CODE = "[我方]"

# 不区分对象、所有命中共用一个代号的类别
FIXED = {
    "IM": "[联系方式]",
    "PHONE": "[电话]",
    "EMAIL": "[邮箱]",
    "URL": "[网址]",
    "IDNO": "[证件号]",
    "POSTCODE": "[邮编]",  # Q-139 ①：与电话相同，真实值写入对照表供出口复检
}

# 地址：代号只保留国家，同一国家的地址共用一个代号（与 FIXED 一样不能唯一还原，open_questions Q-45 ①、Q-71 ③）
ADDR = "ADDR"


def address_code(country: str) -> str:
    return f"[地址：{country}]"


# 按对象编号的类别
_NUMBERED = {
    "NAME": "[名称_{}]",
    "TERM": "[专有名词_{}]",
    "PROJECT": "[项目_{}]",
    "PERSON": "[人员_{}]",
    "REFNO": "[编号_{}]",
    "ACCOUNT": "[账号_{}]",  # Q-59：同一账号同一代号，可唯一还原
    "SWIFT": "[SWIFT_{}]",
    "AMOUNT": "[金额_{}]",
    "PRICE": "[单价_{}]",
    "QTY": "[数量_{}]",
    "CUSTOM": "[敏感词_{}]",
}

# 追问中出现、本任务对照表里没有的对象（M6-07，open_questions Q-97）：按任务编号，写入本任务对照表，可唯一还原。
# 序号带 Q 前缀，与解析时分配的代号不会重名；脱敏重算对照表时保留这些条目。
CHAT_LABELS = {
    "IM": "联系方式", "PROJECT": "项目",
    "ORG": "公司", "PERSON": "人员", "CUSTOM": "敏感词", "PHONE": "电话", "EMAIL": "邮箱", "URL": "网址",
    "IDNO": "证件号", "ACCOUNT": "账号", "SWIFT": "SWIFT", "REFNO": "编号", "AMOUNT": "金额", "PRICE": "单价",
    "ADDR": "地址", "POSTCODE": "邮编",
}
CHAT_CODE = re.compile(r"^\[[^\[\]_]+_Q(\d+)(?:：[^\[\]]*)?\]$")


def chat_code(category: str, n: int, country: str | None = None) -> str:
    """[公司_Q1]、[电话_Q2]；地址保留国家：[地址_Q3：阿联酋]。"""
    label = CHAT_LABELS.get(category, "敏感词")
    return f"[{label}_Q{n}：{country}]" if category == "ADDR" and country else f"[{label}_Q{n}]"


def chat_number(code: str) -> int | None:
    m = CHAT_CODE.match(code)
    return int(m.group(1)) if m else None


# 签约方标签（规范化为中文）→ 代号中的角色
BUYER = "买方"
SELLER = "卖方"
OTHER = "其他"

# 回程文法使用同一份类别标签；从现有格式导出，新增编号类别时自动生效。
ROLE_LABELS = frozenset(("客户", "供应商", BUYER, SELLER, "收货人", "通知方", "受益人", "银行", "公司", OTHER))
NUMBERED_LABELS = frozenset(fmt.strip("[]").split("_", 1)[0] for fmt in _NUMBERED.values()) | frozenset(CHAT_LABELS.values())


def letters(n: int) -> str:
    """0→A，25→Z，26→AA（与电子表格列名相同）。"""
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(ord("A") + r) + out
    return out


def code_role(role_label: str, stance: str, org_type: str | None) -> str:
    """9.2：立场为销售时买方写“客户”，立场为采购时卖方写“供应商”；收货人、通知方、银行等按标签写；无法判断时写“公司”。"""
    if role_label == BUYER:
        return "客户" if stance == "sales" else BUYER
    if role_label == SELLER:
        return "供应商" if stance == "purchase" else SELLER
    if role_label in ("收货人", "通知方", "受益人", "银行"):
        return role_label
    if org_type == "银行":
        return "银行"
    return "公司"


_NUMBERED_BY_LABEL = {fmt.strip("[]").split("_", 1)[0]: category for category, fmt in _NUMBERED.items()}
_ROLE_CODE = re.compile(r"\[([^\[\]_]+)_([A-Z]+)\]")
_NUMBER_CODE = re.compile(r"\[([^\[\]_]+)_(\d+)\]")


def letter_index(text: str) -> int:
    """letters() 的反函数：A→0，Z→25，AA→26。"""
    n = 0
    for ch in text:
        n = n * 26 + ord(ch) - ord("A") + 1
    return n - 1


class Allocator:
    """按首次出现顺序分配代号。

    seed() 之后对照表只增（V36-R-01）：首次送审后再重算时，旧对照表里的同一对象沿用原来的字母或序号（角色随立场变化），
    新对象接在已用过的最大号之后，号码不复用；旧轮次、追问与增量复审里的代号因此仍指向原来的对象。"""

    def __init__(self) -> None:
        self._org_letters = 0
        self._counters: dict[str, int] = {}
        self._codes: dict[tuple[str, str], str] = {}
        self._seed_letters: dict[str, list[int]] = {}
        self._seed_codes: dict[tuple[str, str], list[str]] = {}
        self._claimed: set[str | int] = set()

    def seed(self, pinned: dict[str, str]) -> None:
        from app.redaction.parties import normalize  # 延迟导入，避免循环导入
        for entry, value in sorted(pinned.items(), key=lambda kv: "#" in kv[0]):  # 主写法优先于其他写法
            if entry.startswith("@") or "#D" in entry or not value:
                continue
            code, variant = entry.split("#", 1)[0], "#" in entry
            role = _ROLE_CODE.fullmatch(code)
            if role and role[1] in ROLE_LABELS:
                index = letter_index(role[2])
                self._org_letters = max(self._org_letters, index + 1)
                if not variant:
                    self._seed_letters.setdefault(normalize(value), []).append(index)
                continue
            number = _NUMBER_CODE.fullmatch(code)
            category = _NUMBERED_BY_LABEL.get(number[1]) if number else None
            if category is None:
                continue
            self._counters[category] = max(self._counters.get(category, 0), int(number[2]))
            codes = self._seed_codes.setdefault((category, normalize(value)), [])
            if code not in codes:
                codes.append(code)

    def org(self, key: str, role: str) -> str:
        if ("ORG", key) not in self._codes:
            index = next((i for i in self._seed_letters.get(key, ()) if i not in self._claimed), None)
            if index is None:
                index = self._org_letters
                self._org_letters += 1
            self._claimed.add(index)
            self._codes[("ORG", key)] = f"[{role}_{letters(index)}]"
        return self._codes[("ORG", key)]

    def numbered(self, category: str, key: str, value: str | None = None) -> str:
        if (category, key) not in self._codes:
            code = None
            if value is not None and self._seed_codes:
                from app.redaction.parties import normalize
                code = next((c for c in self._seed_codes.get((category, normalize(value)), ())
                             if c not in self._claimed), None)
            if code is None:
                n = self._counters.get(category, 0) + 1
                self._counters[category] = n
                code = _NUMBERED[category].format(n)
            self._claimed.add(code)
            self._codes[(category, key)] = code
        return self._codes[(category, key)]

    def fixed(self, category: str, key: str) -> str:
        return FIXED[category]

    def address(self, country: str, key: str) -> str:
        return address_code(country)

    def our(self, key: str | None = None) -> str:
        return OUR_CODE


NEUTRAL = re.compile(r"\[\[(?:OUR|[A-Z]+_\d+)\]\]")
_NEUTRAL_LABELS = {
    "NAME": "NAME", "TERM": "PROPN",
    "ORG": "ORG", "PERSON": "PER", "PROJECT": "PRJ", "REFNO": "REF", "PHONE": "TEL",
    "EMAIL": "EMAIL", "URL": "URL", "POSTCODE": "ZIP", "ACCOUNT": "ACCT", "SWIFT": "SWIFT",
    "AMOUNT": "AMT", "PRICE": "PRICE", "QTY": "QTY", "CUSTOM": "TERM", "IM": "IM", "IDNO": "ID", "ADDR": "ADDR",
}
NEUTRAL_CATEGORIES = {label: category for category, label in _NEUTRAL_LABELS.items()}


class NeutralAllocator:
    """按类别和规范化真实值续号；已用过的号码永不复用。"""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._codes: dict[tuple[str, str], str] = {}

    @staticmethod
    def _normalize(key: str) -> str:
        # 延迟导入避免 parties 中的角色常量形成循环导入。
        from app.redaction.parties import normalize
        return normalize(key)

    def seed(self, pinned: dict[str, str]) -> None:
        for entry, value in pinned.items():
            code = entry.split("#", 1)[0]
            match = re.fullmatch(r"\[\[([A-Z]+)_(\d+)\]\]", code)
            if code == "[[OUR]]":
                # 旧会话已分配的 [[OUR]] 继续代表同一主体，新会话不再分配（V32-A-02）
                self._codes.setdefault(("ORG", self._normalize(value)), code)
                continue
            if not match or match[1] not in NEUTRAL_CATEGORIES:
                continue
            category, number = NEUTRAL_CATEGORIES[match[1]], int(match[2])
            self._counters[category] = max(self._counters.get(category, 0), number)
            self._codes.setdefault((category, self._normalize(value)), code)

    @staticmethod
    def category(entry: str) -> str:
        code = entry.split("#", 1)[0]
        if code == "[[OUR]]":
            return "ORG"
        if NEUTRAL.fullmatch(code):
            return NEUTRAL_CATEGORIES.get(code[2:-2].rsplit("_", 1)[0], "CUSTOM")
        return "CUSTOM"

    def numbered(self, category: str, key: str) -> str:
        identity = (category, self._normalize(key))
        if identity not in self._codes:
            number = self._counters.get(category, 0) + 1
            self._counters[category] = number
            self._codes[identity] = f"[[{_NEUTRAL_LABELS[category]}_{number}]]"
        return self._codes[identity]

    def org(self, key: str, role: str) -> str:
        return self.numbered("ORG", key)

    def fixed(self, category: str, key: str) -> str:
        return self.numbered(category, key)

    def address(self, country: str, key: str) -> str:
        return self.numbered("ADDR", key)

    def our(self, key: str | None = None) -> str:
        """会话不再分配 [[OUR]]：我方用该主体的 ORG 代号，“我方”只写在主体说明里（V32-A-02）。"""
        return self.numbered("ORG", key) if key is not None else "[[OUR]]"
