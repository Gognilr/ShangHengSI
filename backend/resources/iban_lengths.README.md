# IBAN 长度表

来源：[SWIFT IBAN Registry Release 101（2025-12）PDF](https://www.swift.com/sites/default/files/files/iban-registry-v101.pdf)，2026-11-02 通过浏览工具读取，按登记册逐项录入国家码与总长度（事实数据），共 89 个国家/地区。只保存事实表，不取登记册的示例账号、联系人或全文。

原文件 SHA-256 无法取得：官方下载在本机返回 403。官网列出的 Release 103 同样无法下载，因此没有采用；用户已同意本批固定 101，见 V31Q-43。本批不再尝试下载 103；下个版本的 -00 规划时检查一次。

逐项对照 python-stdnum 的 `stdnum/iban.dat`（https://raw.githubusercontent.com/arthurdejong/python-stdnum/master/stdnum/iban.dat ，2026-11-02 读取；该文件头标明源为 v101）。仅临时读取作对照，没有将该文件或其代码加入仓库。比较国家集合及各国 BBAN 长度加 4，89 项一致，无不一致国家。

生成文件 `iban_lengths.json` SHA-256：`8cd43068c53b59201680b4dd8535990f2b72044b637711cf7d7f76d2392e75ef`。
生成口径：JSON 国家码按字母排序，UTF-8，两个空格缩进，末尾换行。`scripts/generate_iban_lengths.py` 可检查格式与临时对照文件。
