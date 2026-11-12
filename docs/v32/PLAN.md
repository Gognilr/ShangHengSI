# V3.2 计划：智能助手与智能审核统一工作台

状态：**用户 2026-11-12 确认**；任务由 Sonnet 与 Opus 完成（用户 2026-11-12 指派）。分工：安全与回程关键的后端（A-02、B-03）及复核、收尾由 Opus；其余由 Sonnet，停点交 Opus 复核。

依据：
* 设计：[docs/v312/unified-workspace-implementation.md](../v312/unified-workspace-implementation.md)。第 11 节为准；第 1～9 节中与第 11 节冲突的数据设计（Workspace 表、版本复制、封存、新 kind）作废。
* 决定：用户 2026-11-12 的八项决定（同文件 11.5）。
* 前置：V311-Z 完成（用户 2026-11-12 决定只做复核、SPEC 合并与全量测试，不打标签、不构建发布包、不升级服务器）。
* V3.1.2 不单独发布（用户 2026-11-12）：其四项缺陷修复并入本阶段，作为第一个任务 V32-A-00，任务内容按 [docs/v312/PLAN.md](../v312/PLAN.md) 第一节执行。

## 〇、范围与规则

**目标流程：** 在同一个会话里上传材料、确认脱敏、直接提问、发起全面审核，针对某个问题继续讨论，把讨论稿保存为该问题的修改建议，最后采纳并导出。文件只上传一次、只确认一次，两种模式共用同一套代号。

**设计要点（第 11 节）：**
* 工作空间就是现有会话（Task kind=assistant，purpose=chat）。不建新表承载工作空间，不加新 kind，不复制材料。
* 文件解析后不可变；文件序号只增；被审核轮次引用的文件不能单独移除。
* 对照表只增；A-01 的原地补替换保留。
* 日期按全局开关在会话首次脱敏时决定是否平移，之后固定；旧会话不平移。
* 会话里不分配 [[OUR]]，我方只作为主体说明里的角色。
* 会议纪要、翻译的存储与行为不变。旧审核任务用原页面打开，不转换。

**不做：** 在线编辑原件、多人协作、通用 Agent、模型更换、旧审核任务转工作空间（第 11.3 节 O5）。

**规则：**
* 沿用 V3.1.1 计划第〇节的执行规则：main 上串行，提交说明以任务编号开头，小步提交。
* 测试只写第五节的代表性场景与 golden 样本；脱敏、还原相关任务结束时跑 golden、A-03 与回程样本测试；整仓全量只在 V32-Z 跑一次。
* 172.16.11.252 在用户宣布正式启用前可以直接部署和测试。
* 新问题记在 docs/v32/open_questions.md（编号 V32Q-01 起）。报告放 docs/v32/reports/，每份不超过一页，末尾写建议的 SPEC 改动，由 V32-Z 合并。

| 编号 | 任务 | 模型 | 强度 | 依赖 | 停点 | 状态 |
|-|-|-|-|-|-|-|
| V32-00 | 本计划 | Opus | high | 第 11 节决定 | 用户确认 | 已完成（2026-11-12 用户确认） |
| V32-A-00 | 四项缺陷修复：V312Q-01～04（按 docs/v312/PLAN.md 第一节） | Sonnet | high | V311-Z | — | 已完成（2026-11-12，报告 docs/v32/reports/V32-A-00.md） |
| V32-A-01 | 数据与文件不变式 | Sonnet | high | A-00 | — | 已完成（2026-11-12，报告 docs/v32/reports/V32-A-01.md） |
| V32-A-02 | 会话发起审核（后端） | Opus | high | A-01 | Opus 新会话安全复核（须在 B-03 开工前完成） | 已完成（2026-11-12，报告 docs/v32/reports/V32-A-02.md）；安全复核已于 B-03 开工前完成（见 B-03 报告） |
| V32-A-03 | 中性代号审核对照评测（真实模型） | Sonnet | high | A-02 | 用户录入密钥；结果交 Opus | 完成，达标（报告 reports/V32-A-03.md） |
| V32-B-01 | 工作台界面与侧栏 | Sonnet | high | A-03 达标 | Opus 评审，用户看界面 | 已完成（2026-11-12，报告 docs/v32/reports/V32-B-01.md），待 Opus 评审与用户看界面 |
| V32-B-02 | 按问题讨论 | Sonnet | high | B-01 | — | 已完成（2026-11-12，报告 docs/v32/reports/V32-B-02.md） |
| V32-B-03 | 讨论稿保存、采纳与导出 | Opus | high | B-02 | 回程复核 | 已完成（2026-11-12，报告 docs/v32/reports/V32-B-03.md） |
| V32-Z | 复核、SPEC、版本号、全量、发布包、服务器 | Opus（新会话） | high | B-03 | 推送标签前问用户 | 待执行 |

## 一、V32-A-01：数据与文件不变式

**文件范围：** db/models.py、新 Alembic 迁移（0040）、assistant/service.py、tasks/service.py、redaction/engine.py（只改 material_version 递增点）。

**迁移（只加列，不转换数据）：**

| 表 | 新列 |
|-|-|
| tasks | material_version（int，默认 0）、last_file_seq（int，默认 0，回填为现有最大 seq） |
| review_runs | material_version（int，可空）、file_ids（jsonb，可空；旧轮次为空，表示“全部文件”） |
| assistant_messages | material_version（int，可空）、finding_id（uuid，可空，外键 findings.id，ON DELETE SET NULL） |
| findings | discussed_text（text）、discussed_message_id（bigint）、discussed_at（timestamptz），均可空 |

**行为：**
1. 文件序号取 `last_file_seq + 1` 并写回，不再取剩余文件的最大值，两处上传入口都改。
2. 会话的 remove_file：文件若被任一 review_runs.file_ids 引用（file_ids 为空时视为引用全部文件），返回 409 FILE_IN_USE（“该文件已用于审核，不能移除；可在发送时不选它”）。未被引用的文件照旧移除。
3. material_version 在影响外发正文的提交处 +1：加文件确认、替换/保留决定、手动补充、A-01 原地补替换、我方变更、补充说明修改。用户消息与审核轮次创建时写入当前值。
4. 消息发送与审核创建接受 expected_material_version；不一致返回 409 MATERIAL_CHANGED，前端重新预览。旧客户端不传时不校验。

**用例：** 移除最后一个文件后再上传，序号不复用；被引用文件移除返回 409；预览后材料变化，发送返回 409。迁移一致性测试通过。

## 二、V32-A-02：会话发起审核（后端）

**文件范围：** review/service.py、review/router.py、review/job.py、redaction/engine.py、redaction/outbound.py、redaction/leaks.py、standards/compare.py、tasks/pipeline.py、assistant/service.py、assistant/prompt.py、assistant/history.py、backup/retention.py、security/deps.py。

1. **入口：** 新增 POST /assistant/sessions/{id}/runs，复用 create_run 的检查与入队，要求 use.review，只接受 purpose=chat 的会话。
   * 请求带 stance、source、model_id、user_note、file_ids（默认全部已确认文件）、expected_material_version。
   * 会话没有 stance 时由本次请求写入。改变立场只影响主体说明里的角色文字，不改代号。
2. **状态：** 会话的 task.status 保持 active / awaiting_confirm，不进入 reviewing / reviewed。“审核进行中”由该会话存在 queued 或 running 的 ReviewRun 判断。审核进行中发送消息返回 409 SESSION_BUSY（“审核进行中，完成后再发送”）；反过来，消息生成中也不能发起审核。
3. **外发文本：** build_outbound 改为按本次操作选择（mode='review' 或 'assist'），不再按 kind 判断。
   * 审核模式对会话也执行报价汇总、比率与本地校验说明，只取 file_ids 中的文件。
   * 读取轮次结果的 _View、导出与 GET /runs 接口，对会话轮次同样适用。owned_run 已按 owner 校验，确认对 kind=assistant 也通过。
4. **解析附加步骤：** 会话文件解析时也识别 doc_type，跑 run_checks 本地校验并写条款向量，与审核任务一致。assistant/history 的历史条款检索把会话里审核过的文件也计入。
5. **我方与主体说明：** 会话的主体说明写成“[[ORG_1]]：我方，卖方；[[ORG_2]]：阿联酋，贸易商”。会话不再分配 [[OUR]]（NeutralAllocator.our 对新会话返回该主体的 ORG 代号）；旧会话已有的 [[OUR]] 照常还原。leaks、standards/compare 对中性主体的判断同步修改。审核仍要求恰好一个我方。
6. **日期平移：** engine._run 对 purpose=chat 的会话，只在对照表为空时（首次脱敏）按全局开关决定并写入 @date_shift_days，之后沿用。旧会话对照表非空且没有该键，永不平移。
   * 用户消息沿用追问已有的日期平移。
   * 助手提示词在平移的会话里加一句与审核相同的平移说明。
   * 模型推算出的新日期按现有 date 标记标出。
7. **保留期：** 发起过审核的会话，按原件层与结果层计算（以最后活动时间为准）。原件层到期后禁止审核与讨论，与审核任务一致。
8. **权限：** 只有 use.review 的用户可以在会话里上传、确认、审核；普通提问仍要求 use.assistant（ensure_cloud_send 不变）。

**用例（代表性）：** 同一会话先问答、再审核，两者外发正文中同一实体代号相同；审核前后只做一次文件确认。local_only 会话发起审核走本地校验，零外发。会话审核轮次的问题定位、本地算式和中性代号还原正确（各写进回程样本一例）。日期：新会话平移，旧会话不平移。跑 golden、A-03、回程样本测试。

**停点：** 完成后交 Opus 做安全复核（出口复检未放宽、代号只增、日期平移一致、权限）。

## 三、V32-A-03：中性代号审核对照评测

* 用 backend/tools/eval_review.py，口径同 V31-E-07：同一批虚构样本，分别以审核任务（现行代号风格）与会话审核（中性代号）各跑若干轮。比较问题召回、结论一致率、偷懒率、日期关系类问题召回。
* 模型用系统当前的审核默认模型。开工前停下，等用户在场录入密钥；密钥不写入仓库和日志。
* **达标线：** 召回与结论一致率不低于现行风格（允许 ±1 个样本的波动）。
* **不达标：** 只改提示词与主体说明后重测一次；仍不达标就停下报告，由用户与 Opus 决定，不退回双代号风格。
* 报告 docs/v32/reports/V32-A-03.md：数字表、样本说明、结论。

## 四、V32-B：界面与讨论

### V32-B-01 工作台界面与侧栏

**文件范围：** router/index.ts、MainLayout 与侧栏组件、views/Assistant.vue、views/History.vue、views/ReviewNew.vue（只改路由，不删文件）、components/assistant/*、components/result/*（复用；可加属性注入数据加载函数，不改结构）、frontend/src/api 中会话、轮次与历史的接口、Login.vue 不动。后端只限下列读取入口（2026-11-12 Opus 开工前评审补入）：security/deps.py、redaction/router.py、assistant/router.py、assistant/service.py（历史合并列表）、tasks/service.py（复用列表行构造）。

1. **侧栏（用户 2026-11-12 确认）：** “智能助手”改名“工作台”；“智能审核”保留，作为直达工作台审核设置的快捷入口，下一版本再评估是否去掉；“审核历史”改名“历史记录”，同时列出会话与旧审核任务。会议纪要、翻译不变。
   * 快捷入口只跳到新建工作台视图并预选“全面审核”，**不在点击时建会话**；首次加文件或发送时才创建（设计 3.1），避免近期列表出现空会话。
   * 旧路由 /review/new 重定向到同一视图（带原 query 无意义，直接丢弃）。ReviewNew.vue 暂留，随快捷入口去留一并决定。
2. **工作台：** 顶部为标题、材料按钮、“直接提问 / 全面审核”切换。切换只改视图，不外发、不清空输入。
   * 全面审核首次进入显示设置（主文件、立场、来源、模型、补充说明），主按钮“确认发送并开始审核”。确认页只列本轮新增发送的内容（补充说明、主体说明），已确认文件不再要求核对（第 11 节 R4）；数据取 `GET /assistant/sessions/{id}/outbound?mode=review`，不展示全文。
   * 审核结果复用现有结果页的左原文、右问题清单组件。轮次、导出、问题处理按 run id / finding id 读写（/runs/{rid}…、PATCH /findings/{fid}），已可用于会话轮次，不改。
   * **结果画布的读取入口（后端补）：** `ResultCanvas` 现在读 `GET /tasks/{id}/redaction`，其依赖 owned_redaction_task 对已确认（active）的会话返回 409；节点与原件走 `/tasks/{id}/files/...`，只认 kind=review。做法：GET /redaction（只读）对本人的 purpose=chat 会话在任何状态都放行，修改类接口的限制不变；`ResultCanvas` 加属性把会话的 `/assistant/sessions/{id}/files/{fid}/nodes|raw` 加载函数传给 `ConfirmCanvas.nodeLoader`（及原件视图）。
   * **新增错误码的界面处理：** 发送与发起审核都带 expected_material_version；409 MATERIAL_CHANGED → 重新取预览后再确认；SessionDetail.review_running 为真或收到 409 SESSION_BUSY → 锁定输入与材料操作，显示“审核进行中”，轮询轮次状态直到结束；409 FILE_IN_USE → 提示“该文件已用于审核，不能移除；可在发送时不选它”。
3. **旧数据与历史记录：** 旧审核任务在历史记录中点击后打开原结果页（/review/:taskId），旧链接全部可用。
   * 历史记录用**后端合并列表**（一个分页接口，按最近活动倒序，行带 kind 区分“会话 / 审核任务”，沿用现有搜索与日期筛选；会议纪要、翻译不在其中，保持各自页面）。不在前端合并两个分页列表（翻页与筛选会错乱）。接口与 owner 校验同现有列表。
4. **权限：** 没有 use.review 的用户看不到“全面审核”切换。只有 use.review 的用户，在没有锚定问题时提问框禁用并说明原因。
   * 路由守卫：/assistant 与 /assistant/:sessionId 改为持有 use.assistant 或 use.review 任一即可进入；homeFor 的回退顺序改为“任一即进工作台”；侧栏“工作台”菜单同样按任一权限显示。
5. 按 frontend.md 第十一节验收；用浏览器在 http://localhost:5173 走一遍主线并截图。

**停点：** 完成后交 Opus 评审界面，用户看过后再继续。

### V32-B-02 按问题讨论

**文件范围：** assistant/service.py、assistant/prompt.py、assistant/job.py、新增 assistant/context.py（上下文构建）、对应前端组件。

1. 问题卡加“继续讨论”：切换到提问视图，显示锚点卡（问题标题、所属轮次），可以解除锚点。消息带 finding_id 发送，服务端校验 finding 属于本会话。
2. **上下文：** 该问题的模型结论（代号文本）、finding.locs 对应节点及前后各两段、同一条款号的节点、该锚点下最近的消息。普通对话与各问题的历史互不混入。总量按现有字符预算估算，超限返回 CONTEXT_TOO_LONG，不截掉问题条款。
3. 问题所属文件已被“不再选用”时仍可讨论（文件未删）。原件层到期后禁止讨论。
4. **用例：** 后部条款的问题讨论确实包含该条款；问题 A、B 的历史不混入；跨会话的 finding_id 返回 404。

### V32-B-03 讨论稿保存、采纳与导出

**文件范围：** review/service.py（finding 更新）、新接口 PUT /findings/{fid}/draft、export/revised_docx.py、export/annotated_docx.py、export/report_docx.py、结果页组件。

1. 讨论回复下方显示“保存到该问题的修改建议”，打开预览，显示从回复中提取的建议条文（要求模型把条文放在固定标记内，提示词加一句）。用户可以编辑；编辑后的文字先在本地脱敏，再以代号文本存入 discussed_text。没有有效条文时提示重新生成或手工整理。
2. 问题卡显示“原审核建议 / 讨论修改稿”二选一。采纳动作沿用现有逻辑，只是记录所选来源。原 suggested_text 不变。
3. 导出时修订版取所选文本，与 suggested_text 走同一条还原和 clause=True 校验路径；不合格的降为批注。
4. **用例：** 完整主线一例（上传 → 确认 → 问答 → 审核 → 讨论 → 保存讨论稿 → 采纳 → 导出修订版），文件只上传一次、切换模式零模型调用；含未知代号的讨论稿降为批注。跑回程样本测试。

## 五、V32-Z

1. **Opus 复核**（新会话；Opus 额度不足时由 Sonnet 新会话执行）：安全不变量（出口复检、代号只增、日期一致、owner 校验、local_only 零外发）、旧数据兼容（旧审核、旧会话、会议、翻译各打开一次）。
2. 合并各报告的 SPEC 建议：1.6 / 6.9 侧栏、7.3 / 7.4、9.2 代号、9.3 第 7A 步、13A、17.8 与保留期、18 新列、19 新接口；头部修订记录加 3.2。
3. 版本号 3.2.0：SPEC、CLAUDE.md、AGENTS.md、Login.vue；“当前阶段”改为“V3.2 已发布，下一阶段待定”。
4. 整仓全量测试与前端构建；在仓库外的 clone 中构建 3.2.0 发布包；打 `v3.2.0` 标签（推送前问用户）。
5. 升级 172.16.11.252（用户宣布正式启用前直接执行），升级后用虚构文件走一次完整主线。记录写入 docs/acceptance/V3.2升级记录.md。
6. 报告 docs/v32/reports/V32-Z.md。

## 六、后续再定

* B-01 的“智能审核”快捷入口在 V3.2 之后是否去掉，留到 V3.2 使用后再定。
