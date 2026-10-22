# 开发任务清单

本文件是全部开发任务的唯一清单，按 SPEC 第 22 节的里程碑 M1–M8 拆分。

**执行方式：串行。** 同一时间只有一个会话在开发。任务严格按编号顺序执行（编号顺序已满足依赖关系，并尽量让相同模型的任务排在一起，减少换会话）。

## 当前进度

- M7-09 已完成（2026-10-22，Opus 5.5）：按规则 10 评审 M7-02～M7-08。新增 tests/test_m7_acceptance.py（A-06 补充：管理员调用全部 /admin/* 读取接口不出现他人任务对照表中的任何真实值，外发记录详情出口复检零命中，已记住的名称是唯一出现名称明文处；管理员访问他人任务、轮次、问题、对话、报告一律 404）。Docker 全量 1601 passed（test_forbidden_words 需整仓挂载，见 Q-116）。compose + 模拟模型浏览器验收：T-14（新增 CO-1 → 抽屉显示 → 确认发送 → 逐条结论有 CO-1 → 轮次 company_req_version=1、报告写“公司补充要求 第 1 版”）；T-16（立即备份 → .bak + .sha256、sha256sum -c OK、decrypt-backup 后 pg_restore --list 成功、保留份数 1 时只留最新一份）；T-18（只回拨验收任务的完成时间后执行清理 → 原件与节点原文清空、右栏提示、报告可导出、无重新审核、追问禁用）；5 个标签页 1440 / 1280、非管理员被拦截（设置页重定向、管理接口 403）、三种提醒条。修复 MainLayout 外层滚动导致的 1280px 横向滚动条（Q-116）。M7 验收结果与 Q-116 已经用户确认（2026-10-22）。

- M7-02～M7-08 已完成（2026-10-22，Sonnet 5.5，连续执行）：账号（app/admin/users.py）、模型（model.py）、审核要求与 CO 序列迁移 0002（requirements.py）、脱敏（redaction.py）、原件保留期清理（backup/retention.py，不外发任务写 last_completed_at，原件删除后禁止重新审核和追问）、数据页（data.py）、管理员提醒（alerts.py，/auth/me 返回 code + message）；前端 api/admin.ts 及 Users / Model / Requirements / Redaction / Data 五个页面；新增 tests/test_admin_*.py、test_retention.py、test_audit.py。Docker 全量 1598 passed、1 warning；`vue-tsc --noEmit` 通过。**未做**：各前端页面的浏览器逐项检查（1440 / 1280 宽度、各状态）未在本次执行，留给 M7-09 的浏览器检查。新增 Q-115（待确认，含对范围外文件的最小改动：api/requirements.ts、tasks/service.py 序列化一个字段、删除 test_runs_api.py 两条随 Q-113 失效的旧用例）。下一个任务：**M7-09**（Opus 档，medium，M7 联调与验收），停止等待用户安排。

- M7-01 已完成（2026-10-22，Opus 5.5）：app/backup/archive.py（文件头 32 字节、1 MiB 分块 AES-256-GCM、scrypt n=2^15，AAD 绑定文件头 + 块序号 + 末块标志）、service.py（advisory lock、running 记录、pg_dump 经环境变量传连接参数、tar 流式加密写 .tmp → fsync → .sha256 → 重命名 → 目录 fsync → 轮换、失败清理并记 failed、审计 backup_run）、job.py（backup 处理函数、enqueue_backup 忙时返回 None、每分钟定时检查当天一次）、jobs.finalize_failed 的 backup 分支、worker 导入、cli decrypt-backup。新增 tests/test_backup_archive.py（22 项）、tests/test_backup.py（含 T-16、sha256sum -c、pg_restore --list 与恢复到新库）；Docker 全量 1379 passed、1 warning。新增 Q-114（待确认）。下一个任务：**M7-02**（Sonnet 档，medium），模型不同，须换会话；M7-02～M7-08 均为 Sonnet 档，可由 GPT-6.1 Sol 连续执行

- M7 已细化（M7-00，2026-10-22，Opus 5.5），设计见 docs/design/M7.md，新增 Q-99～Q-113；M7 细化与 Q-99～Q-113 已经用户确认（2026-10-22）。规则 10：adbabe1 之后只有 Opus 的 M6-07 / M6-09 提交，无待评审的 Sonnet、Haiku、GPT-6.1 Sol 提交。要点：备份文件格式与解密命令在 M7-01 一次定好，M8-01 的 restore.sh 只调用该命令；唯一的表结构变更是 CO 编号序列（M7-04，迁移 0002）；M7-04 强度由 medium 调为 high（含迁移和 T-14 全链路）；M7-06 补上不外发任务的 last\_completed\_at 和“原件删除后不能重新审核、追问”的后端限制。M7-02～M7-08 为 Sonnet 档，可由 GPT-6.1 Sol 连续执行。下一个任务：**M7-01**（Opus 档，xhigh）

- M6-09 已完成（2026-10-22，Opus 5.5）：用户确认 Q-97 ③④ 后开工；按规则 10 评审 M6-08（c36cef1、adbabe1），未发现问题。新增 tests/test_m6_acceptance.py：A-01 在全部样本识别出的 14 张报价表上独立 Decimal 逐行重算（20 行、10 个合计，2 错行 + 1 错合计全部报出、零误报，2 个无法识别行列为未核验）；A-03 回归补本地校验样本 15 份（真实接口 + 模拟网关，出口复检零命中、本地校验金额零外发）。T-04、T-05、T-15、T-19 既有用例通过。验收中按 SPEC 11.3 把问题类别名称改为“表述 / 合规与制裁”（labels.ts、report_docx.py，bd749f5）。浏览器（compose 按当前代码重建 + 模拟模型，虚构账号 m609qa）：第二步本地校验面板、界面确认发送、结果页本地校验标签与点击定位、追问弹窗（[公司_Q1]、[电话_Q2]）与还原、外发记录核对、两种报告导出、1280 无横向溢出，均通过。Docker 最终全量 1337 passed、1 warning，exit 0。详见 docs/acceptance/M6-09.md。M6 已验收并经用户确认（2026-10-22）；Q-91～Q-96、Q-98 已确认。下一个任务：**M7-00**（Opus 档，high，里程碑细化），停止等待用户安排。

- M6-08 已完成（2026-10-22，GPT-6.1 Sol）：ChatPanel.vue、api/chat.ts 与结果页挂载；右侧 480px 追问抽屉、服务端脱敏预览确认、任务级“不再提示”、Ctrl+Enter、等待禁用和失败保留问题供手动重试。前端构建 exit 0；浏览器用独立数据库、虚构账号、真实接口 + 网关模拟回复核对 T-15 弹窗与还原、取消、偏好刷新及任务隔离、失败仅一次调用且不写对话、手动重试、加载失败、不可用原因、纯文本显示，1440 / 1280 无横向溢出，抽屉均为 480px。服务就绪后功能检查无新增 JS 控制台错误（准备阶段 auth/me 502 已解决，原始日志保留）。Docker 最终全量 1332 passed、1 warning，exit 0；因执行期间出现其他会话的后端 WIP，最终测试使用已提交 63ff11e 后端快照 + 本次前端只读挂载，不包含或验证他人的后端 WIP。新增 Q-98（待确认）。凭据位于本机临时目录 aireview-m608-qa（verification.md、full-final.log、截图与控制台记录）；实现提交 c36cef1。下一个任务：**M6-09**（Opus 档，medium，M6 联调与验收），停止，交 Opus 评审与验收。

- M6-07 已完成（2026-10-22，Opus 5.5）：app/chat/service.py、router.py（GET /tasks/{id}/chat、POST …/chat/preview、POST …/chat）；问题在服务端按对照表 → 已记住名称与自定义敏感词 → 9.3 第 4～7 步脱敏，发送时重新计算、不接受前端结果；网关 call_model 新增 timeout_s、retry 两个关键字参数（默认行为不变），追问固定 120 秒、不重试；失败不写对话；system 消息为模板第 1–7 条 + 自然语言回答（prompt.chat_system_prompt）。新增 tests/test_chat.py（T-15、通用代号、消息结构与最近 10 条、截断、可用条件、长度、CSRF、HTTP 500 / 超时 / 拦截 / 上限、A-06、日志安全）与网关两项测试；Docker 全量 1332 passed、1 warning。新增 Q-97（待确认）。下一个任务：**M6-08**（Sonnet 档，medium），模型不同，须换会话

- M6-05 补充修复已完成（2026-10-20，GPT-6.1 Sol）：用户明确批准最小扩围 ReviewNew.vue；仅增加 6 行页面局部样式，沿用结果页的定高与区域内纵向滚动。1280px 修复前根页面 clientWidth=1265 / scrollWidth=1280，修复后两者均为 1280；1440px 两者均为 1440，body 最小宽度仍为 1280。第一步空表单与第二步展开面板、底部确认按钮均检查，两个宽度截图已核对；构建通过，Docker 全量 1306 passed、1 warning。修复提交 4d79604；凭据 scroll-before.json、scroll-after.json、scroll-fixed-1280.png、scroll-fixed-1440.png、scroll-full.log 位于本机临时目录 aireview-m6-qa。无新增业务决定或 open_questions；缩放阶段 ResizeObserver 开发期错误仍有出现，保留 scroll-vite.log 交 Opus 复核。下一任务仍为 **M6-07**（Opus 档，high），停止等待对应会话。

- M6-06 已完成（2026-10-20，GPT-6.1 Sol）：快照型 Word 报告、任务/轮次下载接口、report_export 审计、响应头文件名、结果页与历史导出；14 项报告定向测试通过，清空节点原文仍可导出且 SQL 不读取 text_enc。浏览器验证本地与 AI 下载、结果页选旧轮次导出、历史取最新轮次、409 后恢复；构建通过。Word 实际渲染并逐页核对 AI 2 页、本地 1 页；Docker 全量 1306 passed、1 warning。新增 Q-96（待确认）。M6-01～M6-06 范围内功能已提交；额外发现第二步在 1280px 窗口下既有纵向滚动布局产生约 15px 横向滚动，ReviewNew.vue 最小扩围随后已获用户批准并补修，见顶部 M6-05 补充记录。停止 Vite 时补取到缩放阶段的两条 ResizeObserver loop completed with undelivered notifications 开发期错误记录（18:09:55、18:13:11）；业务流程断言通过，但控制台仍需 Opus 复核这两条，详见临时 browser-diagnostics.txt。测试凭据和虚构报告在本机临时目录 aireview-m6-qa。下一任务 **M6-07**（Opus 档，high）：停止，开工前须复审自 3980acb 起的待评审提交（含 M5 遗留）。本会话没有替代 Opus 独立评审，也没有完成 M6 里程碑验收。

- M6-05 已完成（2026-10-20，GPT-6.1 Sol）：第二步默认收起的计数面板、AI 结果页本地校验标签页、不外发结果表格及点击定位；构建通过，1440 / 1280 浏览器检查正常、加载中、空列表、模拟 503 错误，正常流程控制台无报错；截图已核对（保存在 Tabbit 本次任务 artifacts）。Docker 全量 1292 passed、1 warning。新增 Q-95（列表计数预读，待确认）。下一任务 **M6-06**（Sonnet 档，medium），按用户授权连续完成后停在 M6-07 Opus 边界。

- M6-04 已完成（2026-10-20，GPT-6.1 Sol）：完整本地校验编排、加密写入、解析同事务接入（含不外发）、归属隔离接口、模型摘要和报价汇总行注记；28 项新增定向测试通过。首轮全量 1291 passed / 1 failed（原报价汇总行旧期望），仅更新 test_pipeline.py 对应文字后，Docker 全量 1292 passed、1 warning，含 A-03 与 M5 引用回归。用户批准的 outbound.py、validate.py 范围扩展已实施，默认参数兼容。下一任务 **M6-05**（Sonnet 档，medium），继续执行。

- M6-03 已完成（2026-10-20，GPT-6.1 Sol）：明确日期解析、L-07 报价过期与 L-08 交期检查；新增 3 份虚构样本，日期与既有金额定向测试 91 passed；Docker 全量 1264 passed、1 warning。新增 Q-94（待确认）。下一任务 **M6-04**（Sonnet 档，high），继续执行。

- M6-02 已完成（2026-10-20，GPT-6.1 Sol）：L-04 大小写金额、L-05 附件合计与主合同总价、L-06 币种检查；113 项金额相关定向测试通过，新增合同与附件的精确 manifest；Docker 全量 1237 passed、1 warning。新增 Q-93（待确认）。下一任务 **M6-03**（Sonnet 档，medium），继续执行。用户已明确允许 M6-04 / M6-06 最小追加 outbound.py、validate.py、frontend/src/api/http.ts 及对应测试，保留默认行为。

- M6-01 已完成（2026-10-20，GPT-6.1 Sol）：公共校验结构、Decimal 数字解析、报价表识别、L-01 / L-02 / L-03 / L-INFO 与 7 份虚构 XLSX / DOCX / PDF 样本；95 项定向测试通过，manifest 的校验项与位置完全一致，摘要出口复检为空；Docker 全量 1219 passed、1 warning。新增 Q-91、Q-92（待确认）。用户已在本会话安排 M6-01～M6-06 连续执行；下一任务 **M6-02**（Sonnet 档，high），继续执行。M6-04 / M6-06 的最小范围扩展已向用户提出，待答复，不影响先完成 M6-02、M6-03。

- M6 已细化（M6-00，2026-10-20，Opus 5.5），设计见 docs/design/M6.md，新增 Q-81～Q-90（待确认），**待用户确认**；任务顺序已调整（追问后端移到 M6-07），M6-01～M6-06 为 Sonnet 档，可由 GPT-6.1 Sol 连续执行；M6-07、M6-09 为 Opus 档。M5-08 的修复与 M5-06 范围例外的 Opus 复审（规则 10，自 3980acb 起）本会话因额度未做，留给下一个 Opus 会话（M6-07 开工前）。下一个任务：**M6-01**（Sonnet 档，high）

- M5-08 已完成（2026-10-20，GPT-6.1 Sol，用户在会话中明确授权执行原定 Opus 档任务）：A-02 / A-05 / A-07 / A-08、T-12 / T-13 及 M2～M4 回归通过；新增 27 份可解析样本、460 条引用的真实接口 + 模拟网关验收；修复重复问题引用升级后首个定位与快照不一致，保留修复前失败证据；修正抽屉间距与提示字号。后端最终 Docker 全量 1124 passed、1 warning；之后仅抽屉 CSS 小修，构建及 A-08 7 passed；浏览器五文件 5 段、四轮状态（completed / 分段失败 partial / unable partial / 上限 failed）、旧轮次备注、扫描件定位、两种宽度、抽屉和删除取消均核对。详见 docs/acceptance/M5-08.md。M5 验收结果待用户确认；修复及 M5-06 范围例外待 Opus 复审，未冒充独立评审。下一个任务：**M6-00**（Opus 档，high），本会话停止，不进入 M6。

- M5-07 已完成（2026-10-20，GPT-6.1 Sol）：新增 A-08 禁用词扫描测试，覆盖 frontend/src 的 .vue/.ts、backend/app 的 .py、backend/resources 全部文件和可选报告模板；排除测试与 fixtures，移除制裁等级“无明显风险”后扫描；Q-58 已决定只约束系统自有文字。Docker 定向测试 7 passed；Docker 全量测试 1122 passed、1 warning（测试容器额外挂载 frontend/ 只读卷，以便扫描前端源码）。下一个任务：**M5-08**（Opus 档，medium，M5 联调与验收）。

- M5-06 已完成（2026-10-20）：GET /requirements 与审核要求抽屉，第一步、第二步加入口链接；后端新增测试通过（除 test_eval_review.py 因本容器缺少 tools 模块无法收集外，其余 652 passed）、前端构建通过；浏览器已核对：第一步（全部文件类型，内置要求标注适用类型）、第二步（合同·采购·我方发出，不含销售专属条目，公司补充要求为空显示“暂无”，本次补充说明正确显示）；核对时把已有测试任务重新审核回到待确认，未再发送；未测公司补充要求有数据的显示；下一个任务：**M5-07**（Haiku 档，low）。

- M5-05 已完成（2026-10-20，Codex）：结果页轮次切换、覆盖说明、失败与分段失败提示、删除确认、轮次状态标签、第三步分段进度、TERM_IN_PROMPT 文字；新增 Q-80（待确认）。前端构建通过；Docker 全部测试 1105 passed、1 warning；以专用虚构账号和预置状态经真实接口核对 1440 / 1280 布局、轮次与旧问题编辑、coverage=null、failed / 两类 partial、进度 2/3→3/3 与终态跳转、404、删除确认取消；未在浏览器执行最终删除，未重新跑上传至模拟模型的全流程。Codex 首个前端任务，Opus 重点评审通过（2026-10-20，修复切换轮次时预览重复下载原件），用户确认 Codex 可继续接前端任务；Q-80 已确认；下一个任务：**M5-06**（Sonnet 档，medium）。

- M1 已验收并经用户确认（2026-10-15）
- M2 已细化（M2-00）并经用户确认，设计见 docs/design/M2.md；M2-01 至 M2-18 已完成
- 当前交接：M2-17 已完成（Docker 全部测试 641 passed、1 warning，2026-10-16）；M2-13 的 Opus 专门评审（规则 11）仍待用户安排；M2-14 至 M2-16 未在浏览器联调；回归集发现的脱敏漏替换缺口 Q-52 已修复（Docker 全部测试 642 passed，2026-10-16）
- M2 验收（M2-19 的模拟模型部分，2026-10-16）：Docker 全部测试 645 passed；用模拟模型服务经真实接口走通全流程，A-04、T-01、T-02、T-07、T-09、T-10、T-11 全部通过，浏览器联调第一步至结果页、审核历史页可用；修复 worker 以 `python -m app.worker` 启动时处理函数未注册（NO_HANDLER）、set-model 读入 BOM 两个问题；新增 Q-53、Q-54；Q-53（job 在处理函数外失败时任务卡住）已修复，Docker 全部测试 651 passed
- M2-18 已完成（2026-10-16，GPT-6.1 Sol）：新增开发环境审核评测脚本和使用说明，逐份 DOCX 复用解析→脱敏→审核（经网关），输出 Markdown；附加参数仅在当前评测会话覆盖，不写全局设置；新增 Q-57（待确认）与 16 项测试，Docker 全部测试 668 passed、1 warning
- M2-19 已完成（2026-10-19）：模拟模型全流程与 A-04、T-01、T-02、T-07、T-09、T-10、T-11 通过；用虚构样本调用真实模型（deepseek-flash）试跑非思考、思考两种模式，外发记录无真实值，结论数 = 发送的要求数，非缺失型问题的引用全部校验通过；新增 Q-58、Q-59、Q-60，Q-12 待用户决定
- M2 已验收并经用户确认（2026-10-19）；Q-12 决定采用思考模式、max_tokens 32000
- M3 已细化（M3-00）并经用户确认（2026-10-19），设计见 docs/design/M3.md；Q-38～Q-57、Q-61～Q-66 已确认；SPEC 的 max\_tokens 默认值改为 32000
- M3-01 已完成（2026-10-19）：PDF、XLSX、图片虚构样本与生成脚本、manifest 已提交，Docker 全部测试 668 passed
- M3-02 已完成（2026-10-19）：XLSX 解析、model.py 常量与 ocr_pages，Docker 全部测试通过
- M3-03 已完成（2026-10-19）：PDF 文字页解析（扫描页暂抛占位 ParseError，M3-04 替换）
- M3-04 已完成（2026-10-19）：OCR 引擎封装、图片解析、PDF 扫描页；Docker 全部测试通过，OCR 测试在 --network none 容器中通过
- M3-05 已完成（2026-10-19）：pipeline 按格式分派、OCR 预算与 ocr_slot；表格列提示支持 XLSX 和非首行表头；问题位置显示页码和单元格；脱敏回归集增加 XLSX 报价单；Docker 全部测试 749 passed；新增 Q-68（待确认）
- M3-06 已完成（2026-10-19）：节点接口与原件接口（Q-63 的错误码和响应头），A-06 重跑通过；Docker 全部测试 764 passed
- M3-07 已完成（2026-10-19）：DocPreview 组件、getBlob 与 preview 接口封装，pdfjs-dist 6.4.299；`npm run build` 通过，尚未在浏览器联调（组件未挂载，留到 M3-08）
- M3-08 已完成（2026-10-19）：结果页双栏与点击定位，common.css 与标签颜色常量整理；浏览器核对了 PDF、XLSX 的预览与高亮、1280 / 1440 宽度、审核历史页；DOCX、图片未在浏览器核对（留到 M3-09）；联调时重建了 app / worker 镜像
- M3-09 已完成（2026-10-19）：Docker 全部测试 764 passed；OCR 与 T-20 在 --network none 容器中 52 passed，运行镜像断网 OCR 可用；经真实接口 + 模拟模型走通 DOCX、PDF（文字 / 扫描 / 混合）、XLSX、PNG、JPG；T-03 通过（第二步显示低置信页提示，结果页点击问题后第 2 页扫描图高亮对齐并滚动）；浏览器补核 DOCX、图片预览与定位、completed_local 右栏；M2 验收用例回归通过；修复 DocPreview 手写编号重复显示；新增 Q-69（扫描页账号标签被 OCR 识别错时账号漏替换，M4 疑似遗漏须覆盖）
- M3 已验收并经用户确认（2026-10-19）；Q-69 交 M4 处理；下一个任务：**M4-00**（里程碑细化，Opus 档，high）
- M4 已细化（M4-00）并经用户确认（2026-10-19），设计见 docs/design/M4.md；Q-70～Q-74 已确认；Q-59 决定采用建议（账号、SWIFT 带序号，提示词补充一句），SPEC 9.2、11.2 已同步修改；下一个任务：**M4-01**（Sonnet 档，high）
- M4-01 至 M4-03 已完成（2026-10-19，Sonnet 5.5 一并执行）：人名 / 地址 / 数量识别与正文企业名称起点边界（Q-56）、疑似遗漏 find_suspects（Q-69、Q-74）、tables.py / ratio.py / quotes.py 与 Hit 的 currency、value；新增 Q-75（待确认）；下一个任务：**M4-04**（Opus 档，xhigh），模型不同，须换 Opus 会话
- GPT-6.1 Sol 分工（2026-10-16 用户决定）：试用任务 M2-13 评审通过，以后可以接手 Sonnet 档的任务，含前端任务；它做的第一个前端任务由 Opus 重点评审（见第一节）。前端界面规范见 docs/design/frontend.md
- M4-04 已完成（2026-10-19，Opus 5.5）：engine 整合第 6、7 步、数量、带序号的账号与 SWIFT、用户词条、疑似遗漏、金额比例、报价明细表汇总、命中计数与 add_user_term / decide_suspect / suspect_views / learn_confirmed_names；回归集补标人名、地址、must_suspect 并新增 Q-69 样本；Docker 全部测试 1043 passed；新增 Q-76（待确认）。注意：M4-05、M4-06 完成前，有疑似遗漏的任务在界面上无法处理、不能确认发送；下一个任务：**M4-05**（Opus 档，high）
- M4-05 已完成（2026-10-19，Opus 5.5）：GET /redaction 返回疑似遗漏，新增疑似遗漏决定与手动补充接口（错误码、行锁、A-06 重跑）；确认发送在预检通过后写入已记住的名称；系统提示词第 2 条按 SPEC 11.2 补充，版本 2026.10-2；Docker 全部测试 1059 passed；下一个任务：**M4-06**（Sonnet 档，high），模型不同，须换会话
- M4-06 已完成（2026-10-19，Sonnet 5.5）：第二步完整版（疑似遗漏列表、在原文中选择文字设为敏感信息、确认按钮不可用说明、Q-54 提示）；`npm run build` 通过；浏览器中 DOCX、XLSX、扫描 PDF 各完成一次手动补充，疑似遗漏忽略后可确认发送；下一个任务：**M4-07**（Opus 档，high），模型不同，须换会话
- M4-07 已完成（2026-10-19，Opus 5.5）：新增 A-03（回归集与 fixtures 共 26 份样本经真实接口 + 模拟模型，逐条检查 llm_calls 与实际请求）、日志安全（DEBUG 级别覆盖解析、第二步、确认发送、审核及拦截 / 出错分支）、T-06、T-08 测试，全部通过；M2、M3 验收用例随全部测试回归通过，Docker 全部测试 1063 passed；日志安全测试发现 pdfminer 在 DEBUG 级别输出 PDF 内容，config.setup_logging 把解析库日志器固定为 WARNING；浏览器中 DOCX 走通疑似遗漏替换、手动补充、确认发送到结果页，检查了 XLSX、扫描 PDF（Q-69 账号列为疑似遗漏）的第二步，T-08 两个任务在界面上验证；新增 Q-77（待确认）。M4 验收结果待用户确认；下一个任务：**M5-00**（Opus 档，high）
- M4 已验收并经用户确认（2026-10-19）；Q-77 按建议处理（② 维持 SPEC，报价单号不加入 REFNO；③ 交 M5 细化）；下一个任务：**M5-00**（Opus 档，high）
- M5 已细化（M5-00，2026-10-19，Opus 5.5），设计见 docs/design/M5.md，已经用户确认（2026-10-19）；Q-78 已确认；Q-58 决定采用建议（A-08 只约束系统自己的文字）；下一个任务：**M5-01**（Opus 档，xhigh）
- M5-01 已完成（2026-10-19，Opus 5.5）：chunking.py（按文件开头、标题、一级条款切分）、逐段调用（拦截即整轮 blocked，其他失败分段跳过）、11.5 合并、进度暂存 coverage.progress（RunDetail 的 chunk_count、progress）、engine.build_outbound、汇总行位置映射到表头行（Q-72 ⑥）；不分段时消息与 M2 逐字相同；T-12 经真实接口通过；test_log_safety 改为替换 build_outbound；Docker 全部测试 1084 passed；下一个任务：**M5-02**（Opus 档，high）
- M5-02 已完成（2026-10-19，Opus 5.5）：prompt.fixed_prompt_text；engine._add_term 在写入前按出口复检规则比较（只看 MAP 类原因，固定文字本身的格式扫描命中不算），手动补充与疑似遗漏“替换”两条路径返回 422 TERM_IN_PROMPT，具体名称不受影响；T-13 经真实接口通过（run=failed、提示文字、无新的 sent 记录）；Docker 全部测试 1088 passed（含回归集与 A-03）；下一个任务：**M5-03**（Sonnet 档，medium），模型不同，须换 Sonnet 会话
- M5-03 已完成（2026-10-20，Codex）：coverage 结构与 RunDetail summary / notes 已实现；新增虚构报价夹具补足 A-05；新增 Q-79（待确认）；Docker 全部测试 1094 passed、1 warning；下一个任务：**M5-04**（Sonnet 档，medium）
- M5-04 已完成（2026-10-20，Opus 5.5，用户要求在本会话继续）：PATCH /tasks/{id}（reviewed、awaiting_confirm 可改；local_only 409 LOCAL_ONLY；锁任务行后 apply_party_changes 整体重算，不写审计，latest_run_id 与 high_count 不变）；ReRunDialog 与结果页“重新审核”按钮（reviewed 且 policy=ai 时显示）；`npm run build` 通过；浏览器 1440 / 1280 宽度走通：立场改为采购后回到第二步，代号按新立场重算（我方改为买方后对方为“供应商_A”），再次确认发送产生第 2 轮，第 1 轮保留；控制台只有登录前的 /auth/me 401；未在浏览器中触发 409 提示（后端测试覆盖）；Docker 全部测试 1105 passed；下一个任务：**M5-05**（Sonnet 档，high）
- 上次 Opus 评审到的提交：caa723e（2026-10-22 Opus 5.5 在 M7-09 评审 5d85ee2..caa723e 的 M7-02～M7-08 Sonnet 提交：文件范围与任务行及 Q-115 ⑨ 一致；未改 SPEC、CLAUDE.md、设计文档；审计 detail 不含密码、密钥、词条与要求文字；管理接口全经 require_admin；前端无 v-html。修复 2 处：chat/service.py 一处赋值格式；设置页 1280px 横向滚动条（Q-116，改 MainLayout）；此前 adbabe1（2026-10-22 Opus 5.5 在 M6-09 评审 39020d3..adbabe1 的 M6-08 前端提交，未发现问题）；此前 39020d3（2026-10-22 Opus 5.5 评审 3980acb..39020d3，即 M5-05 收尾至 M6-06 及 M6-05 补充修复：文件范围与用户授权的扩围一致；未改 SPEC、CLAUDE.md；ai_summary 只含位置、行号、百分比、币种和日期，出口复检零命中；报告不读 text_enc；新接口均经 owned_* 校验；前端无 v-html。发现并修复 1 处：英文表头“Amount excl./incl. VAT”“Price incl. VAT”被识别为税额列，整张报价表不做核验——column_kind 改为表头同时含金额/总价/单价词时不算税额列，改正了把错误行为写成期望的测试并补整表用例，design/M6.md 3.1 同步注明；Docker 全量 1310 passed、1 warning。遗留（不阻塞）：报告与前端的问题类别沿用前端既有中文“措辞 / 合规”，与 SPEC 11.3 的“表述 / 合规与制裁”不一致，留 M6-09 一并确认；ResizeObserver loop 为 Element Plus 在开发期缩放窗口时的已知提示，不影响功能）

## 一、模型分工与推理强度

| 模型 | 负责什么 |
|-|-|
| Opus 5.5 | 每个里程碑开工前细化任务（Mx-00）；安全核心（加密、会话、权限、脱敏核心、出口复检、模型网关、追问脱敏、备份恢复）；算法难点（DOCX 自动编号、模型输出校验、分段合并）；评审 Sonnet 和 Haiku 的提交；里程碑验收 |
| Sonnet 5.5 | 规格写得清楚的常规实现：接口、页面、解析器、本地校验、报告、部署脚本 |
| Haiku 5.5 | 机械性、照抄型、低风险的任务：资源文件、占位页面、样本文件、简单的扫描测试 |
| GPT-6.1 Sol | 可以接手“模型”列为 Sonnet 的任务，与 Sonnet 5.5 同档；每个 Sonnet 档任务交给谁由用户安排。在 Codex 中执行，规则见仓库根目录的 AGENTS.md。安全核心、Mx-00 细化、评审和里程碑验收（Opus 档）不交给它。前端任务按 docs/design/frontend.md 执行；它做的**第一个前端任务**完成后停下，由 Opus 重点评审（含浏览器截图核对），用户确认后再继续接前端任务。2026-10-16 试用任务 M2-13 经 Opus 专门评审通过后，用户决定按此分工 |

**推理强度**（“强度”列）：

| 强度 | 用在什么任务 |
|-|-|
| low | 照抄、占位、简单扫描 |
| medium | 常规接口和页面、按规格转写、验收 |
| high | 解析器、金额计算、上传安全检查、认证与权限、里程碑细化 |
| xhigh | 出错会导致数据外泄或数据丢失、且逻辑复杂的任务：DOCX 自动编号与修订、脱敏核心、模型输出校验、分段合并、备份加密、恢复 |

后续里程碑的强度是预估值，会在该里程碑的 Mx-00 中复核。

## 二、执行规则（每个会话必须遵守）

1. **开工**：用户在会话中说“执行任务 Mx-yy”。先核对：该任务状态为“待执行”，依赖都已“已完成”，本会话的模型不低于“模型”列的要求。
2. **开工前必读**：CLAUDE.md、本任务所在行、“SPEC”列列出的章节、docs/open\_questions.md；前端任务（改动 frontend/ 下的页面、组件或样式）还须读 docs/design/frontend.md。不需要通读整份 SPEC。
3. **在 main 分支上直接开发**，按小步骤提交。提交说明以任务编号开头，用英语或日语书写，例如“M1-01 Backend base: pin dependency versions”。
4. **文件范围**：只创建或修改“文件范围”列中的文件，以及它们对应的测试文件。需要改动范围外的文件（尤其是数据库模型、迁移、公共接口）时，停下来报告，不要自行修改。
5. **不得修改** docs/SPEC.md、CLAUDE.md 和 docs/design/ 下的设计文档（设计文档由 Opus 在 Mx-00 中制定，是任务之间的接口契约）。
6. **遇到 SPEC 没写明的细节**：按“最简单、最安全”的做法实现，在代码注释里写明，并在 docs/open\_questions.md 第三节追加一条，状态写“待确认”。
7. **完成标准**：
   - “完成标准”列的要求全部满足；
   - 在 Docker 中运行全部测试，结果通过（命令见 CLAUDE.md 的“常用命令”。M1-01 会在报告中给出构建和测试命令，由 M1-04 的 Opus 会话写进 CLAUDE.md）；
   - 代码已提交。
8. **收尾**（每个任务结束时都要做）：
   1. 把本任务的状态改为“已完成”，更新本文件顶部的“当前进度”，提交。
   2. 输出完成报告：改了哪些文件、测试结果、按规则 6 做出的决定、遗留问题。
   3. 告诉用户**下一个任务的编号、模型和推理强度**，并按下面的交接规则处理。
9. **交接规则**：
   - **下一个任务是 Mx-00（里程碑细化）或里程碑验收**：停下来，等用户安排。
   - **下一个任务的模型和本会话相同**：直接继续执行，不必等用户。如果推理强度和本会话不同，在报告中提示用户调整。同一个会话连续执行超过 4 个任务，或已经跨了里程碑，就建议用户开新会话，避免上下文过长、浪费 token。
   - **下一个任务的模型不同**：停下来，请用户用对应的模型开新会话。
   - “模型”列为 Sonnet 的任务，Sonnet 5.5 和 GPT-6.1 Sol 都算“相同的模型”；在报告中写明下一个任务是 Sonnet 档，由用户决定继续在本会话做还是换另一个。
10. **Opus 的评审**：Opus 会话开始一个任务前，先评审“上次 Opus 评审到的提交”之后，Sonnet、Haiku 和 GPT-6.1 Sol 的全部提交（用 `git log` 和 `git diff` 查看）。有问题的，先修复，或在报告中列出交给用户决定。评审完更新“上次 Opus 评审到的提交”。
11. **GPT-6.1 Sol 提交的评审**：试用期已结束（M2-13，2026-10-16 评审通过），GPT-6.1 Sol 完成任务后不必再停下等专门评审，它的提交按规则 10 由下一个 Opus 会话评审。评审时逐条检查：只改了文件范围内的文件；未改 SPEC、CLAUDE.md 和设计文档；完成标准逐条有测试覆盖；SPEC 没写明的细节记入了 open\_questions；日志和错误消息不含原文、名称、金额、密钥；所有任务接口校验 owner\_id（他人返回 404）；Docker 中全部测试通过；代码风格与周围一致；前端任务符合 docs/design/frontend.md，并在浏览器中截图核对。发现的问题汇报给用户；如质量明显下降，由用户决定是否调整分工。

## 三、状态说明

`待执行` → `已完成`。另有 `待细化`：后续里程碑的任务，由该里程碑的 Mx-00 补全文件范围和接口约定，经用户确认后改为“待执行”。

## 四、任务清单

### M1 骨架（验收：A-06、T-20）

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC | 文件范围 | 完成标准 | 状态 |
|-|-|-|-|-|-|-|-|-|
| M1-01 | 后端基础：锁定依赖版本；读取环境变量和密钥文件的 config；FastAPI 入口，统一挂载 /api/v1；统一错误格式；日志配置（只输出到标准输出，不记录请求体）；pytest 配置；后端 Dockerfile | Sonnet | medium | — | 3、5、19.1、20.3、17.7 | backend/requirements.txt、requirements-dev.txt、Dockerfile、pytest.ini、app/main.py、app/config.py、app/security/errors.py、app/\_\_init\_\_.py 及各模块空的 \_\_init\_\_.py、tests/conftest.py（基础部分） | Docker 镜像能构建；用 python:3.12-slim 固定版本，镜像内含 postgresql-client 16；依赖全部锁定精确版本，不含 SPEC 禁用的库；错误返回 {"code","message"} 格式；有一个冒烟测试通过；报告中给出构建和测试命令 | 已完成 |
| M1-02 | 数据库模型与初始迁移：按 SPEC 18 节定义全部 22 张表、CHECK 约束、索引和级联删除；初始迁移启用 pgcrypto | Sonnet | high | M1-01 | 18、open\_questions Q-04、Q-07 | backend/alembic.ini、app/db/base.py、app/db/session.py、app/db/models.py、app/db/alembic/env.py、app/db/alembic/versions/0001\_initial.py、tests/test\_migrations.py | 升级后正好 22 张表，字段、类型、非空、默认值、CHECK、唯一约束、索引与 SPEC 一致；回滚后数据库为空；测试使用独立的测试库 | 已完成 |
| M1-03 | 部署编排：docker-compose 定义 4 个服务（db 带健康检查；app 启动时执行 alembic upgrade head；worker 先用空循环）；Caddyfile（开发环境用 tls internal，/api 转发到 app:8000，上传上限 320 MB）；.env.example；worker 空循环 | Sonnet | medium | M1-02 | 4、20.2、20.3 | deploy/docker-compose.yml、deploy/Caddyfile、deploy/.env.example、backend/app/worker.py | `docker compose up -d` 后 4 个容器都正常运行（web 容器在前端完成前可先提供一个占位页）；db 不对外暴露端口；所有镜像使用固定版本标签 | 已完成 |
| M1-04 | 加密与密码工具：AES-256-GCM 加解密（密文格式：版本 1 字节 + 随机数 12 字节 + 密文 + 标签 16 字节）；主密钥加载；argon2id 哈希；密码强度规则；生成主密钥的命令。开工时先把 M1-01 报告中的命令写进 CLAUDE.md 的“常用命令” | Opus | high | M1-03 | 17.1、17.3、Q-08 | app/security/crypto.py、app/security/passwords.py、app/cli.py（gen-key 部分）、tests/test\_crypto.py、tests/test\_passwords.py、CLAUDE.md（常用命令） | 往返加解密正确；篡改任一字节都解密失败；每次的随机数不同；密钥文件不是 32 字节时拒绝启动；密码规则符合 6.6 | 已完成 |
| M1-05 | 认证：会话令牌（库中只存 SHA-256，空闲 8 小时过期，Cookie 为 HttpOnly、Secure、SameSite=Lax）；CSRF 校验；登录、退出、/auth/me、改密；连续 5 次失败锁定 15 分钟；停用账号时删除其会话；登录写审计日志；创建管理员的命令 | Opus | high | M1-04 | 6.1、6.6、17.1、17.7、19.2、Q-05、Q-11 | app/security/sessions.py、app/security/csrf.py、app/auth/\*、app/audit/service.py、app/cli.py（create-admin 部分）、tests/test\_auth.py | 上述行为都有测试覆盖；不带 CSRF 令牌的修改类请求返回 403 CSRF\_FAILED；日志中不出现密码和令牌 | 已完成 |
| M1-06 | 权限依赖与安全测试：current\_user、require\_admin（返回 403）、get\_owned\_task / run / finding（非本人返回 404，管理员也一样）；A-06 测试（用只在测试中挂载的路由）；T-20 静态扫描测试 | Opus | high | M1-05 | 17.2、23（A-06、T-20）、Q-03、Q-09 | app/security/deps.py、tests/test\_permissions.py、tests/test\_no\_network\_imports.py | A-06、T-20 测试通过 | 已完成 |
| M1-07 | 前端框架：Vue 3 + TS + Vite + Element Plus + Pinia + Router；请求封装（自动带 X-CSRF-Token，401 时跳转登录页）；登录状态存储；路由守卫（未登录、强制改密、非管理员）；主布局（顶部只有“智能审核”“审核历史”，右上角用户下拉含修改密码和退出，“设置”只对管理员显示）；登录页；改密对话框；按 Asia/Shanghai 显示时间的工具函数；前端 Dockerfile（node 构建后放进 caddy 镜像） | Sonnet | high | M1-06 | 1.6、6.1、6.6、6.7（只留位置）、21、Q-06 | frontend/ 下的工程文件、src/main.ts、App.vue、router/、stores/、api/、utils/、layouts/MainLayout.vue、components/ChangePasswordDialog.vue、views/Login.vue、frontend/Dockerfile、deploy/docker-compose.yml 中 web 服务部分 | 能登录和退出；强制改密时对话框无法关闭；普通用户看不到“设置”，直接访问设置页会被拦截；package-lock.json 锁定版本；`vue-tsc` 类型检查通过 | 已完成 |
| M1-08 | 前端占位页面：新建审核、审核结果、审核历史，设置页 5 个标签页（账号、模型、审核要求、脱敏、数据），每页只显示标题和“建设中” | Haiku | low | M1-07 | 1.6、5、15 | src/views/ReviewNew.vue、ReviewResult.vue、History.vue、views/settings/\*.vue（含 SettingsLayout.vue）、路由中对应的条目 | 菜单点击都能进入对应页面；设置页的标签页能切换 | 已完成 |
| M1-09 | M1 联调与验收：完整跑一遍，浏览器手动检查，向用户汇报 | Opus | medium | M1-08 | 22、23 | CLAUDE.md、docs/ | A-06、T-20 通过；验收结果已汇报给用户 | 已完成 |

### M2 最小闭环：只支持 DOCX 的完整链路（验收：A-04、T-01、T-02、T-07、T-09、T-10、T-11）

**开工前必读 [docs/design/M2.md](design/M2.md)**：模块划分、函数签名、接口形状都以它为准。下表“文件范围”中的路径相对于 `backend/`（前端任务相对于 `frontend/`），“设计”列指向 M2.md 的章节。

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC / 设计 | 文件范围 | 完成标准 | 状态 |
|-|-|-|-|-|-|-|-|-|
| M2-00 | 里程碑细化：模块划分、接口约定、模拟模型服务约定 | Opus | high | M1-09 | 22 | docs/design/M2.md、docs/TASKS.md | 用户确认 | 已完成 |
| M2-01 | jobs 队列与 worker 框架：取任务、心跳、超时重新入队、最多尝试 3 次、并发线程、处理函数注册、OCR 单并发锁、定时工作（含清理过期会话） | Sonnet | high | M2-00 | 7.5、18.19、21；设计二 | app/db/jobs.py、app/worker.py、tests/test\_jobs.py、tests/test\_worker.py | 两个并发取任务不会取到同一条（用两个连接验证 SKIP LOCKED）；心跳超时的任务按次数重新入队或置 failed；处理函数异常和未注册类型都置 failed 且 last\_error 不含消息内容；过期会话被清理；worker 能响应停止信号 | 已完成 |
| M2-02 | 上传与任务接口：上传检查、原件加密存储、新建任务并入队 parse、任务列表、任务详情、删除任务 | Sonnet | high | M2-01 | 6.2 第一步、6.4、7.1、17.6、17.8、19.3；设计三 | app/tasks/router.py、service.py、schemas.py、storage.py、app/main.py（只加 include\_router 一行）、tests/test\_tasks\_api.py、tests/test\_upload\_checks.py | 设计三的每个错误码都有测试；落盘文件是密文且不含明文片段；删除任务后原件文件、节点、轮次全部消失而 llm\_calls 保留；列表只返回本人任务，搜索、日期筛选、分页正确；对真实接口重跑 A-06（他人和管理员 404） | 已完成 |
| M2-03 | DOCX 虚构样本生成脚本与样本：修订痕迹、“第%1条”和“%1.%2”自动编号（含 chineseCounting）、签约方与简称、电话、邮箱、账号、SWIFT、地址、金额与大写金额、页眉页脚、批注、文本框、合并单元格、提示词注入文字（T-09） | Sonnet | medium | M2-02 | 8.3、9.3、23；设计十一 | tests/fixtures/docx/make\_docx\_fixtures.py、tests/fixtures/docx/\*.docx、tests/fixtures/docx/manifest.yaml | 脚本可重复生成；每个样本的预期事实写入 manifest.yaml；内容全部虚构；用 Word 兼容的 XML（w:ins、w:del、numbering.xml） | 已完成 |
| M2-04 | 文件类型识别 | Haiku | low | M2-03 | 8.6；设计五 | app/parsing/doctype.py、tests/test\_doctype.py | 按 SPEC 8.6 的顺序和关键词匹配，只看文件名和正文前 1000 字；英文不区分大小写；每种类型及“都不命中→letter”都有测试 | 已完成 |
| M2-05 | 资源词典起草与加载：countries.yaml（约 200 个国家和地区，中英文名称及别名，主要港口和城市）、org\_types.yaml、common\_terms.txt（常见合同用语，至少 150 条） | Haiku | medium | M2-04 | 9.4、9.5、Q-22；设计五 | resources/countries.yaml、resources/org\_types.yaml、resources/common\_terms.txt、app/redaction/dictionaries.py、tests/test\_dictionaries.py | YAML 能加载且无重复别名；“Jebel Ali”“迪拜”→阿联酋、“Mersin”→土耳其、“Hamburg”→德国 等有测试；英文按单词边界匹配（“Iran”不命中“Irani”）；企业类型按 9.4 判断 | 已完成 |
| M2-06 | 内置审核要求与系统提示词：按 SPEC 原文转写 builtin\_requirements.yaml（第 12 节全部条目）和 prompts/system.md（11.2 不联网版）；要求加载与筛选 | Haiku | low | M2-05 | 11.2、12；设计五 | resources/builtin\_requirements.yaml、resources/prompts/system.md、app/review/requirements.py、tests/test\_requirements.py | 条目数与 SPEC 一致（G 12 条、C 14 条、Q 6 条、O 4 条、I 4 条、LT 4 条）；文字与 SPEC 逐字一致；筛选测试覆盖立场和来源限制（如 C-13 只在销售、G-11 只在我方发出）；提示词版本号可读出 | 已完成 |
| M2-07 | 统一文档模型与 DOCX 解析：修订、自动编号、clause\_no、合并单元格、页眉页脚、批注、文本框、嵌套表格、位置编号、警告 | Opus | xhigh | M2-06 | 8.1–8.3、Q-15；设计四 | app/parsing/model.py、app/parsing/docx.py、tests/test\_parse\_docx.py | T-01、T-02 通过（用 M2-03 的样本）；manifest.yaml 中的预期事实全部满足；损坏文件抛出 ParseError 且消息不含原文 | 已完成 |
| M2-08 | 脱敏核心与 parse 流水线：9.3 第 1–5 步、代号分配、对照表加密、替换记录、外发文本构建、主体说明、转义 document 标签；parse 处理函数（含不外发分支） | Opus | xhigh | M2-07 | 7.1、9.1–9.3、9.7、9.9、17.5；设计一、六 | app/redaction/engine.py、detect.py、parties.py、placeholders.py、outbound.py、app/tasks/pipeline.py、tests/test\_redaction\_engine.py、tests/test\_pipeline.py | 样本中的名称、简称、电话、邮箱、账号、SWIFT、证件号、编号、金额全部替换，同一对象同一代号；保留项不被替换；外发文本格式符合 9.7；不外发的任务直接 completed\_local 且不产生任何脱敏数据 | 已完成 |
| M2-09 | 出口复检与还原 | Opus | high | M2-08 | 9.8、9.9、Q-19；设计六 | app/redaction/recheck.py、app/redaction/restore.py、tests/test\_recheck.py、tests/test\_restore.py | 混入任一真实值、已记住名称、自定义敏感词或未替换的电话 / 邮箱 / 账号 / 证件号 / 带币种金额时拦截；拦截原因只含类别和位置；短数字不误拦；还原长代号优先 | 已完成 |
| M2-10 | 模型网关、设置读取、用量统计、模拟模型服务、开发用 set-model 命令 | Opus | high | M2-09 | 5、7.5、11.1、11.7、15.2、17.4、Q-12；设计七 | app/llm/gateway.py、app/llm/usage.py、app/admin/settings\_store.py、app/cli.py（set-model 部分）、tests/mock\_llm.py、tests/test\_gateway.py、tools/mock\_llm\_server.py、deploy/docker-compose.mock.yml | 执行顺序为上限检查 → 出口复检 → 发送 → 记录；T-07 的网关部分通过（blocked 记录不含真实值）；达到上限不发送；重试次数和间隔正确；附加请求参数原样合并；API 密钥不出现在日志和 llm\_calls 中 | 已完成 |
| M2-11 | 审核核心：拼接提示词、输出结构、结果校验（11.4 全部 9 条）、解析失败重试 1 次 | Opus | xhigh | M2-10 | 11.2–11.4、A-07；设计八 | app/review/prompt.py、schema.py、validate.py、tests/test\_review\_validate.py、tests/test\_review\_prompt.py | T-10、T-11 通过；要求结论数 = 发送的要求数（A-07）；document 标签被转义；类别、严重程度映射与兜底正确；重复问题合并 | 已完成 |
| M2-12 | review 处理函数与轮次接口：确认发送（含预检）、轮次列表、轮次详情（还原真实名称）、修改问题处理状态 | Sonnet | high | M2-11 | 7.1–7.3、19.5、A-04；设计八 | app/review/router.py、service.py、job.py、app/main.py（只加 include\_router 一行）、tests/test\_runs\_api.py、tests/test\_review\_job.py | 设计八的每个 409 错误码都有测试；local\_only 任务模型调用次数为 0（A-04）；网关拦截时 run=blocked 且任务回到 awaiting\_confirm（T-07）；返回给前端的文字已还原；对真实接口重跑 A-06 | 已完成 |
| M2-13 | 脱敏确认接口：签约方查询与修改、脱敏摘要、外发文本预览、修改文件类型 | GPT-6.1 Sol（试用） | medium | M2-12 | 6.2 第二步、19.3、19.4；设计九 | app/redaction/router.py、app/tasks/router.py（只加 PATCH 文件类型）、app/main.py（只加 include\_router 一行）、tests/test\_redaction\_api.py | 必须恰好一方为我方；非 awaiting\_confirm 时修改返回 409；外发文本预览是代号版本；对真实接口重跑 A-06 | 已完成 |
| M2-14 | 前端：新建审核第一步（上传与设置）、第三步（审核中轮询）、枚举翻译、multipart 请求 | Sonnet | medium | M2-13 | 6.2；设计十 | src/api/http.ts（加 postForm）、src/api/tasks.ts、src/utils/labels.ts、src/views/ReviewNew.vue | 上传前在前端检查数量、大小、扩展名；立场和来源默认为该用户上次的选择；轮询每 3 秒，离开页面停止；`npm run build` 通过 | 已完成 |
| M2-15 | 前端：第二步基础版（文件信息与类型修改、签约方确认、脱敏摘要、查看将发送的文本、确认发送） | Sonnet | medium | M2-14 | 6.2 第二步；设计九、十 | src/views/ReviewNew.vue、src/api/redaction.ts、src/api/runs.ts、src/components/ 下新增的第二步子组件 | 未选我方不能提交；国家、类型用可输入的下拉框（Q-50 ②）；409 错误按错误码给出中文提示；外发文本纯文本显示；`npm run build` 通过 | 已完成 |
| M2-16 | 前端：结果页基础版（顶部信息栏、问题清单 FindingCard、审核要求逐条结论标签页）、审核历史页 | Sonnet | medium | M2-15 | 6.3、6.4；设计八、十 | src/views/ReviewResult.vue、src/views/History.vue、src/components/FindingCard.vue | 问题卡片字段与 6.3 一致，可直接替换的措辞带复制按钮，处理状态和备注可保存；历史页搜索、日期筛选、分页、删除（二次确认）可用；界面不出现“审核通过”“无风险”；`npm run build` 通过 | 已完成 |
| M2-17 | 脱敏回归测试集：至少 5 份虚构样本（合同、报价单、订单、询价函、函件各一，中英文混合）、标注文件、自动化测试 | Sonnet | high | M2-16 | 23.3；设计十一 | tests/fixtures/redaction\_golden/\*、tests/test\_redaction\_golden.py | must\_replace 全部不在外发文本中（召回率 100%）；must\_keep 全部仍在；M2 范围内只标注第 1–5 步的类别（人名、地址留到 M4） | 已完成 |
| M2-18 | 审核效果评测脚本 | Sonnet | medium | M2-17 | 23.3；设计七、八 | tools/eval\_review.py、tools/README.md | 对指定目录下的样本走完 parse → 脱敏 → 审核（经网关），输出每个样本的要求结论统计和问题清单到 Markdown 文件；可用 `--extra-params` 临时覆盖附加请求参数（对比思考模式）；只能在开发环境运行 | 已完成 |
| M2-19 | M2 联调与验收：用模拟模型服务走通全流程；用户提供 DeepSeek 测试密钥后，用虚构样本调用真实模型试跑，对比思考与非思考模式 | Opus | high | M2-18 | 22、23、Q-12 | 按需 | A-04、T-01、T-02、T-07、T-09、T-10、T-11 通过；评测结果汇报给用户 | 已完成 |

### M3 解析补全与预览（验收：T-03）

**开工前必读 [docs/design/M3.md](design/M3.md)**：模块划分、函数签名、接口形状都以它为准；前端任务同时遵守 [docs/design/frontend.md](design/frontend.md)。下表“文件范围”中的路径相对于 `backend/`（前端任务相对于 `frontend/`），“设计”列指向 M3.md 的章节。

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC / 设计 | 文件范围 | 完成标准 | 状态 |
|-|-|-|-|-|-|-|-|-|
| M3-00 | 里程碑细化：模块划分、接口约定、样本约定、任务拆分 | Opus | medium | M2-19 | 22；— | docs/design/M3.md、docs/design/frontend.md（结果页双栏提前到 M3）、docs/TASKS.md、docs/open\_questions.md | 用户确认 | 已完成 |
| M3-01 | 虚构样本生成脚本与样本：文字版 PDF、扫描版 PDF（含低质量页）、混合 PDF、旋转页 PDF、加密 PDF、XLSX 报价单（标题行在表头上方、百分比与货币格式、合并单元格、合计行）、含未缓存公式 / 隐藏行 / 隐藏工作表的 XLSX、清晰 PNG、带 EXIF 旋转的低质量 JPG | Sonnet | medium | M3-00 | 8.4、8.5、23；设计九 | tests/fixtures/pdf/\*、tests/fixtures/xlsx/\*、tests/fixtures/images/\*（各含生成脚本和 manifest.yaml）、requirements-dev.txt（只加 reportlab）、Dockerfile（只在 test 阶段安装 fonts-wqy-microhei） | 脚本可重复生成；内容全部虚构；manifest 写明每页类型、关键节点文字、必须出现的警告和必须替换的敏感项；运行镜像不含 reportlab 和字体 | 已完成 |
| M3-02 | XLSX 解析：两次读取、单元格文字格式化（数字、百分比、货币、日期）、合并单元格、隐藏工作表、未缓存公式与隐藏行列的警告、单元格数上限 | Sonnet | high | M3-01 | 8.1、8.2、8.4、8.7；设计二、三 | app/parsing/model.py（只加第二节的常量和 ocr\_pages 字段）、app/parsing/xlsx.py、tests/test\_parse\_xlsx.py | manifest 的预期事实全部满足；数字不出现科学计数法和浮点误差；损坏、加密、超限文件抛出 ParseError 且消息不含原文 | 已完成 |
| M3-03 | PDF 文字页解析：inspect\_pdf、文字页与扫描页判定、文本行合并为块、表格单元格、表格区域内的文字不重复（Q-16）、bbox 与旋转页换算、页数上限 | Sonnet | high | M3-02 | 8.1、8.2、8.5、8.7、Q-16；设计二、四 | app/parsing/pdf.py（文字页部分；扫描页先抛出 ParseError 占位）、tests/test\_parse\_pdf.py | 文字版和旋转页样本的预期事实全部满足；表格内文字只出现在单元格节点中；超过 200 页、加密、损坏时抛出 ParseError | 已完成 |
| M3-04 | OCR 与图片：ocr.py（RapidOCR 引擎复用、禁用 onnxruntime 遥测、行合并）、PDF 扫描页（pypdfium2 200 DPI 渲染、坐标换算）、parse\_image（EXIF 方向、缩放、像素上限）、低置信与未识别到文字的警告 | Sonnet | high | M3-03 | 8.5、8.7、21、Q-64；设计四、五 | app/parsing/ocr.py、app/parsing/image.py、app/parsing/pdf.py（扫描页部分）、tests/test\_ocr.py、tests/test\_parse\_image.py、tests/test\_parse\_pdf.py | 扫描、混合、图片样本的预期事实满足（关键词出现、低置信页有警告）；坐标换算有测试；完成报告给出在 `--network none` 容器中运行 OCR 测试的命令和结果；T-20 仍通过 | 已完成 |
| M3-05 | pipeline 接入与脱敏适配：按格式分派、OCR 预算与 ocr\_slot、删除“后续版本提供”分支；表格列提示支持 XLSX 和非首行表头；问题位置显示页码和单元格 | Opus | xhigh | M3-04 | 7.1、8.7、9.3 第 5 步、Q-65；设计一、六 | app/tasks/pipeline.py、app/redaction/engine.py（只改 \_table\_hints 及其辅助函数）、app/review/service.py（只改 \_View.locations）、tests/test\_pipeline.py、tests/test\_redaction\_engine.py、tests/test\_runs\_api.py、tests/fixtures/redaction\_golden/\*（增加 XLSX 报价单样本）、tests/test\_redaction\_golden.py | 五种格式都能从上传走到 awaiting\_confirm 或 completed\_local；OCR 超预算在 OCR 前失败；XLSX 报价单的单价和金额全部替换；回归测试集通过；对真实接口重跑 T-07 的预检拦截 | 已完成 |
| M3-06 | 预览接口：节点接口、原件接口（错误码、响应头、保留期清理后的表现） | Sonnet | medium | M3-05 | 6.3、17.2、19.3、Q-63；设计七 | app/tasks/router.py、app/tasks/service.py、app/tasks/schemas.py、tests/test\_preview\_api.py | 每个错误码都有测试；raw 字节与原件一致且响应头齐全；text\_available 在清理后为 false；对真实接口重跑 A-06（他人和管理员 404） | 已完成 |
| M3-07 | 前端：DocPreview 组件（DOCX、XLSX 按节点渲染，PDF 用 pdfjs-dist 逐页渲染，图片用 img，高亮层与滚动定位）、getBlob 与 preview 接口封装 | Sonnet | high | M3-06 | 6.3 右栏、Q-17；设计八 8.1、8.2 | src/api/http.ts（只加 getBlob）、src/api/preview.ts、src/components/DocPreview.vue、package.json、package-lock.json（只加 pdfjs-dist） | 四种格式都能显示；传入 loc 后高亮并在预览区内滚动；PDF 只渲染可视页；原件不经 URL 直接加载；`npm run build` 通过；按 frontend.md 第十一节自检 | 已完成 |
| M3-08 | 前端：结果页双栏与点击定位；公共样式整理（common.css，标签颜色移到 labels.ts） | Sonnet | medium | M3-07 | 6.3；设计八 8.3；frontend.md 第二节、第十节 | src/views/ReviewResult.vue、src/components/FindingCard.vue、src/styles/common.css、src/main.ts（只加引入 common.css）、src/utils/labels.ts、src/views/ReviewNew.vue、src/views/History.vue、src/components/ConfirmStep.vue（后三个只改为使用公共样式） | 点击问题高亮对应位置，缺失型问题不可点；整理后各页面截图与整理前一致；`npm run build` 通过；按 frontend.md 第十一节自检 | 已完成 |
| M3-09 | M3 联调与验收：五种格式走通全流程；T-03；回归 M2 验收用例；浏览器检查预览与定位 | Opus | medium | M3-08 | 22、23 | 按需 | T-03 通过，M2 的验收用例回归通过；验收结果汇报给用户 | 已完成 |

### M4 脱敏完善（验收：A-03、T-06、T-08）

**开工前必读 [docs/design/M4.md](design/M4.md)**：模块划分、函数签名、接口形状都以它为准；前端任务同时遵守 [docs/design/frontend.md](design/frontend.md)。下表“文件范围”中的路径相对于 `backend/`（前端任务相对于 `frontend/`），“设计”列指向 M4.md 的章节。M4-01 至 M4-03 是不碰数据库的纯函数，由 M4-04 接入 engine。

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC / 设计 | 文件范围 | 完成标准 | 状态 |
|-|-|-|-|-|-|-|-|-|
| M4-00 | 里程碑细化：模块划分、接口约定、任务拆分 | Opus | high | M3-09 | 22；— | docs/design/M4.md、docs/TASKS.md、docs/open\_questions.md | 用户确认 | 已完成 |
| M4-01 | 识别规则一：人名、地址、数量（find\_persons、find\_addresses、find\_quantities）；正文企业名称的起点边界（Q-56） | Sonnet | high | M4-00 | 9.2、9.3 第 6、7 步、9.4、Q-56；设计 2.1、2.2 | app/redaction/detect.py（只加三个函数及其常量）、app/redaction/parties.py（只改 find\_other\_org\_names 及其辅助常量）、tests/test\_detect\_persons\_addresses.py、tests/test\_redaction\_engine.py（只加起点边界的测试）、tests/fixtures/redaction\_golden/\*（只加 Q-56 样本） | 设计 2.1 的每个标签、称谓、停止条件、单位都有正例和反例；“货物由……有限公司代为装运”只替换公司名；回归测试集通过 | 已完成 |
| M4-02 | 识别规则二：疑似遗漏 find\_suspects（四条规则、优先级、排除项、Q-69 的分组数字） | Sonnet | high | M4-01 | 9.5、Q-69；设计 2.3 | app/redaction/suspects.py、resources/common\_terms.txt（只增补词条）、tests/test\_suspects.py | 四条规则及每个排除项都有正例和反例；`6222 0000 1111 2222 333` 和 OCR 标签识别错的账号行被列为 long\_digits；日期、HS 编码、数量不列；候选不跨越已替换区间；回归集各样本的 must\_keep 不被列为疑似遗漏（用 find\_suspects 直接检查原文中未替换的部分） | 已完成 |
| M4-03 | 识别规则三：表格工具移入 tables.py（含 QTY 表头）；金额比例 ratio.py；报价明细表 quotes.py；Hit 增加 currency、value | Sonnet | high | M4-02 | 9.6、9.7、Q-18；设计 2.4–2.6 | app/redaction/tables.py、ratio.py、quotes.py、app/redaction/detect.py（只给 Hit 加字段并在 find\_amounts 中填写）、app/redaction/engine.py（只把表格辅助函数改为从 tables.py 导入，行为不变）、tests/test\_tables.py、tests/test\_ratio.py、tests/test\_quotes.py | 比例全部用 Decimal、ROUND\_HALF\_UP，有标签、无标签同币种、多币种不加比例、总价为 0 各有测试；报价明细表在 DOCX、PDF、XLSX 三种 loc 下各有测试，少于 3 个数据行时不汇总；汇总文字格式与设计 2.6 一致；原有测试和回归集全部通过 | 已完成 |
| M4-04 | engine 整合：第 6、7 步与数量脱敏接入 compute、ADDR 代号、账号与 SWIFT 带序号（Q-59）、用户词条（保存与重算保留、不留半个名称）、疑似遗漏写入与决定保留、金额比例、报价明细表汇总进外发文本、命中计数；add\_user\_term、decide\_suspect、suspect\_views、learn\_confirmed\_names；回归集补标 | Opus | xhigh | M4-03 | 9.2–9.10、18.10–18.12；设计三、四 | app/redaction/engine.py、outbound.py、placeholders.py、app/redaction/detect.py（只给 ACCOUNT、SWIFT 命中填 key）、tests/test\_redaction\_engine.py、tests/test\_recheck.py、tests/test\_restore.py、tests/test\_pipeline.py、tests/test\_redaction\_golden.py、tests/fixtures/redaction\_golden/\*（补标 PERSON、ADDR、must\_suspect，新增 Q-69 样本） | 设计第七节 engine 整合的各项都有测试；回归测试集召回率 100%、must\_keep 全部保留且不被列为疑似遗漏；出口复检对新增的地址、人名真实值生效；不同账号得到不同代号且可还原 | 已完成 |
| M4-05 | 接口：疑似遗漏决定、手动补充、GET /redaction 返回疑似遗漏；确认发送时写入已记住的名称；系统提示词按 SPEC 11.2 补充一句并升版本（Q-59） | Opus | high | M4-04 | 6.2 第 4、5、8 项、9.10、19.4、Q-54；设计五 | app/redaction/router.py、app/review/service.py（只改 create\_run）、resources/prompts/system.md、tests/test\_redaction\_api.py、tests/test\_runs\_api.py、tests/test\_review\_prompt.py | 设计第五节每个错误码都有测试；手动补充公司名后可设为我方并确认发送（Q-54）；确认发送后 redaction\_terms 写入且第二个任务自动替换（T-08 接口部分）；预检拦截时不写入；提示词与 SPEC 11.2 逐字一致且版本号已更新；对新接口重跑 A-06 | 已完成 |
| M4-06 | 前端第二步完整版：疑似遗漏列表、在原文中选择文字设为敏感信息、确认按钮的不可用说明、Q-54 提示 | Sonnet | high | M4-05 | 6.2 第二步；设计六；frontend.md | src/components/ConfirmStep.vue、src/components/SuspectList.vue、src/components/ManualRedactDialog.vue、src/api/redaction.ts、src/utils/labels.ts | 字符位置与后端一致（含中英文混排、表格单元格、OCR 文字）；跨节点和已替换的选择有提示；上下文按纯文本显示；`npm run build` 通过；按 frontend.md 第十一节自检并在浏览器中对 DOCX、XLSX、扫描 PDF 各做一次手动补充 | 已完成 |
| M4-07 | M4 联调与验收：A-03 自动化测试、日志安全测试、T-06、T-08；回归 M2、M3 验收用例；浏览器检查第二步 | Opus | high | M4-06 | 17.7、22、23；设计第七节 | tests/test\_a03\_outbound.py、tests/test\_log\_safety.py；其余按需 | A-03、T-06、T-08 通过，M2、M3 的验收用例回归通过；验收结果汇报给用户 | 已完成 |

### M5 AI 审核完善（验收：A-02、A-05、A-07、A-08、T-12、T-13）

**开工前必读 [docs/design/M5.md](design/M5.md)**：模块划分、函数签名、接口形状都以它为准；前端任务同时遵守 [docs/design/frontend.md](design/frontend.md)。下表“文件范围”中的路径相对于 `backend/`（前端任务相对于 `frontend/`），“设计”列指向 M5.md 的章节。

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC / 设计 | 文件范围 | 完成标准 | 状态 |
|-|-|-|-|-|-|-|-|-|
| M5-00 | 里程碑细化：模块划分、接口约定、任务拆分 | Opus | high | M4-07 | 22；— | docs/design/M5.md、docs/TASKS.md、docs/open\_questions.md | 用户确认 | 已完成 |
| M5-01 | 分段审核与结论合并：chunking.py、按段拼消息、逐段调用与失败处理、合并、进度、build\_outbound（engine）、汇总行位置映射（Q-72 ⑥） | Opus | xhigh | M5-00 | 7.3、7.5、11.4、11.5、Q-72；设计一、二、三、五 | app/review/chunking.py、prompt.py、validate.py、job.py、service.py（只改 build\_context、RunDetail 的 chunk\_count 与 progress）、app/redaction/engine.py（只加 build\_outbound 并让 build\_outbound\_text 调用它）、tests/test\_chunking.py、test\_review\_prompt.py、test\_review\_validate.py、test\_review\_job.py、test\_runs\_api.py | 设计第十一节分段部分的每一项都有测试；N=1 时发送的消息与 M2 逐字相同，原有测试全部通过；T-12 的接口流程通过；分段轮次的结论数 = 发送的要求数（A-07） | 已完成 |
| M5-02 | 调用上限 T-13；手动补充与疑似遗漏替换拒绝提示词中的词（Q-77 ③，TERM\_IN\_PROMPT） | Opus | high | M5-01 | 7.5、9.8、19.4、Q-77；设计六 | app/review/prompt.py（只加 fixed\_prompt\_text）、app/redaction/engine.py（只改 \_add\_term、decide\_suspect 的替换分支）、tests/test\_runs\_api.py、tests/test\_redaction\_api.py、tests/test\_review\_prompt.py | T-13 通过（run=failed、提示文字、无新的 sent 记录）；两条路径都拦截提示词中的词，具体名称不受影响；回归集与 A-03 测试仍通过 | 已完成 |
| M5-03 | 覆盖说明：coverage.py（build\_coverage、describe）、处理函数写入、RunDetail 返回 summary 与 notes | Sonnet | medium | M5-02 | 11.5、11.6、A-05、Q-60、Q-79；设计四 | app/review/coverage.py、app/review/job.py（只加写入覆盖说明）、app/review/service.py（只改 RunDetail 的 coverage）、tests/test\_coverage.py、tests/test\_review\_job.py、tests/test\_runs\_api.py、tests/fixtures/docx/make\_docx\_fixtures.py、tests/fixtures/docx/manifest.yaml、tests/fixtures/docx/coverage\_quote\_table.docx | 设计第四节每一项都有测试；A-05 的样本（页眉页脚批注文本框、隐藏工作表、低置信扫描页、报价明细表、解析警告）全部出现在覆盖说明中；describe 的句子不含原文；T-12 断言覆盖说明注明分段 | 已完成 |
| M5-04 | 重新审核：PATCH /tasks/{id}、整体重算、ReRunDialog 弹窗与结果页按钮 | Sonnet | medium | M5-03 | 6.3、7.4、19.3；设计七 | app/tasks/router.py、app/tasks/service.py、app/tasks/schemas.py、tests/test\_tasks\_api.py；前端 src/components/ReRunDialog.vue、src/api/tasks.ts、src/views/ReviewResult.vue（只加按钮和弹窗挂载） | 设计第十一节重新审核部分各项有测试；对新接口重跑 A-06；`npm run build` 通过；浏览器中改立场后重新审核产生第 2 轮 | 已完成 |
| M5-05 | 结果页完整版：顶部信息栏、轮次下拉、覆盖说明卡片、失败与部分完成提示、删除；第三步分段进度；TERM\_IN\_PROMPT 提示 | Sonnet | high | M5-04 | 6.2 第三步、6.3、7.3、Q-60；设计八；frontend.md | src/views/ReviewResult.vue、src/views/ReviewNew.vue（只改第三步进度）、src/components/CoverageCard.vue、src/api/runs.ts、src/api/redaction.ts（只加错误码文字）、src/utils/labels.ts | `npm run build` 通过；按 frontend.md 第十一节自检；浏览器中检查轮次切换、覆盖说明、failed / partial 显示、分段进度；界面不出现“审核通过”“无风险” | 已完成 |
| M5-06 | 审核要求抽屉与 GET /requirements | Sonnet | medium | M5-05 | 6.2、6.5、19.5；设计九；frontend.md | app/review/requirements.py、app/review/router.py、tests/test\_requirements.py、tests/test\_runs\_api.py；前端 src/components/RequirementsDrawer.vue、src/api/requirements.ts、src/views/ReviewNew.vue（只加入口链接）、src/components/ConfirmStep.vue（只加入口链接） | 接口参数校验、类型为空、公司补充要求筛选各有测试；`npm run build` 通过；浏览器中第一步、第二步打开抽屉内容正确 | 已完成 |
| M5-07 | 禁用词检查测试（A-08） | Haiku | low | M5-06 | A-08、Q-58；设计十 | tests/test\_forbidden\_words.py | 测试通过；扫描范围与排除规则与设计第十节一致 | 已完成 |
| M5-08 | M5 联调与验收：A-02、A-05、A-07、A-08、T-12、T-13；回归 M2～M4 验收用例；浏览器检查结果页 | Opus | medium | M5-07 | 22、23；设计十一 | 按需 | 验收用例全部通过；验收结果汇报给用户 | 已完成 |

### M6 本地校验、报告与追问（验收：A-01、T-04、T-05、T-15、T-19）

**开工前必读 [docs/design/M6.md](design/M6.md)**：模块划分、函数签名、接口形状都以它为准；前端任务同时遵守 [docs/design/frontend.md](design/frontend.md)。下表“文件范围”中的路径相对于 `backend/`（前端任务相对于 `frontend/`），“设计”列指向 M6.md 的章节。M6-00 调整了顺序：追问后端（Opus）移到本地校验和报告之后，使 M6-01～M6-06 可由 Sonnet 档连续执行。

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC / 设计 | 文件范围 | 完成标准 | 状态 |
|-|-|-|-|-|-|-|-|-|
| M6-00 | 里程碑细化：模块划分、接口约定、任务拆分 | Opus | high | M5-08 | 22；— | docs/design/M6.md、docs/TASKS.md、docs/open\_questions.md | 用户确认 | 已完成 |
| M6-01 | 本地校验基础：公共结构、数字解析、报价表识别、L-01 / L-02 / L-03 / L-INFO；A-01 样本 | Sonnet | high | M6-00 | 10.1、10.2、Q-81～Q-83；设计二、3.1、3.2、九 | app/checks/model.py、numbers.py、amounts.py、tests/test\_checks\_numbers.py、tests/test\_checks\_amounts.py、tests/fixtures/checks/（make\_check\_fixtures.py、manifest.yaml、生成的样本） | 3.1、3.2 每条规则有正反两例测试；全部计算用 Decimal、ROUND\_HALF\_UP；每条 ai\_summary 满足 4.1（recheck 为空、不含 detail 中的金额数字）；样本中 L-01～L-03 的 manifest 完全相等 | 已完成 |
| M6-02 | L-04 大小写一致；L-05 跨文件金额、L-06 币种一致 | Sonnet | high | M6-01 | 10.2、9.6、Q-72、Q-84；设计 3.3、3.4 | app/checks/amounts.py（只加 L-04）、app/checks/consistency.py、tests/test\_checks\_amounts.py、tests/test\_checks\_consistency.py、tests/fixtures/checks/ | T-05 的单元测试；L-05 同币种不一致、币种未知、主文件自身报价表不参与；L-06 报出后不再报 L-05；4.1 约束；manifest 中 L-04～L-06 完全相等 | 已完成 |
| M6-03 | 日期解析与 L-07 报价有效期、L-08 交期与签约日 | Sonnet | medium | M6-02 | 10.2、Q-85、Q-86；设计 3.5 | app/checks/dates.py、tests/test\_checks\_dates.py、tests/fixtures/checks/ | 3.5 所列每种写法可解析、不确定写法与不存在的日期返回 None；L-07 以传入的 today 判断；多个签约日期不做 L-08；manifest 中 L-07、L-08 完全相等 | 已完成 |
| M6-04 | 校验结果写入与外发接入：evaluate / run\_checks、parse 中调用（含不外发）、GET /tasks/{id}/local-findings、【本地校验摘要】、汇总行“本地校验：…” | Sonnet | high | M6-03 | 7.1、9.7、10.3、11.2、19.4；设计一、3.6、四、五（接口部分） | app/checks/service.py、app/checks/router.py、app/main.py（只加挂载）、app/tasks/pipeline.py（只加调用）、app/redaction/quotes.py（只改 summary\_text）、app/redaction/engine.py（只改 build\_outbound 传 note）、app/review/service.py（只改 build\_context 的 local\_summary）、tests/test\_checks\_service.py、tests/test\_local\_findings\_api.py，以及因汇总行文字变化需更新期望值的现有测试 | local\_only 任务也写 local\_findings 且模型调用为 0；detail 加密存储；接口他人 404（A-06 补充）；T-04 后端部分（接口返回真实数字，llm\_calls.request\_text 中不含该行金额）；汇总行 note 各分支有测试；回归集与 A-03 测试仍通过 | 已完成 |
| M6-05 | 本地校验前端：第二步折叠面板、结果页“本地校验”标签页、不外发任务的结果页 | Sonnet | medium | M6-04 | 6.2 第 7 项、6.3、10.3；设计五；frontend.md | src/api/localFindings.ts、src/components/LocalFindings.vue、src/components/ConfirmStep.vue（只加折叠项）、src/views/ReviewResult.vue（只加标签页和不外发任务的表格）、src/utils/labels.ts | `npm run build` 通过；按 frontend.md 第十一节自检；浏览器中检查第二步面板、结果页标签页、不外发任务、点击定位 | 已完成 |
| M6-06 | Word 报告导出：report\_docx.py、两个下载接口、审计日志；结果页“导出报告”按钮、审核历史“导出” | Sonnet | medium | M6-05 | 14、17.7、19.5、Q-87；设计六；frontend.md | app/export/report\_docx.py、app/export/router.py、app/main.py（只加挂载）、app/review/service.py（只在需要时把位置文字函数提为公共函数）、tests/test\_report\_docx.py、tests/test\_report\_api.py；前端 src/api/report.ts、src/views/ReviewResult.vue（只加按钮）、src/views/History.vue（只加导出） | T-19（结构逐节、名称已还原且无代号、固定声明、无禁用词）；不外发任务报告；清空 text\_enc 后仍可导出；文件名编码；他人 404、未完成轮次 409；审计日志 report\_export；`npm run build` 通过；浏览器中下载一次 | 已完成 |
| M6-07 | 追问后端：问题脱敏、预览、经网关发送（网关加 timeout\_s、retry 参数）、回复还原 | Opus | high | M6-06 | 13、9.3、9.8、19.6、Q-88～Q-90；设计七 | app/chat/service.py、app/chat/router.py、app/main.py（只加挂载）、app/llm/gateway.py（只加两个关键字参数）、app/review/prompt.py（只加 chat\_system\_prompt，如放在这里）、tests/test\_chat.py、tests/test\_gateway.py | 设计第七节测试列表全部通过（含 T-15、A-06、日志安全）；网关原有测试不改 | 已完成 |
| M6-08 | 追问前端 ChatPanel.vue | Sonnet | medium | M6-07 | 6.3、13；设计八；frontend.md | src/components/ChatPanel.vue、src/api/chat.ts、src/views/ReviewResult.vue（只加按钮和抽屉挂载） | `npm run build` 通过；按 frontend.md 第十一节自检；无 v-html；浏览器中检查 T-15 弹窗、“不再提示”、失败后可重试、不可用时按钮禁用 | 已完成 |
| M6-09 | M6 联调与验收：A-01、T-04、T-05、T-15、T-19；回归 M2～M5 验收用例；浏览器检查 | Opus | medium | M6-08 | 22、23；设计十 | 按需 | 验收用例全部通过；验收结果汇报给用户 | 已完成 |

### M7 设置与数据（验收：T-14、T-16、T-18）

**开工前必读 [docs/design/M7.md](design/M7.md)**：模块划分、函数签名、接口形状都以它为准；前端任务同时遵守 [docs/design/frontend.md](design/frontend.md)。下表“文件范围”中的路径相对于 `backend/`（前端任务相对于 `frontend/`），“设计”列指向 M7.md 的章节。每个任务的管理接口都按设计第二节写审计；`app/main.py` 各任务只加自己那一行 include，`src/api/admin.ts` 由 M7-02 新建、之后各任务只追加自己的函数。

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC / 设计 | 文件范围 | 完成标准 | 状态 |
|-|-|-|-|-|-|-|-|-|
| M7-00 | 里程碑细化：模块划分、接口约定、备份文件格式、任务拆分 | Opus | high | M6-09 | 22；— | docs/design/M7.md、docs/TASKS.md、docs/open\_questions.md | 用户确认 | 已完成 |
| M7-01 | 备份：文件格式与分块流式加密 / 解密、pg\_dump、tar、.tmp → fsync → .sha256 → 重命名、轮换、备份记录、advisory lock、backup 处理函数与定时检查、decrypt-backup 命令 | Opus | xhigh | M7-00 | 16.1–16.3、20.3、Q-100～Q-104；设计第三节 | app/backup/archive.py、service.py、job.py、app/worker.py（只加一行导入）、app/db/jobs.py（只在 finalize\_failed 加 backup 分支）、app/cli.py（只加 decrypt-backup）、tests/test\_backup\_archive.py、tests/test\_backup.py | 设计 3.5 每一项都有测试（含 T-16：写入期间只有 .tmp、完成后 .bak + .sha256、轮换只删命名匹配的最旧文件）；解密后 `pg_restore --list` 成功；篡改、截断、调换、错密码全部报错；备份密码和数据库密码不出现在命令行参数、日志、记录中 | 已完成 |
| M7-02 | 账号管理：/admin/users 四个接口、Users.vue | Sonnet | medium | M7-01 | 15.1、17.1、19.7、Q-99；设计二、四、十一；frontend.md | app/admin/users.py、app/main.py（只加 include）、tests/test\_admin\_users.py；前端 src/api/admin.ts（新建）、src/views/settings/Users.vue、src/utils/labels.ts（只加角色、状态） | 设计第四节每个错误码和行为都有测试；最后管理员并发降级只有一次成功；审计符合第二节；`npm run build` 通过；按 frontend.md 第十一节自检，浏览器中新增、编辑、停用、重置密码各做一次 | 已完成 |
| M7-03 | 模型设置：读写（密钥加密、只返回末 4 位）、测试连接、用量；Model.vue | Sonnet | medium | M7-02 | 11.1、15.2、19.7、Q-12、Q-105、Q-106；设计二、五、十一 | app/admin/model.py、app/main.py（只加 include）、tests/test\_admin\_model.py；前端 src/api/admin.ts（只追加）、src/views/settings/Model.vue | 设计第五节每条校验的边界有测试；完整密钥不出现在任何响应、日志、审计、llm\_calls 中；测试连接用模拟服务覆盖成功 / 未配置 / HTTP 错误；单价全程 Decimal；`npm run build` 通过；浏览器中保存一次并测试连接（模拟模型服务） | 已完成 |
| M7-04 | 公司补充要求、公司说明、版本历史；序列迁移；GET /requirements 参数可选；Requirements.vue | Sonnet | high | M7-03 | 15.3、18.21、18.22、19.5、19.7、T-14、Q-107、Q-108、Q-113；设计二、六、十一 | app/admin/requirements.py、app/main.py（只加 include）、app/db/models.py（只声明序列）、app/db/alembic/versions/0002\_company\_requirement\_code\_seq.py、app/review/router.py（只把 stance、source 改为可选）、app/review/service.py（只让 company\_rows 接受 stance=None）、tests/test\_admin\_requirements.py、tests/test\_requirements.py、tests/test\_migrations.py（只在需要时补序列检查）；前端 src/api/admin.ts（只追加）、src/views/settings/Requirements.vue | T-14 后端全链路（新增 → GET /requirements → 确认发送 → requirement\_results 有该条 → company\_req\_version 为新版本 → 报告写出版本号）；30 条上限、编号不复用、REQ\_TEXT\_SENSITIVE、每种修改都产生新版本；模型与迁移一致的测试通过；抽屉原有行为不变；`npm run build` 通过；浏览器中新增、编辑、停用、删除、保存公司说明、查看历史版本 | 已完成 |
| M7-05 | 脱敏设置：数量脱敏开关、自定义敏感词、已记住的名称；Redaction.vue | Sonnet | medium | M7-04 | 9.10、15.4、18.12、19.7、Q-110；设计二、七、十一 | app/admin/redaction.py、app/main.py（只加 include）、tests/test\_admin\_redaction.py；前端 src/api/admin.ts（只追加）、src/views/settings/Redaction.vue | 设计第七节测试列表全部通过；词条文字不出现在日志和审计中；`npm run build` 通过；浏览器中开关、增删启停自定义词、搜索与删除已记住的名称 | 已完成 |
| M7-06 | 原件保留期清理：retention 处理函数与定时检查；不外发任务写 last\_completed\_at；原件删除后禁止重新审核和追问 | Sonnet | high | M7-05 | 16.4、17.8、T-18、Q-109；设计第八节 | app/backup/retention.py、app/worker.py（只加一行导入）、app/tasks/pipeline.py（只在 completed\_local 处写 last\_completed\_at）、app/tasks/service.py（只改 patch\_task 的检查）、app/tasks/schemas.py（只给 TaskFileOut 加 original\_deleted\_at）、app/chat/service.py（只改 availability）、tests/test\_retention.py、tests/test\_tasks\_api.py、tests/test\_chat.py；前端 src/views/ReviewResult.vue（只改“重新审核”按钮的显示条件）、src/api/tasks.ts（只加字段类型） | T-18 后端部分（设计第八节测试列表）全部通过；已有的报告、预览测试不改仍通过；`npm run build` 通过 | 已完成 |
| M7-07 | 数据页：备份设置与立即备份、备份记录与空间、保留期设置、外发记录、系统状态；Data.vue | Sonnet | medium | M7-06 | 15.5、16.2、17.2、19.7、Q-111；设计二、九、十一 | app/admin/data.py、app/main.py（只加 include）、tests/test\_admin\_data.py；前端 src/api/admin.ts（只追加）、src/views/settings/Data.vue | 设计第九节测试列表全部通过；外发记录筛选的日期边界按 Asia/Shanghai；详情纯文本显示、无 v-html；`npm run build` 通过；浏览器中立即备份（compose 环境）并看到结果、筛选并打开一条外发记录 | 已完成 |
| M7-08 | 审计日志核对与补全；管理员提醒（/auth/me alerts） | Sonnet | medium | M7-07 | 6.7、16.3、17.7、Q-112；设计第十节 | app/admin/alerts.py、app/auth/router.py（只改 me 返回 alerts）、app/auth/schemas.py（只改 alerts 类型）、tests/test\_audit.py、tests/test\_admin\_alerts.py；前端 src/api/auth.ts（只给 AdminAlert 加 code）；17.7 中发现缺失的动作所在的文件（在报告中列出） | 17.7 每个动作都有测试证明会写入；全部审计 detail 不含敏感信息；三种提醒的边界有测试；普通用户 alerts 为空；`npm run build` 通过；浏览器中构造一种提醒并看到提醒条 | 已完成 |
| M7-09 | M7 联调与验收：T-14、T-16、T-18；A-06 补充；回归 M2～M6 验收用例；浏览器检查 5 个设置标签页与提醒条 | Opus | medium | M7-08 | 22、23；设计第十二节 | 按需 | 验收用例全部通过；验收结果汇报给用户 | 已完成 |

### M8 部署与测试（验收：全部用例，含 T-17）

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC | 状态 |
|-|-|-|-|-|-|-|
| M8-00 | 里程碑细化 | Opus | medium | M7-09 | 22 | 待细化 |
| M8-01 | restore.sh：校验 SHA-256、解密（必须与备份格式一致）、恢复数据库和原件 | Opus | xhigh | M8-00 | 16.3 | 待细化 |
| M8-02 | install.sh、upgrade.sh、backup\_now.sh | Sonnet | medium | M8-01 | 20.5 | 待细化 |
| M8-03 | 离线镜像构建与导出脚本：固定版本，打包 RapidOCR 模型 | Sonnet | medium | M8-02 | 20.5 | 待细化 |
| M8-04 | 中文运维说明：创建虚拟机、挂载备份盘、首次部署、升级、备份恢复、用 SFTP 拉取备份、常见问题 | Sonnet | medium | M8-03 | 20 | 待细化 |
| M8-05 | 全量回归、恢复演练、最终验收报告 | Opus | high | M8-04 | 23 | 待细化 |

## 五、工作量分布

| 模型 | 任务数 | 说明 |
|-|-|-|
| Opus 5.5 | 29 | 15 个是里程碑细化和验收，14 个是安全核心和难点 |
| Sonnet 5.5 | 42 | 大部分编码工作 |
| Haiku 5.5 | 7 | 资源文件、占位页、样本、简单测试 |

按顺序执行，整个项目大约要换 23 次模型，平均每个里程碑约 3 次。每个 Mx-00 和验收任务都会停下来等用户确认。
