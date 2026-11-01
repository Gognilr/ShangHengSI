# 中文词库：zh_words（V31-S-01）

来源：https://github.com/fxsjy/jieba/tree/v0.42.1 （固定标签 / 提交）。下载与使用由 V31Q-15 授权。
原文件位于该版本的 jieba/ 目录。

- `dict.txt`：SHA-256 `7197c3211ddd98962b036cdf40324d1ea2bfaa12bd028e68faa70111a88e12a8`；349046 行。

生成口径：jieba v0.42.1 主词典（不取 extra_dict）；2～4 个汉字、词频 ≥ 1000，词性排除 nr / nrfg / nrt / ns / nt / nz / j。合并去重，按 Unicode 排序，UTF-8、LF，每行一个词、末尾换行。
生成：`python backend/scripts/build_zh_words.py --source <原文件与许可目录> --out backend/resources`。
结果：4610 行，33305 字节；SHA-256 `8670c8dbdc20539a6e9f96ed2030553b95c089edb864c88da982dd8148c16617`。
只按 V31-S.md 1.2 过滤实体识别与疑似，不撤销规则、词条、签约方等已有替换。

## 剔除表

无（已有 golden 未出现回退）。

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
