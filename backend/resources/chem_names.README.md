# 化工品名（V31-S-01a）

来源：[2015 年第 5 号公告（2015-02-27）](https://www.mem.gov.cn/gk/gwgg/xgxywj/wxhxp_228/201503/t20150309_232632.shtml)，
[官方 DOC 原件](https://www.mem.gov.cn/gk/gwgg/xgxywj/wxhxp_228/201503/W020240524642146440778.doc)。
调整：[2022 年第 8 号公告（2022-10-13）](https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202211/t20221107_426077.shtml)，
[官方 PDF 原件](https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202211/P020221107750004104671.pdf)。
下载由用户 2026-11-02 确认（设计 12.1）；政府规范性文件按著作权法第五条不受保护。

- `catalog.doc` SHA-256 `4ad47e211763b849e6339ab09dd28cc2c55686463a15d13bf31c736d2cc12070`
- `adjustment.pdf` SHA-256 `ce6f28beff9c819b01a26a75ec8dd35c9f2919354aa2493695f504ca2afc6074`

用仓库现有 aireview-converter:dev 的 LibreOffice 将 catalog.doc 转为 catalog.docx，
然后在后端测试容器运行 `python scripts/build_chem_names.py --source <原件目录> --out resources`。
只读表格第 2、3 列，以分号或换行拆分、去空白，保留 2～30 字；不取 CAS、备注、序号。
按调整公告把 1674 项品名改为“柴油”，不移植闭杯闪点条件。
去重、Unicode 排序、UTF-8/LF，共 5156 行；SHA-256 `f71ee3191390b3784dbc8e48fd27596f619c63f36f8f1c288fb61ce60c9cf79f`。
只用于实体识别整段相等与不确定项最长匹配，不改变规则、企业后缀、签约方与已有替换。
