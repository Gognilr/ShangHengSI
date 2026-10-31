# V3-D 测试记录

## D-01（2026-10-31）

- 测试先行：先运行新测试，因尚无 revised_docx 模块而收集失败，再实现生成与接口。
- Docker / aireview-db:dev 专用测试库 aireview-v3d-test-db；未改原有旧测试库。
- `test_revised_export.py test_annotated_export.py test_docx_anchor.py`：40 通过，1 条既有 Starlette 弃用提醒。
- 覆盖：只写已采纳且有建议条文的问题、真实名称还原、替换与缺失型插入、重叠回退、未知代号转批注、顺序插入与编号样式继承、插入文字不影响后续定位、双视图与正文篡改校验、owner 404、原件含修订 / 无已采纳 / 全部无法修订的 409、响应计数与审计。既有批注版测试未改。
- 未新增待确认事项；未运行全量、未调用真实模型、未使用真实样本。Word 实际接受 / 拒绝与编号顺延留 D-03。
