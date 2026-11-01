# V31-R 测试记录

## V31-R-01（2026-11-01）

- 新增 `check()`、不可变的 `Mark` / `Checked`：扫描模型原文一次，无歧义变体按对照表还原；不确定写法、未知代号、合同中的共用代号及系统标记分别标出。建议条文删除金额比例注记；普通字段按调用方的位置表替换位置编号。
- `restore()` 保持原样；助手只沿用它已有的还原及括号兼容，已结束消息与 `incremental(finished=True)` 逐字一致。替换后的真实值不再扫描，标记下标按最终输出的 Python 字符串位置计算。
- `placeholders.py` 只导出类别标签，未改分配格式。contract、quotation、inquiry 三份已有 golden 新增 15 条 `returns`；新增一个参数化读取函数与 3 条专门测试。涵盖新旧写法、英文、全角、歧义、未知代号、共用代号、比例、位置、报价表标记和助手。
- 测试先行：新增测试首次因缺少 `check` 而收集失败；实现后相关筛选 22 项通过。复核修正 SWIFT 普通编号与助手外形重合时的优先级，并补充 Q 编号；最终还原文件筛选 4 项通过。
- 相关完整回归：`tests/test_restore.py tests/test_redaction_golden.py tests/test_a03_outbound.py` 共 **242 通过、0 失败、0 跳过**，348.08 秒；最终 SWIFT 优先级补改由上述还原文件筛选 4 项验证。仅有既有 Starlette/httpx 弃用警告。使用 Docker Python 3.12、`aireview-backend-test`，连接 `aireview-v3d-test-db`（`aireview-db:dev`、带 pgvector）；未使用旧的 `aireview-test-db`（postgres:16.15）。
- 未跑整仓全量，留 R-05；未调用真实模型、下载文件或操作正式服务器。无新增待确认问题。结果页、追问和各导出的接入留 R-04；本任务只提供检查函数与回程样本。
- 实现提交：`c5ce364`。下一任务 V31-R-02（GPT-6.1 Sol，high），按已批准的体检安排准备专用评测库，模型密钥须由用户本人在终端录入。
