# E-06a 最终代码的完整两模式评测

代码 83d77f2；种子 530；组 8354；位置 25062；核心 A 类未拦截残留 0。

按 V31-E 6.5 / 6.6 分级。变体样本只评原写法；普通表格数字格的中文金额、无分隔证件号的分组列参考。名称类纯变体仅报数。旧报告保留，因分级与种子规模变化，不直接比较泄漏率。

## core

| 模式 | 类别 | 替换 | 拦截 | 残留未拦截 | 总位置 |
|-|-|-|-|-|-|
| mixed | ACCOUNT | 333 | 0 | 0 | 333 |
| mixed | ADDR | 297 | 0 | 0 | 297 |
| mixed | AMOUNT | 1193 | 4 | 0 | 1197 |
| mixed | EMAIL | 72 | 0 | 0 | 72 |
| mixed | IDNO | 612 | 0 | 0 | 612 |
| mixed | IM | 204 | 0 | 0 | 204 |
| mixed | ORG | 1422 | 0 | 0 | 1422 |
| mixed | PERSON | 909 | 0 | 0 | 909 |
| mixed | PHONE | 453 | 0 | 0 | 453 |
| mixed | POSTCODE | 93 | 0 | 0 | 93 |
| mixed | PRICE | 225 | 0 | 0 | 225 |
| mixed | PROJECT | 72 | 0 | 0 | 72 |
| mixed | REFNO | 2328 | 0 | 0 | 2328 |
| mixed | SWIFT | 177 | 0 | 0 | 177 |
| mixed | URL | 12 | 0 | 0 | 12 |
| pure | ACCOUNT | 333 | 0 | 0 | 333 |
| pure | ADDR | 291 | 3 | 3 | 297 |
| pure | AMOUNT | 1191 | 6 | 0 | 1197 |
| pure | EMAIL | 72 | 0 | 0 | 72 |
| pure | IDNO | 612 | 0 | 0 | 612 |
| pure | IM | 204 | 0 | 0 | 204 |
| pure | ORG | 1419 | 0 | 3 | 1422 |
| pure | PERSON | 897 | 0 | 12 | 909 |
| pure | PHONE | 453 | 0 | 0 | 453 |
| pure | POSTCODE | 93 | 0 | 0 | 93 |
| pure | PRICE | 225 | 0 | 0 | 225 |
| pure | PROJECT | 72 | 0 | 0 | 72 |
| pure | REFNO | 2094 | 0 | 234 | 2328 |
| pure | SWIFT | 177 | 0 | 0 | 177 |
| pure | URL | 12 | 0 | 0 | 12 |

## reference

| 模式 | 类别 | 替换 | 拦截 | 残留未拦截 | 总位置 |
|-|-|-|-|-|-|
| mixed | ACCOUNT | 90 | 0 | 0 | 90 |
| mixed | ADDR | 127 | 0 | 26 | 153 |
| mixed | AMOUNT | 334 | 2 | 186 | 522 |
| mixed | EMAIL | 18 | 12 | 24 | 54 |
| mixed | IDNO | 321 | 0 | 0 | 321 |
| mixed | IM | 91 | 26 | 30 | 147 |
| mixed | ORG | 755 | 0 | 310 | 1065 |
| mixed | PERSON | 429 | 6 | 60 | 495 |
| mixed | PHONE | 114 | 0 | 0 | 114 |
| mixed | POSTCODE | 27 | 0 | 0 | 27 |
| mixed | PRICE | 60 | 0 | 36 | 96 |
| mixed | PROJECT | 36 | 0 | 0 | 36 |
| mixed | REFNO | 862 | 2 | 0 | 864 |
| mixed | SWIFT | 132 | 0 | 0 | 132 |
| mixed | URL | 5 | 0 | 4 | 9 |
| pure | ACCOUNT | 81 | 3 | 6 | 90 |
| pure | ADDR | 114 | 0 | 39 | 153 |
| pure | AMOUNT | 240 | 3 | 279 | 522 |
| pure | EMAIL | 0 | 18 | 36 | 54 |
| pure | IDNO | 318 | 3 | 0 | 321 |
| pure | IM | 63 | 33 | 51 | 147 |
| pure | ORG | 396 | 0 | 669 | 1065 |
| pure | PERSON | 351 | 6 | 138 | 495 |
| pure | PHONE | 66 | 0 | 48 | 114 |
| pure | POSTCODE | 27 | 0 | 0 | 27 |
| pure | PRICE | 42 | 0 | 54 | 96 |
| pure | PROJECT | 36 | 0 | 0 | 36 |
| pure | REFNO | 369 | 222 | 273 | 864 |
| pure | SWIFT | 60 | 12 | 60 | 132 |
| pure | URL | 3 | 0 | 6 | 9 |

## 核心门槛残留逐组清单

| 序号 | 样本标识 | 类别 | 变体 | 模式 |
|-|-|-|-|-|
| 0 | — | — | — | — |

真实模型调用 0；这是本机离线评测。原始诊断与分片日志留在仓库外。
