# V31-E 测试记录

## E-01（2026-11-02）

- 迁移 0033：receipt、unable_reason、hidden_spans、formula_enc 与 L-13～L-22 约束；新增 send_ratios 默认关闭。后续任务使用的字段先落库，旧轮次不回填。
- 确认时保存数量回执，job 按本轮审核网关记录累计成功发送、字符、复检与拦截；含分段和格式重试，重入不重复计数。详情增加 receipt、restore_stats；本人可查看实际 user 消息，写 view_sent 审计；保留期清理后返回 available=false。
- 代表用例：回执分类合计、发送字符计数、实际请求一致、复用 A-03 的 outbound_records/recheck 检查真实值、他人 404、清理后不可用、旧回执 null；已有完整性用例补还原统计，已有拦截和分段用例补计数断言。
- 统计口径见 V31Q-57（待确认）：无精确对象数量的解析警告按文件计；还原统计为本次详情展示的检查累计，非去重数；rechecks 计成功与拦截，不计服务错误。
- Docker（Python 3.12，aireview-db:dev/pgvector，临时测试库）：runs_api + migrations 55 通过；追加拦截 / 分段 / 格式重试重点检查 4 通过。
- golden + A-03 + restore + review_job 首轮 346 通过 / 1 失败（600.41 秒）。失败为已有 `test_task_deleted_during_model_call`：模型调用期间删除任务后，新增回执逻辑更新缓存轮次触发 StaleDataError。修正为锁内重读现存轮次，已删除时跳过；修正后删除场景、回执、首段拦截、格式重试与第二段拦截共 5 项通过（48.41 秒）。A-03 外发检查通过。
- 只使用虚构样本和模拟网关，真实模型调用 0；未下载文件，未操作正式服务器。未跑整仓全量（留 E-08），未做界面实现（留 U3/U4）。
