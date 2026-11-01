# V31-S 测试记录

## V31-S-01（2026-11-02）

- 新增 jieba 常用词 4,610 条、THUOCL 法律财经词 9,926 条，均为数据文件，无新增依赖或运行时下载。固定标签 / 提交、原件与产物 SHA-256、许可全文见 `backend/resources/zh_words.README.md`、`zh_legal_finance.README.md`；标准库生成脚本重生成的 4 个文件逐字节一致。无需 golden 剔除词。
- 中文词表按设计分别过滤 NER 与疑似；常用词不用于企业后缀、签约方或规则替换。标准号、化学式、贸易术语、行业参数先遮盖再查疑似，中置信 NER 同样过滤；Word 第一行与 XLSX 各工作表首个含至少两个非空单元格的行仅过滤 capitalized / ner 疑似。法律财经词表变更计入 NER 缓存版本。
- 新增 6 份虚构 `uncertain_*` 样本（含 Word / XLSX 表头、独占标题、多工作表、表头人名与长串数字），共 31 条 `must_suspect`、16 条 `must_replace`、60 条 `plain_uncertain`。后者只校验标注存在，用于统计与 S-06。首轮新增样本误将高置信 NER 已自动替换的机构名标为 must_suspect，出现 6 个失败；核对后将 16 个机构名归入 must_replace，补足叙述人名覆盖，未降低真名称断言。首轮在 254 通过、6 失败时中止，容器 `aireview-v31s01-golden` 保留输出。
- 对相同的最终 51 份样本，用任务前 HEAD 四个识别源文件与修改后源文件分别计算，每份样本按规范形式去重后求和：不确定项 **129 → 110 组（减少 19，14.73%）**；其中原有 45 份 **55 → 53**，新增 6 份 **74 → 57**。`plain_uncertain` 被列出 **26 / 60（43.33%）**，前后相同。按样本标注计数，不是按全文出现次数；不能据此推算真实文件效果。
- 既有 `test_suspects.py` 的 golden 检查改用 compute 结果，以纳入表头位置与中置信 NER；golden 加载在同一测试进程内缓存纯计算结果，避免每项断言重复本地模型推理。所有断言保留。
- 七文件相关回归：493 通过、3 失败、3 跳过。3 失败为标准号旧预期 1 条和单位遮盖引起的回归 2 条：按设计将 `GB 12345678` 从“仍须列出”移到“标准号排除”（标准号已不要求年份）；修正长串数字的数量单位判断，让它同 HS 前缀一样读取原始上下文，MT / kg 被遮盖后也不误列数量。输出留在容器 `aireview-v31s01-final`。
- 最终受影响四文件复跑 **413 通过、0 失败**（246.18 秒）：`tests/test_redaction_golden.py tests/test_a03_outbound.py tests/test_restore.py tests/test_suspects.py`；其余三个文件 `test_person_suspects.py test_ner.py test_ner_node_cache.py` 此前 **83 通过、3 跳过**，未改动且未重复运行。两次合计最终相关覆盖 **496 通过、3 跳过**，不是整仓全量。3 个跳过是更换模型时才运行的对拍 / 留出集 / 参考质量评测。最终输出留在 `aireview-v31s01-recheck`，`git diff --check` 通过。
- 测试使用 Docker Python 3.12、`aireview-backend-test` 与 `aireview-v3d-test-db`（`aireview-db:dev`，aireview-test 网络）；没有使用缺 pgvector 的旧 `aireview-test-db`，也未改动它。
- 未跑整仓全量（留 S-07）、未调用真实模型、未接触正式服务器。无新增待确认实现决定；`plain_terms.txt` 为开发方起草，仍待业务人员审阅（设计 1.3）。

## V31-S-02（2026-11-02）

- 不确定项在代号分配前全文默认替换；NAME / TERM 与中性 NAME / PROPN、REFNO 按设计分配。保留原文只作用于不确定项，reset 单条 / 批量生效；视图补 category / placeholder / kept_by 并按文档顺序。pending 不再阻止审核与助手确认，默认项不进已记住的名称。迁移 0027、提示词 2026.11-6 / assistant-2026.11-3。
- V31Q-16：旧确认任务中未覆盖的 pending 在确认时先补算，随后照常校验我方与出口。正常新任务不重复计算。
- 首轮七文件 360 通过、8 失败，golden / A-03 / 回程 / 迁移 / 助手会话通过；失败均为已改变行为的旧预期。扩展八文件 206 通过、2 失败；最后对提示词、具体名称修改类别、keep / reset / pending 发送、旧 pending 与助手闸门定向 16 通过。容器依次为 aireview-v31s02-first / api / fix，保留输出。未重复全量。
- 旧测试调整：不再断言 SUSPECTS_PENDING；疑似按文档顺序且携带新字段；已有默认替换的名称通过疑似接口改类别；提示词常量与版本同步设计（SPEC 留 S-08 合并）。追问 CHAT_SUSPECTS_PENDING 保持原样。

## V31-S-03（2026-11-02）

- 已实现加密 keep_terms 与迁移 0028、个人 / 两人共享 / 管理员认可生效范围、确认后写入、解析命中计数、敏感词优先与替换时删除本人行、业务管理员查询 / 认可 / 删除。主密钥轮换按 `_enc` 元数据自动覆盖此表。V31Q-17 记录同词多行时每次命中只计一次的口径。
- 四个代表用例、模型与迁移一致、golden / A-03 / 回程合计 277 通过，容器 aireview-v31s03-first。额外助手检查复现真实冲突：保留原文后，固定历史映射引起 MAP@7，确认 409；容器 aireview-v31s03-assistant2 保留输出（前一次用例误写上传状态 202，已改为现有契约的 200）。
- 用户按六项条件确认，已记为 V31Q-20（2026-11-02）：当前出口现算整串相等的保留豁免，所有当前替换、规则与敏感词仍复检，审核与助手共用同一函数；加密历史映射与还原不变。包含关系不豁免，留 V31Q-19。
- 助手复现改入 uncertain_quotation.yaml 的 history_keep 标注及现有 A-03，移除独立助手测试；仅另加一条“另一处仍替换则 MAP 拦截”的反例。最终 keep_terms / golden / A-03 / 回程四文件 **277 通过**（233.23 秒），容器 aireview-v31s03-current-outbound，未增加其他测试。`git diff --check` 通过。
