# 开发任务清单

本文件是全部开发任务的唯一清单，按 SPEC 第 22 节的里程碑 M1–M8 拆分。

**执行方式：串行。** 同一时间只有一个会话在开发。任务严格按编号顺序执行（编号顺序已满足依赖关系，并尽量让相同模型的任务排在一起，减少换会话）。

## 当前进度

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
- 上次 Opus 评审到的提交：e1634bd（2026-10-20 评审 M5-03（Codex）：fixtures 生成脚本、manifest 与新样本超出原文件范围并改了表中文件范围（测试样本，可接受）；日志与覆盖说明不含原文；发现并修复：覆盖说明“无法判断”的原因未还原代号（与审核要求列表不一致）、A-05 测试对解析警告的断言过弱、Q-79 记在了第四节）。更早：fd99eaf（2026-10-19 M4-07 开工前评审 M4-06（Sonnet）：只改了文件范围内的文件；上下文与原文按纯文本插值，无 v-html；选择位置按码点计算、跨节点和已替换的选择有提示；未发现问题）。更早：2d09adc（2026-10-19 M4-04 开工前评审 M4-01 至 M4-03（Sonnet）：M4-02 改了范围外的 parties.py（只给 _name_start_after_boundary 加 min_han 参数，行为不变，可接受）；疑似遗漏、比例、报价汇总均为纯函数且不写日志；发现并在 M4-04 中修复：“法定代表人（签字）：”后的人名漏识别、“Order No”“Account No”等英文标签被列为疑似遗漏（Q-76 ⑥）；Q-75 ③ 的已知代价（名称含单个边界字时前半留在原文且不会被列为疑似遗漏）交用户决定）。更早：aaa2af7（2026-10-19 M4-00 开工前检查：f227c08 之后只有 M3-09 的 Opus 提交，无需评审的提交）。更早：f227c08（2026-10-19 M3-09 开工前评审 M3-06 至 M3-08（Sonnet）：只改了文件范围内的文件；预览接口经 owned_task_file 校验归属，raw 响应头符合 Q-63；前端原件先取 Blob，无 v-html，滚动只在预览容器内；发现 DocPreview 手写编号重复显示，已在 M3-09 修复；无阻塞问题）。更早：e43e63f（2026-10-19 M3-05 开工前评审 M3-01 至 M3-04（Sonnet）：只改了文件范围内的文件（M3-04 按规则 6 追加 Q-67），解析失败消息不含原文，日志只写页数、块数和耗时，onnxruntime 遥测在建引擎前关闭，无阻塞问题。更早：94cd950（2026-10-17 评审 M2-18（GPT-6.1 Sol）：只改了文件范围内的文件，完成标准有测试覆盖，Q-57 已记录，输出不含原文和密钥，按 README 实跑可用，无阻塞问题。此前 2026-10-16 已评审 M2-12 至 M2-17 及 Q-52）

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
| M5-04 | 重新审核：PATCH /tasks/{id}、整体重算、ReRunDialog 弹窗与结果页按钮 | Sonnet | medium | M5-03 | 6.3、7.4、19.3；设计七 | app/tasks/router.py、app/tasks/service.py、app/tasks/schemas.py、tests/test\_tasks\_api.py；前端 src/components/ReRunDialog.vue、src/api/tasks.ts、src/views/ReviewResult.vue（只加按钮和弹窗挂载） | 设计第十一节重新审核部分各项有测试；对新接口重跑 A-06；`npm run build` 通过；浏览器中改立场后重新审核产生第 2 轮 | 待执行 |
| M5-05 | 结果页完整版：顶部信息栏、轮次下拉、覆盖说明卡片、失败与部分完成提示、删除；第三步分段进度；TERM\_IN\_PROMPT 提示 | Sonnet | high | M5-04 | 6.2 第三步、6.3、7.3、Q-60；设计八；frontend.md | src/views/ReviewResult.vue、src/views/ReviewNew.vue（只改第三步进度）、src/components/CoverageCard.vue、src/api/runs.ts、src/api/redaction.ts（只加错误码文字）、src/utils/labels.ts | `npm run build` 通过；按 frontend.md 第十一节自检；浏览器中检查轮次切换、覆盖说明、failed / partial 显示、分段进度；界面不出现“审核通过”“无风险” | 待执行 |
| M5-06 | 审核要求抽屉与 GET /requirements | Sonnet | medium | M5-05 | 6.2、6.5、19.5；设计九；frontend.md | app/review/requirements.py、app/review/router.py、tests/test\_requirements.py、tests/test\_runs\_api.py；前端 src/components/RequirementsDrawer.vue、src/api/requirements.ts、src/views/ReviewNew.vue（只加入口链接）、src/components/ConfirmStep.vue（只加入口链接） | 接口参数校验、类型为空、公司补充要求筛选各有测试；`npm run build` 通过；浏览器中第一步、第二步打开抽屉内容正确 | 待执行 |
| M5-07 | 禁用词检查测试（A-08） | Haiku | low | M5-06 | A-08、Q-58；设计十 | tests/test\_forbidden\_words.py | 测试通过；扫描范围与排除规则与设计第十节一致 | 待执行 |
| M5-08 | M5 联调与验收：A-02、A-05、A-07、A-08、T-12、T-13；回归 M2～M4 验收用例；浏览器检查结果页 | Opus | medium | M5-07 | 22、23；设计十一 | 按需 | 验收用例全部通过；验收结果汇报给用户 | 待执行 |

### M6 本地校验、报告与追问（验收：A-01、T-04、T-05、T-15、T-19）

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC | 状态 |
|-|-|-|-|-|-|-|
| M6-00 | 里程碑细化 | Opus | high | M5-08 | 22 | 待细化 |
| M6-01 | 追问对话后端：用户问题先脱敏、预览确认、经网关发送、回复还原 | Opus | high | M6-00 | 13、19.6 | 待细化 |
| M6-02 | 报价表识别和 L-01、L-02、L-03 | Sonnet | high | M6-01 | 10.1、10.2 | 待细化 |
| M6-03 | 中文大写金额解析和 L-04；跨文件和币种校验 L-05、L-06 | Sonnet | high | M6-02 | 10.2 | 待细化 |
| M6-04 | 日期解析和 L-07、L-08 | Sonnet | medium | M6-03 | 10.2 | 待细化 |
| M6-05 | 本地校验结果写入、接口和前端面板（第二步折叠面板、结果页标签页） | Sonnet | medium | M6-04 | 10.3、19.4 | 待细化 |
| M6-06 | Word 报告导出 | Sonnet | medium | M6-05 | 14 | 待细化 |
| M6-07 | 前端追问面板 ChatPanel.vue | Sonnet | medium | M6-06 | 13 | 待细化 |
| M6-08 | M6 联调与验收 | Opus | medium | M6-07 | 23 | 待细化 |

### M7 设置与数据（验收：T-14、T-16、T-18）

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC | 状态 |
|-|-|-|-|-|-|-|
| M7-00 | 里程碑细化 | Opus | high | M6-08 | 22 | 待细化 |
| M7-01 | 备份：pg\_dump、打包、分块流式加密、先写 .tmp 再重命名、生成 .sha256、轮换删除、备份记录、定时触发 | Opus | xhigh | M7-00 | 16.1–16.3 | 待细化 |
| M7-02 | 账号管理：接口和 Users.vue | Sonnet | medium | M7-01 | 15.1、19.7 | 待细化 |
| M7-03 | 模型设置：密钥加密保存且只显示末 4 位、测试连接、用量；接口和 Model.vue | Sonnet | medium | M7-02 | 15.2、19.7 | 待细化 |
| M7-04 | 公司补充要求、公司说明、版本历史；接口、Requirements.vue；审核时纳入 CO 编号的要求 | Sonnet | medium | M7-03 | 15.3、19.7 | 待细化 |
| M7-05 | 脱敏设置：数量脱敏开关、自定义敏感词、已记住的名称；接口和 Redaction.vue | Sonnet | medium | M7-04 | 15.4、19.7 | 待细化 |
| M7-06 | 原件保留期清理 | Sonnet | high | M7-05 | 16.4 | 待细化 |
| M7-07 | 数据页：备份设置、外发记录、系统状态；接口和 Data.vue | Sonnet | medium | M7-06 | 15.5、19.7 | 待细化 |
| M7-08 | 审计日志补全到所有规定的动作；管理员提醒条 | Sonnet | medium | M7-07 | 6.7、17.7 | 待细化 |
| M7-09 | M7 联调与验收 | Opus | medium | M7-08 | 23 | 待细化 |

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
