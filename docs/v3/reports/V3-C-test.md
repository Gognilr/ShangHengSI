# V3-C 测试记录

## V3-C-01（2026-10-31，已完成）

- 输出结构说明已更新：quote 必须取自 locs 首个位置，逐字连续、不跨段、不加省略号、最多 100 字，缺失型为 null；suggested_text 为该片段的完整替换文字，缺失型为完整新增条款，无法提供时为 null。cite_status、结果页与报告标签未改。
- 在现有提示词用例中增加两项字段说明断言，未新增测试函数。Docker Python 3.12、`aireview-db:dev`（pgvector）的既有测试库容器 `aireview-v3b-test-db`，测试自建并删除临时数据库：`test_review_prompt.py` **11 passed**（33.18 秒），一个既有 Starlette/httpx 弃用警告。
- `test_redaction_golden.py` 与 `test_a03_outbound.py` **228 passed**（315.34 秒），一个相同的既有弃用警告；出口回归通过。
- V3Q-18 已由用户批准并提交 `5f9084c`：system.md 仅升级版本号至 `2026.11-4`，正文不变；test_requirements.py、test_runs_api.py 只同步版本断言，未改其他断言、未增加用例。最终复跑这两个文件及 test_review_prompt.py，**68 passed**（124.58 秒），一个相同的既有弃用警告；未重复 golden 与 A-03。
- 未跑整仓全量；模型调用全部模拟，未下载文件、未连接正式服务器。既有未跟踪资料保留。
