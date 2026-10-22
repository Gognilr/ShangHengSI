"""代号格式与分配（SPEC 9.2）。

同一任务内同一对象始终使用同一代号；字母和序号按首次出现顺序分配。
"""
import re

OUR_CODE = "我方"

# 不区分对象、所有命中共用一个代号的类别
FIXED = {
    "PHONE": "[电话]",
    "EMAIL": "[邮箱]",
    "URL": "[网址]",
    "IDNO": "[证件号]",
}

# 地址：代号只保留国家，同一国家的地址共用一个代号（与 FIXED 一样不能唯一还原，open_questions Q-45 ①、Q-71 ③）
ADDR = "ADDR"


def address_code(country: str) -> str:
    return f"[地址：{country}]"


# 按对象编号的类别
_NUMBERED = {
    "PERSON": "人员_{}",
    "REFNO": "编号_{}",
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
    "ORG": "公司", "PERSON": "人员", "CUSTOM": "敏感词", "PHONE": "电话", "EMAIL": "邮箱", "URL": "网址",
    "IDNO": "证件号", "ACCOUNT": "账号", "SWIFT": "SWIFT", "REFNO": "编号", "AMOUNT": "金额", "PRICE": "单价",
    "ADDR": "地址",
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


class Allocator:
    """按首次出现顺序分配代号。"""

    def __init__(self) -> None:
        self._org_letters = 0
        self._counters: dict[str, int] = {}
        self._codes: dict[tuple[str, str], str] = {}

    def org(self, key: str, role: str) -> str:
        if ("ORG", key) not in self._codes:
            self._codes[("ORG", key)] = f"{role}_{letters(self._org_letters)}"
            self._org_letters += 1
        return self._codes[("ORG", key)]

    def numbered(self, category: str, key: str) -> str:
        if (category, key) not in self._codes:
            n = self._counters.get(category, 0) + 1
            self._counters[category] = n
            self._codes[(category, key)] = _NUMBERED[category].format(n)
        return self._codes[(category, key)]
