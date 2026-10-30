# V2-F 测试与交接

本批仅本机验证，模型网关使用模拟服务；不操作正式服务器。各任务分别提交，F-05 后交 Opus 执行 F-06。

## F-01 零宽字符

解析节点与任务补充说明、追问 preview/send、助手消息入口删除设计指定的六种字符。新增追问用例修复前失败（已知名称没有沿用原代号）。同名 `cases_zero_width.docx` 含原始零宽字符，`.txt/.yaml` 标注归一后的识别结果；容器脚本验证 DOCX 解析、名称映射、助手输入与六种字符清除通过。既有 golden 加载器直接构造 NodeIn，故原始 DOCX 解析另行验证，不修改公共测试加载器。

Docker / Python 3.12，独立 `aireview-f-test-db`（aireview-db:dev，已核实可用 vector 扩展）：`test_chat.py`、`test_redaction_golden.py`、`test_a03_outbound.py` 共 **268 passed、1 既有警告、exit 0**（409.86 秒）。原始 DOCX 的解析与替换也写入同一新增追问用例，单独复跑 **1 passed、exit 0**（27.61 秒）。

## F-02 疑似遗漏减负

新增角色与英文术语 golden 标注，修复前两个代表性保留用例均失败。实现后中文角色用例通过；英文保留用例仍失败：第 243 行连写表头 `Range Specific Heat Dedusting Oil Grain Stability HGI Strength Size` 中的未知词使既有 capitalized 规则覆盖全段，违背新增 must_keep。已向用户报告，未放宽标注或擅改原样本。

容器内模拟 NER 结果验证：中置信常用词过滤、高置信自动替换保留、含汉字与虚构名称仍列疑似，均通过。Docker 定向 `test_redaction_golden.py` 与 `test_a03_outbound.py`：**227 passed、1 failed、1 既有警告、exit 1**（311.13 秒）；唯一失败为上述 must_keep，A-03 通过。原有三项 must_suspect 仍通过。日志与退出码：`.codex-tmp/v2f/f02-focused.log`、`f02-focused.exit`，保留在本机忽略目录。

标注修订：按用户确认的 V2Q-185 与任务卡 077be48，从 `en_spec_suspects.yaml` 的 must_keep 删除 Grain Stability、Specific Heat。两项在连写表头中被既有 capitalized 规则覆盖属于设计行为；原始 TXT、该表头规则及原有三项 must_suspect 保持不变。剩余新增 must_keep 为 Bulk Density、Supplier Warehouse、Customs Warehouse、Non-Financial Penalty；中文角色标注保留。

有意行为变化：共享英文判断按单词检查常用词 / 小写证据，连字符复合词按组成词检查，首字母大写规则对连字符复合词的判断随之变化；相对 F-01，Non-Financial Penalty、Non-Cash Penalty 不再列疑似。用户已确认保留（任务卡 7db3b00，两条规则一致，口径同 V2Q-182），Non-Financial Penalty 保留在 must_keep。高置信 NER 分支、签约方和出口复检未改。

按确认后的标注复跑 golden 与 A-03：**228 passed、1 既有警告、exit 0**（314.34 秒）；日志 `f02-approved.log`、退出码 `f02-approved.exit`（同一本机忽略目录）。F-02 已完成。

## F-03 多选忽略

新增静态 batch 路由，先验证全部 ID 属于本任务，再逐项复用现有忽略分支，最后统一刷新一次摘要 / 列表并提交。前端保留逐组替换 / 忽略；新增复选框、全选、忽略所选及设计指定的确认文字，提交中锁定操作，取消保留选择，成功刷新后移除已处理选择。超过 200 组时禁用按钮并显示上限说明（V2Q-186，待确认）。

Docker 代表性用例 **1 passed、55 deselected、exit 0**（26.40 秒），覆盖成功及混入他人任务 ID 时整批 404、无变化。前端 `vue-tsc` / build exit 0，既有 chunk 体积提示。新代码无十六进制颜色、v-html、console 或禁用措辞；浏览器四尺寸与状态检查放在 F-05 集中执行。未新增测试函数矩阵。

## 后续交接

用户已提供两份真实样本本机原路径；F-05 使用，不复制入仓库。用户已批准 F-03 增补 SuspectList.vue（任务卡已由用户侧更新，441c73a），原有逐组替换 / 忽略按钮保留。F-05 须独立候选环境、模拟模型、前端 build、整仓挂载全量一次及用户目视复核；完成后才交 Opus F-06，不提前更新版本或标签。
