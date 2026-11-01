# 中文词库：zh_legal_finance（V31-S-01）

来源：https://github.com/thunlp/THUOCL/tree/a30ce79d895d01ab5132a5c74c29703ff7efb4cc （固定标签 / 提交）。下载与使用由 V31Q-15 授权。
原文件位于该版本的 data/ 目录。

- `THUOCL_law.txt`：SHA-256 `8ef1a5f41052ac95dedde88b167c5031a43b3e0211bf128b6125736cc76ef7b2`；9896 行。
- `THUOCL_caijing.txt`：SHA-256 `68e63d2009e59c6f388eeaa789d19b6a681657de3e8e56cc3479b21cd8548542`；3830 行。

生成口径：法律与财经词表只取第一列，保留 2～8 个汉字；不取地名词库。合并去重，按 Unicode 排序，UTF-8、LF，每行一个词、末尾换行。
生成：`python backend/scripts/build_zh_words.py --source <原文件与许可目录> --out backend/resources`。
结果：9926 行，144161 字节；SHA-256 `bd443f75bbd997614d436e159811a7f2138eef645ccf334d9ab7d78f8a61e627`。
只按 V31-S.md 1.2 过滤实体识别与疑似，不撤销规则、词条、签约方等已有替换。

## 剔除表

无（已有 golden 未出现回退）。

## 上游许可全文

```text
MIT License

Copyright (c) 2018 THUNLP

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 上游 README 使用说明（原文）

1. THUOCL面向国内外大学、研究所、企业、机构以及个人免费开放，可用于研究与商业。
2. 欢迎对该工具包提出任何宝贵意见和建议。请发邮件至thunlp@gmail.com。
3. 如果您在THUOCL基础上发表论文或取得科研成果，请您在发表论文和申报成果时声明“使用了清华大学开放中文词库”，并按如下格式引用：

```
中文： 韩世依, 张钰晖, 马云山, 涂存超, 郭志芃, 刘知远, 孙茂松. THUOCL：清华大学开放中文词库. 2016.
```

```
英文： Shiyi Han, Yuhui Zhang, Yunshan Ma, Cunchao Tu, Zhipeng Guo, Zhiyuan Liu, Maosong Sun. THUOCL: Tsinghua Open Chinese Lexicon. 2016.
```
