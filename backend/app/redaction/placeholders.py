"""代号格式与分配（SPEC 9.2）。

同一任务内同一对象始终使用同一代号；字母和序号按首次出现顺序分配。
"""

OUR_CODE = "我方"

# 不区分对象、所有命中共用一个代号的类别
FIXED = {
    "PHONE": "[电话]",
    "EMAIL": "[邮箱]",
    "URL": "[网址]",
    "ACCOUNT": "[账号]",
    "SWIFT": "[SWIFT]",
    "IDNO": "[证件号]",
}

# 按对象编号的类别
_NUMBERED = {
    "PERSON": "人员_{}",
    "REFNO": "编号_{}",
    "AMOUNT": "[金额_{}]",
    "PRICE": "[单价_{}]",
    "QTY": "[数量_{}]",
    "CUSTOM": "[敏感词_{}]",
}

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
