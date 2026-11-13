# 普通词库（V321-B-02）

来源：https://github.com/fxsjy/jieba/tree/v0.42.1 ，只取 jieba/dict.txt；沿用 V31Q-15 来源，用户 2026-11-13 同意下载固定版本词典及许可。
输入 SHA-256：`7197c3211ddd98962b036cdf40324d1ea2bfaa12bd028e68faa70111a88e12a8`。
口径：1～8 个汉字；排除 nr / ns / nt / nz 及其子类；多字词频 ≥ 5（首次 50，按基准只校准一次）。普通简称 j 仍收，如“研发”；专名词性仍不收。
单字只收白名单与上游词典的交集：一不与且为之也了于人仅仍从他以们你使全其再到及可各向员和在套她它对将已并应我或所把按方无是有未本此由的站等级组经给者而虚被让该这那都。保留词性，每行“词\t词性”，UTF-8、LF、Unicode 排序。
结果：86004 行；SHA-256 `694283ee5186416e8240826c168f487bc869d21f776e3ca9ced1d79bb91432ad`。
生成：`python scripts/build_zh_words.py --plain-only --source <词典及许可目录> --out resources`（Docker 内）。
旧 zh_words.txt 及其用途不变。本词库只解释不确定候选及标签人名取值，不改变出口复检，也不撤销已有强识别。

## 上游许可全文

```text
The MIT License (MIT)

Copyright (c) 2013 Sun Junyi

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
