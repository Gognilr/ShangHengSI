# V3.51 质量修复版计划

状态 **范围已授权，V351-00 已完成技术复核**（2026-11-25）。业务名称为 V3.51 质量修复版，拟发布版本号 `3.5.1`、标签 `v3.5.1`。基线为 `v3.5.0` / `37cf9da9`。问题编号、触发条件和证据见 [问题清单](open_questions.md)，本轮验证见 [V351-00 核验记录](reports/V351-00.md)。

## 〇、范围与规则

- 优先处理账号间录音隔离、备份可恢复性、标书结论时效、批注稿次归属和审核／提问确认链。随后修复合同与单证漏报、导出、翻译和历史状态、管理员的告警与失败恢复。改动放在现有入口内。用户于 2026-11-25 确认 Q-01～18 全部纳入本版，包含唱标价、合同沉淀状态和文件审核 PDF 识别校对。
- 现行安全边界见 `CLAUDE.md` 与 SPEC 第 17 节。每个任务一个会话，在 `main` 上按表串行。改动以各卡列出的文件为限，范围变更先说明。用户于 2026-11-25 授权本轮由 Codex 自审，表中交叉复核停点按自审和实际验收记录。按任务号小步提交，逐路径暂存。落实的规格变更写入短报告，由 V351-Z 合并。
- 后端专项在 Docker Python 3.12 和 `aireview-db`（pgvector）运行，每卡少量代表性用例。前端按澄明规范实走受影响的入口。完整回归只在 V351-Z 运行一次。实测用虚构材料和模拟模型，真实模型或下载另行申请授权。
- 开工首先在当前发布基线和新建数据上复现该卡问题。模拟时序或业务对象时，注明替身边界。修复后以同样输入验收，再核对旧数据所走的兼容分支。只有取得相应证据后才更新任务状态。
- **旧数据验收。** `172.16.11.252` 有旧版本业务数据。本机准备两套隔离环境：空库装当前迁移并新建资料，以及以 `v3.4.0` / `0051` 的虚构有数据副本为基线，升级到 `v3.5.0` / `0056` 后再升级至本版候选。另保留一组在 `v3.5.0` 创建的记录。曾经更早迁入的已支持数据形态，用相关历史夹具补查，按实际字段和状态验收。两套环境各用独立数据库、原件卷和密钥。下列任务的兼容验收不接触真实服务器数据。
- **上线停点。** 候选在带数据的隔离环境升级、恢复均通过后，提交 V351-Z 报告、包校验值和上线方案供确认。现场升级前先核对实际版本和数据状态，完成授权后的应用备份与 PVE 快照并核验可用性。生产上的停止、升级、恢复和推送发布标签须事前确认。

## 任务表

| 编号 | 任务 | 问题 | Claude 侧 | ChatGPT 侧 | 依赖 | 停点 | 状态 |
|-|-|-|-|-|-|-|-|
| V351-00 | 核验 V3.5.0 基线并复核计划 | Q-01～18 | Opus，high | Sol 6.1，high | — | 技术复核 | 已完成（2026-11-25，Codex，[报告](reports/V351-00.md)） |
| V351-A-01 | 浏览器现场录音按账号隔离 | Q-01 | Opus，high | Sol 6.1，high | 00 | 自审和双账号走查 | 已完成（2026-11-25，Codex，[报告](reports/V351-A-01.md)） |
| V351-A-02 | 数据库与原件备份一致、先核验再切换恢复 | Q-02 | Opus，high | Sol 6.1，high | A-01 | 自审和隔离恢复演练 | 已完成（2026-11-25，Codex，[报告](reports/V351-A-02.md)） |
| V351-A-03 | 标书 OCR 与初审结果、Word 覆盖一致 | Q-03、13 | Opus，high | Sol 6.1，high | A-02 | 自审和 Word 目视 | 已完成（2026-11-26，Codex，[报告](reports/V351-A-03.md)） |
| V351-A-04 | 审核批注按轮次选稿与归属 | Q-04 | Opus，medium | Sol 6.1，high | A-03 | Word 目视 | 已完成（2026-11-26，Codex，[报告](reports/V351-A-04.md)） |
| V351-A-05 | 文件审核重开及确认后送审 | Q-17 | Opus，high | Sol 6.1，high | A-04 | 另一侧复核、虚构任务实走 | 待开工 |
| V351-A-06 | 普通对话仅续发明确提交过的提问 | Q-18 | Opus，high | Sol 6.1，high | A-05 | 另一侧复核、模拟外发 | 待开工 |
| V351-B-01 | 一次性合同对比保留跨列与首尾空格差异 | Q-05 | Sonnet，high | Sol 6.1，high | A-06 | — | 待开工 |
| V351-B-02 | 单证同额异币分别保留并核对 | Q-06 | Sonnet，high | Sol 6.1，high | B-01 | — | 待开工 |
| V351-B-03 | 修复按问题讨论的回复导出 | Q-07 | Sonnet，medium | Terra，medium | B-02 | — | 待开工 |
| V351-B-04 | 翻译停止、完成与待确认状态对齐 | Q-08、09 | Sonnet，high | Sol 6.1，medium | B-03 | 确认 Q-09 状态口径 | 待开工 |
| V351-B-05 | 历史筛选只采纳最新请求 | Q-10 | Sonnet，medium | Terra，medium | B-04 | — | 待开工 |
| V351-C-01 | 用实际备份卷容量告警 | Q-11 | Sonnet，medium | Terra，medium | B-05、A-02 | — | 待开工 |
| V351-C-02 | 知识库失败项可原位重新上传 | Q-12 | Sonnet，medium | Terra，medium | C-01 | — | 待开工 |
| V351-D-01 | 标书结果页更正唱标价 | Q-14 | Sonnet，medium | Terra，medium | C-02、A-03 | — | 已纳入，待开工 |
| V351-D-02 | 合同沉淀显示复用状态与重试 | Q-15 | Sonnet，high | Sol 6.1，medium | D-01、C-02 | — | 已纳入，待开工 |
| V351-D-03 | 文件审核确认脱敏页就地校对 PDF 扫描文字 | Q-16 | Opus，high | Sol 6.1，high | D-02 | 另一侧脱敏复核 | 已纳入，待开工 |
| V351-Z | 双基线验收、复核规格、打包和发布 | Q-01～18 | Opus，high | Sol 6.1，high | A～D | 报告交用户，推标签和升级前等确认 | 待开工 |

执行顺序 **00 → A-01～06 → B-01～05 → C-01～02 → D-01～03 → Z**。

## 一、A 批｜数据、隐私与结果正确性

### V351-A-01 浏览器现场录音按账号隔离

- **现状** 浏览器的现场录音元数据和分片共用一个记录空间。换账号后，后一个会议用户仍能读取上一个用户的未上传草稿。
- **做法** 用已登录的稳定用户 ID 隔离元数据和分片。开始录音时捕获归属，追加、恢复、上传回调及清理继续用同一归属。切换账号后仅加载当前账号的草稿。无归属的旧浏览器数据不得被当前账号自动认领或显示主题，处置按 Q-01 确认；同账号的带归属草稿保留刷新恢复能力。
- **文件** `frontend/src/utils/recordingStore.ts`、`frontend/src/components/meeting/MeetingRecorder.vue`、`frontend/src/views/Meeting.vue`。
- **验收** 在干净的浏览器配置中用本版新建两个会议账号和一段虚构短音频。A 刷新可恢复，退出后 B 看不到 A 的主题、音频或恢复入口，B 自己录音和清理不影响 A，A 重新登录后只见自己的草稿。单独放置旧版无归属草稿，确认新账号不能访问。检查无真实音频或主题写入日志。

### V351-A-02 备份一致与恢复预检

- **现状** 备份先快照数据库，再打包原件。其间删除任务可让快照引用已经消失的文件，仍得到成功的备份。恢复要到切换后才查出缺件。
- **做法** 从同份 `db.dump` 获取快照引用的必需文件键。建议在本机用 `pg_restore --data-only --table=task_files` 读取备份中的表数据，复用现有版本读取方式。归档完成后用实际写入的键集合核对，缺必需文件就记录失败并保留旧备份。恢复准备阶段复用该校验，对照已解包原件，在停服及切换库、卷前拒绝缺件包，保留切换后的既有复核。维持现有归档格式，核对旧字段缺省、已过保留期的原件以及录音回放替代和已登记转换件。具体取键口径由另一侧复核。
- **文件** `backend/app/backup/service.py`、`backend/app/backup/restore.py`、共用引用读取模块 `backend/app/backup/references.py`（如需）、`backend/tests/test_backup.py`、`backend/tests/test_backup_restore.py`。如所选做法需调整恢复流程入口，仅限 `backend/app/cli.py`、`deploy/scripts/restore.sh` 和 `docs/ops/运维说明.md` 的对应部分。
- **验收** 用新建的虚构任务截在数据库快照后删除，确认当前备份安全失败、未发布新包、可用旧包仍在。正常备份在隔离环境恢复，核对数据库引用、原件和权限。对现有格式的完整备份与缺件备份各演练一次，缺件在切换前被识别。手工与定时共用业务路径，备份原件和恢复脚本的现场表现以隔离虚拟机演练为准。

### V351-A-03 标书 OCR、结果与 Word 覆盖一致

- **现状** 扫描页写入和检查重算分开提交，末页入库后界面可提前撤下「初步」并停止轮询。初检与末页写入也可能交错。Word 在该窗口可读到此前的结果和覆盖说明。
- **做法** 在检查结果中记录该次实际纳入的 OCR 覆盖，区分扫描、结果重算成功和失败。协调初检发布与后台补算，确保末页写入后有结果更新，初检仍在跑时也能收敛。页面按实际结果版本续刷。暂用 Word 仍可导出，首页标注初步或重算失败并显示适用的覆盖说明。最终结果与 Word 读取同一次已提交的检查，失败时给现有页面内的重试入口。沿用澄明 8.5，历史结果缺覆盖字段时保持可看、可导出，显示已有说明并对未知覆盖如实标注。
- **文件** `backend/app/bid/checks.py`、`backend/app/bid/pipeline.py`、`backend/app/bid/extract.py`、`backend/app/bid/service.py`、`backend/app/bid/results.py`、`backend/app/bid/router.py`、`backend/app/export/bid_docx.py`、`frontend/src/api/bid.ts`、`frontend/src/views/BidProject.vue`、`frontend/src/components/bid/BidResults.vue`、`backend/tests/test_bid_checks.py`、`backend/tests/test_bid_report.py`、`backend/tests/test_bid_model.py`。
- **验收** 虚构项目的最后一页有一条严重问题和原件出处。把后台卡在末页入库与重算之间，核对页面继续显示初步且 Word 标注覆盖。放开后自动出现同一条问题及定位，Word 与页面一致。另验一次补算失败和恢复，以及旧结果无覆盖字段、原件保留期已过时的查看与出件。结论分类按现行规范。

### V351-A-04 批注按当轮文件和主稿归属

- **现状** 审核轮次已保存选中文件顺序，但批注下载可选该会话内的全部可批注文件，无位置审核意见及无归属本地校验项仍用会话主文件分配。
- **做法** 对有 `run.file_ids` 快照的轮次，导出入口及服务端都校验所选文件在本轮范围中，用首项作为该轮主稿。`file_ids IS NULL` 的历史轮次沿用旧主稿取值，保留已有的归属校验和原件已清理提示。
- **文件** `backend/app/review/service.py`、`backend/app/export/router.py`、`backend/app/export/annotated_docx.py`、`frontend/src/api/runs.ts`、`frontend/src/components/result/ExportButton.vue`、`frontend/src/components/workspace/RunResult.vue`、`backend/tests/test_annotated_export.py`。
- **验收** 同一会话两稿、两轮分别选择不同主稿，每轮各有一项无定位的模拟意见。切换轮次时导出菜单、接口和生成的 Word 一致，未参审文件不可出件。对旧 `NULL` 轮次打开旧报告和批注，核对原有归属和内容。

### V351-A-05 文件审核重开及确认后送审

- **现状** 文件审核首页建的是普通工作台会话，首轮前只靠当前地址和页面内存保留审核意图。审核列表和待办靠有无轮次分类，历史、近期、搜索等入口可能漏列或跳到提问。审核视图里的文件抽屉也能打开只保存脱敏的普通弹窗。会话详情的轮次数目前可能与轮次列表不一致。
- **做法** 在服务端保存明确来自文件审核入口的会话意图，会话详情、历史、待办、近期、搜索和审核首页共用审核归类与恢复规则。让审核视图的待确认入口都回到内嵌审核确认页，显示本轮材料、我方、立场、模型及外发预览。审核主按钮为「确认发送并开始审核」，复用带 `confirm_pending` 的事务，受理后显示真实轮次及进度，失败保留可处理的说明。会话详情与列表用同一轮次口径。旧会话有轮次时按审核恢复，无轮次且无可证入口时保留手动进入「全面审核」的能力。视图切换本身只导航。
- **文件** `backend/app/assistant/router.py`、`backend/app/assistant/service.py`、`backend/app/assistant/schemas.py`、`backend/app/tasks/search.py`、`backend/app/db/models.py` 及本卡 Alembic 迁移，`frontend/src/api/assistant.ts`、`frontend/src/views/Review.vue`、`frontend/src/views/Assistant.vue`、`frontend/src/views/History.vue`、`frontend/src/components/assistant/WorkbenchHome.vue`、`frontend/src/components/assistant/FilePanel.vue`、`frontend/src/components/workspace/ReviewPane.vue`、`frontend/src/components/GlobalSearch.vue`、`frontend/src/components/SidebarSessions.vue`、`frontend/src/stores/sessions.ts`、`frontend/src/layouts/MainLayout.vue`、`backend/tests/test_session_review.py`、`backend/tests/test_bid_report.py` 的已有历史和搜索专项。涉及共用接口的增量范围在评审中确认。
- **验收** 在全新库从文件审核上传虚构文件，解析后未发起首轮，从审核首页、历史、近期或标题搜索重开仍进审核。核对仅有审核权限和同时有提问权限的两种账号，从审核视图文件抽屉也到同一确认页。一次确认只创建一个审核轮次，详情和列表一致。模拟预检拦截时轮次未创建、用户可继续处理并重新提交。已单独确认的文件能继续送审，旧报告可查，原文件不需重传。

### V351-A-06 普通对话仅续发明确提交过的提问

- **现状** 工作台首页的上传操作传入当前草稿和模板，创建会话时据此推断已经点击发送。附件确认后，挂起的发送可能自动继续。
- **做法** 创建会话时区分上传材料和明确发送两种用户动作，保留草稿和模板。只有点击发送或在输入框明确提交，才能挂起并等待解析、脱敏确认后续发。仅保存确认时提示「脱敏已保存」并说明下一步。确有待发提问时显示后续动作与取消入口，关闭确认或切到审核视图时撤销自动续发并保留草稿。沿用发送前预览、人工确认疑似项和网关出口复检。
- **文件** `frontend/src/views/Assistant.vue`、`frontend/src/components/assistant/Composer.vue`、`frontend/src/components/assistant/FilePanel.vue`、`frontend/src/components/ConfirmWorkbench.vue`、`frontend/tests/assistant-input.mjs`。
- **验收** 各用一组虚构文本和附件覆盖先写草稿、先选模板后只上传。确认脱敏后草稿和模板仍在，消息发送接口计数为零。明确点发送的一组可按已显示的流程确认、预览并只发送一次。取消待发后保持零发送，真实模型调用为零。

## 二、B 批｜业务与界面闭环

### V351-B-01 一次性合同对比的跨列与空格

- **现状** 对比首先只按文字对齐，相同文字移到同一表格的其他列可能消失于差异清单。Word 通用解析提前裁掉段落及表格单元格的首尾空格。
- **做法** 一次性对比读取有效正文与表格的原始可见空白。对齐后仅在表格关系可以可靠判断时把跨单元格列为位置变化，整体插段或表格前插行产生的顺延不逐条报移位；重复且配对不明按增删呈现。单独打开对比用的读取选项，保持入审解析和增量复审语义。当前页结果沿用现有并排定位。
- **文件** `backend/app/parsing/docx.py`、`backend/app/review/contract_compare.py`、`backend/app/review/drafts.py`、`backend/tests/test_contract_compare.py`。
- **验收** 用虚构 Word 验收同一表格跨列、段首空格各一处，保留既有标点、DOC 转换、两稿相同和不留存用例。加一组前插段落或表格行，避免整批顺移被误报；接口仍不创建任务和外发记录。

### V351-B-02 单证保留同额异币和出处

- **现状** 普通金额项目按数值去重，随后从留下的金额推币种。同一文件的 `USD 10,000` 和 `EUR 10,000` 可变成一条，造成金额与币种都显示一致。
- **做法** 归一与去重保留币种信息，币种项目使用每个实际候选和其位置。多币种候选缺乏足够配对时提示人工核对，缺币种时不自动补币种。保留同币种不同写法等价，以及独立的信用证上限逻辑。处理本机结果与模型仅定位补值共用的口径，金额继续使用 Decimal。
- **文件** `backend/app/assistant/crosscheck.py`、`backend/tests/fixtures/crosscheck/` 内本卡虚构样本、`backend/tests/test_assistant_crosscheck.py`、`backend/tests/test_assistant_templates.py`。
- **验收** 一份单据有等额美元和欧元、另一份只有美元，结果与导出保留两处出处，不能把整组判为一致。既有同币种不同写法和信用证专用规则通过。已有加密结果照旧查看，用户重新核对时按新规则计算。

### V351-B-03 问题讨论回复导出

- **现状** 前端将会话和问题拼成视图标识，并作为导出路径的会话 ID，后端拒绝非法 UUID。
- **做法** 将讨论视图的重置标识与真实会话 ID 分开。Word 和有表格时的 Excel 都走已有会话消息导出接口，原有归属和权限检查继续生效。
- **文件** `frontend/src/views/Assistant.vue`、`frontend/src/components/assistant/Conversation.vue`、`backend/tests/test_assistant_export.py`（使用现有回归）。
- **验收** 虚构一条问题和一条带表格回复，经按问题讨论入口导出 Word 和 Excel，切到另一条问题后再确认来源。普通对话与旧讨论记录的导出仍能读取；非本人请求照常拒绝。

### V351-B-04 翻译进度与工作台待办

- **现状** 翻译页把 `stopped` 当作完成。纯本地翻译保留材料的未确认状态，工作台会误列为等待云端发送。
- **做法** 完成只认 `done`，停止时显示已译段数和部分译稿。已停止的本地译稿导出同样标明部分完成。工作台、历史和菜单依据已知的翻译方式与消息状态展示，对没有可靠方式信息的会话沿用 Q-09 确认的口径。界面状态不写成脱敏已经确认，云端发送依然走原有确认和出口复检。
- **文件** `backend/app/assistant/service.py`、`backend/app/assistant/schemas.py`、`backend/app/assistant/export.py`、`frontend/src/api/assistant.ts`、`frontend/src/views/Translate.vue`、`frontend/src/views/History.vue`、`frontend/src/components/assistant/WorkbenchHome.vue`、`frontend/src/components/assistant/TranslationView.vue`、`backend/tests/test_assistant_local_translation.py`、`backend/tests/test_assistant_export.py`、`backend/tests/test_assistant_history.py`。
- **验收** 5 段虚构文件在第 1 段后停止，回到页面和下载均说明只完成 1/5；完整本地翻译显示完成且不进入确认待办；云端待确认仍能直达确认，确认前不发送。旧翻译消息缺方式或段数时可看、可导出，界面准确表达已知信息。

### V351-B-05 历史列表请求顺序

- **做法** 复用全局搜索的最新请求序号思路。只有最新一次类型、搜索词和页码对应的请求可以更新列表、页码和错误状态，包含翻页回退。
- **文件** `frontend/src/views/History.vue`。
- **验收** 让「文件审核」先发后回、「翻译」后发先回，列表和页数一直匹配当前筛选。正常分页无回退。

## 三、C 批｜管理恢复

### V351-C-01 使用真实备份卷容量

- **做法** 管理员告警与数据页共用 worker 记录的真实备份卷采样和采样时间。旧记录无容量时说明暂未测得。阈值仍取最近一次成功备份大小的两倍。
- **文件** `backend/app/admin/alerts.py`、`backend/app/admin/data.py`、`backend/tests/test_admin_alerts.py`。
- **验收** 在隔离部署中让备份卷容量与 app 容器所在盘明显不同，比较两处数据显示和低空间告警。保留一次最近备份失败的提醒。

### V351-C-02 知识库失败项原位重传

- **做法** 同一文件的失败项允许带着文件原位重新上传，按相同权限和唯一性校验排队。失败项复用记录；就绪和处理中重复提交仍拦住。重新上传沿用现有输入清理和加密存储。
- **文件** `backend/app/kb/service.py`、`backend/app/kb/job.py`、`backend/app/kb/router.py`、`frontend/src/api/kb.ts`、`frontend/src/views/settings/Knowledge.vue`、`backend/tests/test_kb.py`。
- **验收** 新上传虚构文件让首次解析失败，从失败项重新选择同一文件且能成功导入。只留一个业务条目，重复上传成功条目仍提示重复。旧版保留下来的失败条目也可以用同一操作恢复。

## 四、D 批｜已确认纳入的改进

### V351-D-01 结果页补录唱标价

- **做法** 在投标人抽屉复用已检查项目的报价重算接口，按后端现有状态约束开放编辑。只刷新相关报价项，保留模型补查及其他项目的排除状态。费率或下浮率沿现有交互限制。
- **文件** `frontend/src/views/BidProject.vue`、`frontend/src/components/bid/BidderCard.vue`、`backend/tests/test_bid_report.py`（使用已有专项）。
- **验收** 一个已完成本地检查和模拟补查的项目补录一次，页面和 Word 更新报价，模型补查和其他问题仍在，零真实模型调用。

### V351-D-02 合同沉淀的复用状态

- **做法** 继续允许专职人员立即沉淀，单独呈现后续检索准备状态。沿用后台任务和已有的管理权限，显示准备中、可复用或准备失败，失败可重试。现有已就绪的合同条文直接可复用，标书同分类项目继续按创建时快照读取生效条文。
- **文件** `backend/app/content_drafts.py`、`backend/app/standards/job.py`、`backend/app/admin/standards.py`、`frontend/src/api/sharedContent.ts`、`frontend/src/components/settings/SharedContent.vue`、`backend/tests/test_content_drafts.py`。是否需持久化新状态由 Q-15 复核后决定，结构变更另列具体 Alembic 文件和验收。
- **验收** 一条虚构合同条文登记后模拟一次向量准备失败，维护列表显示失败，重试成功后相应文件类型和立场的后续审核可取用。现有就绪条文正常读取，撤下和标书共享保持原权责。

### V351-D-03 文件审核确认脱敏页就地校对 PDF 扫描文字

- **现状** 文件审核只有只读识别稿、结果页原件查看和脱敏调整。PDF 扫描错字没有校对入口，签章附近的高置信错字也可能照常进入识别稿。V3.5.0 的 OpenAPI 和签字行预处理已用断网容器验证，见 Q-16。
- **做法** 按用户 2026-11-25 确认的入口，在现有确认脱敏页点选已有的 OCR 文字块或扫描表格单元格，使用「改识别文字」原位编辑、保存或取消，可在同页看原件对应区域。对照原件更正看得清的文字，有歧义、看不清的签章和没有识别块的区域保留「需看原件」提示。原件字节和原识别值保留，校订稿本机加密，沿用同一证据页码与定位框。范围先限 PDF 中确认为 OCR 的正文节点。
- **重新确认** 校订保存后刷新当前材料版本及确认页节点，重跑受影响的本地校验与脱敏。受影响的旧选区、确认和外发预览失效，用户核对后再按既有流程送审并执行出口复检。写入在任务锁内校验本人、解析与审核状态。首版建议只对首次送审前的确认脱敏页开放，已出结果的资料可以从原件新建审核后进入同一页校对，原轮次继续按原有依据查阅。签字盖章状态由人看原件判断。
- **文件** 候选范围为 `frontend/src/components/ConfirmCanvas.vue`、`frontend/src/components/ConfirmWorkbench.vue`、`frontend/src/components/DocPreview.vue`、`frontend/src/api/preview.ts`、`frontend/src/api/assistant.ts`、`backend/app/tasks/router.py`、`backend/app/tasks/service.py`、`backend/app/assistant/router.py`、`backend/app/tasks/pipeline.py` 和对应专项。加密校订存储、材料版本及相关字段变更在设计确认时列出具体文件与 Alembic 迁移，再进入实现。
- **验收** 一份虚构扫描 PDF 含签章旁看得清的日期或印刷姓名误读，以及一处看不清的签名。校订后原件逐字节一致，预览重新脱敏、确认与出口复检通过，页码和定位不漂移，看不清处只提示人工核验。用新任务和原件仍在的旧审核各核对一次，历史轮次报告保持原有依据。列一个 golden 样本并用 A-03 验证校订文本外发，真实模型调用另行确认。

## 五、V351-Z 阶段复核与发布

- **做法** 核对问题、任务、提交和短报告。按两套数据基线走查主要入口，合并已确认的 SPEC 和版本引用。以最终候选包做升级、断网启动和备份恢复演练，记录包清单与 SHA-256。计划状态列回填日期、执行者及报告。
- **文件** 本计划、`open_questions.md`、`reports/V351-Z.md`、`docs/SPEC.md`、`CLAUDE.md`、登录页版本与三语 README 的版本段落、实际完成的部署验收记录。相关变更由阶段复核确认后合并。
- **验收** 整仓全量运行一次并持久记录实际退出码，前端构建通过，受影响的 Word 和界面走查通过。空库新建流程和带虚构旧资料的升级流程均通过。核对旧账户与权限、原件可读、已有历史/审核轮次和批注、标书结果/导出、会议/翻译与知识库记录；检查备份恢复后数据库引用与原件对应。若候选未达验收条件，报告保留差异并等待处置结论。发布标签和正式升级待用户确认。
