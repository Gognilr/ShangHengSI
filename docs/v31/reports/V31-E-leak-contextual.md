# E-06a 完整上下文泄漏评测

种子 527；组 9084；位置 27252；核心 A 类未拦截残留 73；全部未拦截残留 2662。

口径按 V31-E 6.5，代码提交 ac4eb64。核心名称类纯变体仅报数；规则类两模式与名称类混合为门槛。每组3处，报告不含原值。同期另修正位置标记尾部的短金额 OCR 误拦，该修正不在本全集运行中。

## core

| 模式 | 类别 | 替换 | 拦截 | 泄漏 | 总位置 |
|-|-|-|-|-|-|
| mixed | ACCOUNT | 493 | 0 | 2 | 495 |
| mixed | ADDR | 297 | 0 | 0 | 297 |
| mixed | AMOUNT | 1420 | 18 | 26 | 1464 |
| mixed | EMAIL | 72 | 0 | 0 | 72 |
| mixed | IDNO | 913 | 2 | 0 | 915 |
| mixed | IM | 204 | 0 | 0 | 204 |
| mixed | ORG | 1461 | 0 | 0 | 1461 |
| mixed | PERSON | 936 | 0 | 0 | 936 |
| mixed | PHONE | 528 | 0 | 0 | 528 |
| mixed | POSTCODE | 93 | 0 | 0 | 93 |
| mixed | PRICE | 285 | 0 | 0 | 285 |
| mixed | PROJECT | 72 | 0 | 0 | 72 |
| mixed | REFNO | 2328 | 0 | 0 | 2328 |
| mixed | SWIFT | 237 | 0 | 0 | 237 |
| mixed | URL | 12 | 0 | 0 | 12 |
| pure | ACCOUNT | 492 | 0 | 3 | 495 |
| pure | ADDR | 291 | 3 | 3 | 297 |
| pure | AMOUNT | 1398 | 27 | 39 | 1464 |
| pure | EMAIL | 72 | 0 | 0 | 72 |
| pure | IDNO | 912 | 0 | 3 | 915 |
| pure | IM | 204 | 0 | 0 | 204 |
| pure | ORG | 1458 | 0 | 3 | 1461 |
| pure | PERSON | 924 | 0 | 12 | 936 |
| pure | PHONE | 528 | 0 | 0 | 528 |
| pure | POSTCODE | 93 | 0 | 0 | 93 |
| pure | PRICE | 285 | 0 | 0 | 285 |
| pure | PROJECT | 72 | 0 | 0 | 72 |
| pure | REFNO | 2094 | 0 | 234 | 2328 |
| pure | SWIFT | 237 | 0 | 0 | 237 |
| pure | URL | 12 | 0 | 0 | 12 |

## reference

| 模式 | 类别 | 替换 | 拦截 | 泄漏 | 总位置 |
|-|-|-|-|-|-|
| mixed | ACCOUNT | 150 | 0 | 0 | 150 |
| mixed | ADDR | 127 | 0 | 26 | 153 |
| mixed | AMOUNT | 252 | 2 | 178 | 432 |
| mixed | EMAIL | 18 | 12 | 24 | 54 |
| mixed | IDNO | 336 | 0 | 0 | 336 |
| mixed | IM | 91 | 26 | 30 | 147 |
| mixed | ORG | 779 | 0 | 322 | 1101 |
| mixed | PERSON | 443 | 6 | 64 | 513 |
| mixed | PHONE | 141 | 0 | 0 | 141 |
| mixed | POSTCODE | 27 | 0 | 0 | 27 |
| mixed | PRICE | 60 | 0 | 24 | 84 |
| mixed | PROJECT | 36 | 0 | 0 | 36 |
| mixed | REFNO | 862 | 2 | 0 | 864 |
| mixed | SWIFT | 180 | 0 | 0 | 180 |
| mixed | URL | 5 | 0 | 4 | 9 |
| pure | ACCOUNT | 132 | 6 | 12 | 150 |
| pure | ADDR | 114 | 0 | 39 | 153 |
| pure | AMOUNT | 162 | 3 | 267 | 432 |
| pure | EMAIL | 0 | 18 | 36 | 54 |
| pure | IDNO | 333 | 3 | 0 | 336 |
| pure | IM | 63 | 33 | 51 | 147 |
| pure | ORG | 414 | 0 | 687 | 1101 |
| pure | PERSON | 363 | 6 | 144 | 513 |
| pure | PHONE | 93 | 0 | 48 | 141 |
| pure | POSTCODE | 27 | 0 | 0 | 27 |
| pure | PRICE | 48 | 0 | 36 | 84 |
| pure | PROJECT | 36 | 0 | 0 | 36 |
| pure | REFNO | 369 | 222 | 273 | 864 |
| pure | SWIFT | 99 | 15 | 66 | 180 |
| pure | URL | 3 | 0 | 6 | 9 |

## 核心门槛残留逐组清单

| 序号 | 样本标识 | 类别 | 变体 | 模式 |
|-|-|-|-|-|
| 1 | quote_two_headers | AMOUNT | chinese_upper | pure |
| 2 | quote_two_headers | AMOUNT | chinese_upper | mixed |
| 3 | quote_two_headers | AMOUNT | chinese_lower | pure |
| 4 | quote_two_headers | AMOUNT | chinese_lower | mixed |
| 5 | quote_two_headers | AMOUNT | ocr | pure |
| 6 | quote_two_headers | AMOUNT | ocr | mixed |
| 7 | amounts_cn_forms | AMOUNT | chinese_upper | pure |
| 8 | amounts_cn_forms | AMOUNT | chinese_upper | mixed |
| 9 | amounts_cn_forms | AMOUNT | chinese_lower | pure |
| 10 | amounts_cn_forms | AMOUNT | chinese_lower | mixed |
| 11 | e06a_formatted | ACCOUNT | ocr | pure |
| 12 | e06a_formatted | ACCOUNT | ocr | mixed |
| 13 | quote_two_headers | AMOUNT | chinese_upper | pure |
| 14 | quote_two_headers | AMOUNT | chinese_upper | mixed |
| 15 | quote_two_headers | AMOUNT | chinese_lower | pure |
| 16 | quote_two_headers | AMOUNT | chinese_lower | mixed |
| 17 | quote_two_headers | AMOUNT | ocr | pure |
| 18 | quote_two_headers | AMOUNT | ocr | mixed |
| 19 | amounts_cn_forms | AMOUNT | chinese_upper | pure |
| 20 | amounts_cn_forms | AMOUNT | chinese_upper | mixed |
| 21 | e06a_formatted | IDNO | hyphens | pure |
| 22 | fill_brackets | AMOUNT | chinese_upper | pure |
| 23 | fill_brackets | AMOUNT | chinese_upper | mixed |
| 24 | amounts_cn_forms | AMOUNT | chinese_upper | pure |
| 25 | amounts_cn_forms | AMOUNT | chinese_upper | mixed |
| 26 | amounts_cn_forms | AMOUNT | chinese_lower | pure |
| 27 | amounts_cn_forms | AMOUNT | chinese_lower | mixed |
| 28 | fill_brackets | AMOUNT | chinese_lower | pure |
| 29 | fill_brackets | AMOUNT | chinese_lower | mixed |

## 当前提交定向复现

提交 4a75806；种子 1；组 2；核心 A 类未拦截残留 5。仅定向复现 quote_two_headers 的 chinese_upper，两种模式均运行；不是当前提交的全集结果。

结论：核心门槛未达，按设计 6.5 停交 Opus。E-06a 不结项，E-07 / E-08 未开始。

真实模型调用 0。分片日志、原值诊断保存在仓库外。本结果为本机离线评测。
