# 开发任务清单

本文件是全部开发任务的唯一清单，按 SPEC 第 22 节的里程碑 M1–M8 拆分。

**执行方式：串行。** 同一时间只有一个会话在开发。任务严格按编号顺序执行（编号顺序已满足依赖关系，并尽量让相同模型的任务排在一起，减少换会话）。

## 当前进度

- M1 已验收并经用户确认（2026-10-15）
- M2 已细化（M2-00）并经用户确认，设计见 docs/design/M2.md；M2-01 至 M2-08 已完成
- 下一个任务：**M2-09**（Opus 5.5，推理强度 high）
- 试用 GPT-6.1 Sol（2026-10-15 用户同意）：M2-13 交给 GPT-6.1 Sol 试做，做完由 Opus 专门评审（规则 11），再由用户决定后续分工
- 上次 Opus 评审到的提交：3a46fe3（M2-01 至 M2-06 已在 M2-07 开工前评审，无阻塞问题；两处小问题见 M2-07 完成报告）

## 一、模型分工与推理强度

| 模型 | 负责什么 |
|-|-|
| Opus 5.5 | 每个里程碑开工前细化任务（Mx-00）；安全核心（加密、会话、权限、脱敏核心、出口复检、模型网关、追问脱敏、备份恢复）；算法难点（DOCX 自动编号、模型输出校验、分段合并）；评审 Sonnet 和 Haiku 的提交；里程碑验收 |
| Sonnet 5.5 | 规格写得清楚的常规实现：接口、页面、解析器、本地校验、报告、部署脚本 |
| Haiku 5.5 | 机械性、照抄型、低风险的任务：资源文件、占位页面、样本文件、简单的扫描测试 |
| GPT-6.1 Sol（试用） | 只做“模型”列标为“GPT-6.1 Sol（试用）”的任务（目前只有 M2-13），在 Codex 中执行，规则见仓库根目录的 AGENTS.md。安全核心、Mx-00 细化、评审和里程碑验收不交给它。试用通过后，由用户决定是否让它接手 Sonnet 档的任务 |

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
2. **开工前必读**：CLAUDE.md、本任务所在行、“SPEC”列列出的章节、docs/open\_questions.md。不需要通读整份 SPEC。
3. **在 main 分支上直接开发**，按小步骤提交。提交说明以任务编号开头，例如“M1-01 后端基础：锁定依赖版本”。
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
10. **Opus 的评审**：Opus 会话开始一个任务前，先评审“上次 Opus 评审到的提交”之后，Sonnet、Haiku 和 GPT-6.1 Sol 的全部提交（用 `git log` 和 `git diff` 查看）。有问题的，先修复，或在报告中列出交给用户决定。评审完更新“上次 Opus 评审到的提交”。
11. **GPT-6.1 Sol 试用任务的评审**：试用任务完成后停下，不论下一个任务是谁做。用户开一个 Opus 会话专门评审该任务的提交，逐条检查：只改了文件范围内的文件；未改 SPEC、CLAUDE.md 和设计文档；完成标准逐条有测试覆盖；SPEC 没写明的细节记入了 open\_questions；日志和错误消息不含原文、名称、金额、密钥；所有任务接口校验 owner\_id（他人返回 404）；Docker 中全部测试通过；代码风格与周围一致。评审结论和建议的后续分工汇报给用户，由用户决定。

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
| M2-09 | 出口复检与还原 | Opus | high | M2-08 | 9.8、9.9、Q-19；设计六 | app/redaction/recheck.py、app/redaction/restore.py、tests/test\_recheck.py、tests/test\_restore.py | 混入任一真实值、已记住名称、自定义敏感词或未替换的电话 / 邮箱 / 账号 / 证件号 / 带币种金额时拦截；拦截原因只含类别和位置；短数字不误拦；还原长代号优先 | 待执行 |
| M2-10 | 模型网关、设置读取、用量统计、模拟模型服务、开发用 set-model 命令 | Opus | high | M2-09 | 5、7.5、11.1、11.7、15.2、17.4、Q-12；设计七 | app/llm/gateway.py、app/llm/usage.py、app/admin/settings\_store.py、app/cli.py（set-model 部分）、tests/mock\_llm.py、tests/test\_gateway.py、tools/mock\_llm\_server.py、deploy/docker-compose.mock.yml | 执行顺序为上限检查 → 出口复检 → 发送 → 记录；T-07 的网关部分通过（blocked 记录不含真实值）；达到上限不发送；重试次数和间隔正确；附加请求参数原样合并；API 密钥不出现在日志和 llm\_calls 中 | 待执行 |
| M2-11 | 审核核心：拼接提示词、输出结构、结果校验（11.4 全部 9 条）、解析失败重试 1 次 | Opus | xhigh | M2-10 | 11.2–11.4、A-07；设计八 | app/review/prompt.py、schema.py、validate.py、tests/test\_review\_validate.py、tests/test\_review\_prompt.py | T-10、T-11 通过；要求结论数 = 发送的要求数（A-07）；document 标签被转义；类别、严重程度映射与兜底正确；重复问题合并 | 待执行 |
| M2-12 | review 处理函数与轮次接口：确认发送（含预检）、轮次列表、轮次详情（还原真实名称）、修改问题处理状态 | Sonnet | high | M2-11 | 7.1–7.3、19.5、A-04；设计八 | app/review/router.py、service.py、job.py、app/main.py（只加 include\_router 一行）、tests/test\_runs\_api.py、tests/test\_review\_job.py | 设计八的每个 409 错误码都有测试；local\_only 任务模型调用次数为 0（A-04）；网关拦截时 run=blocked 且任务回到 awaiting\_confirm（T-07）；返回给前端的文字已还原；对真实接口重跑 A-06 | 待执行 |
| M2-13 | 脱敏确认接口：签约方查询与修改、脱敏摘要、外发文本预览、修改文件类型 | GPT-6.1 Sol（试用） | medium | M2-12 | 6.2 第二步、19.3、19.4；设计九 | app/redaction/router.py、app/tasks/router.py（只加 PATCH 文件类型）、app/main.py（只加 include\_router 一行）、tests/test\_redaction\_api.py | 必须恰好一方为我方；非 awaiting\_confirm 时修改返回 409；外发文本预览是代号版本；对真实接口重跑 A-06 | 待执行 |
| M2-14 | 前端：新建审核第一步（上传与设置）、第三步（审核中轮询）、枚举翻译、multipart 请求 | Sonnet | medium | M2-13 | 6.2；设计十 | src/api/http.ts（加 postForm）、src/api/tasks.ts、src/utils/labels.ts、src/views/ReviewNew.vue | 上传前在前端检查数量、大小、扩展名；立场和来源默认为该用户上次的选择；轮询每 3 秒，离开页面停止；`npm run build` 通过 | 待执行 |
| M2-15 | 前端：第二步基础版（文件信息与类型修改、签约方确认、脱敏摘要、查看将发送的文本、确认发送） | Sonnet | medium | M2-14 | 6.2 第二步；设计九、十 | src/views/ReviewNew.vue、src/api/redaction.ts、src/api/runs.ts、src/components/ 下新增的第二步子组件 | 未选我方不能提交；409 错误按错误码给出中文提示；外发文本纯文本显示；`npm run build` 通过 | 待执行 |
| M2-16 | 前端：结果页基础版（顶部信息栏、问题清单 FindingCard、审核要求逐条结论标签页）、审核历史页 | Sonnet | medium | M2-15 | 6.3、6.4；设计八、十 | src/views/ReviewResult.vue、src/views/History.vue、src/components/FindingCard.vue | 问题卡片字段与 6.3 一致，可直接替换的措辞带复制按钮，处理状态和备注可保存；历史页搜索、日期筛选、分页、删除（二次确认）可用；界面不出现“审核通过”“无风险”；`npm run build` 通过 | 待执行 |
| M2-17 | 脱敏回归测试集：至少 5 份虚构样本（合同、报价单、订单、询价函、函件各一，中英文混合）、标注文件、自动化测试 | Sonnet | high | M2-16 | 23.3；设计十一 | tests/fixtures/redaction\_golden/\*、tests/test\_redaction\_golden.py | must\_replace 全部不在外发文本中（召回率 100%）；must\_keep 全部仍在；M2 范围内只标注第 1–5 步的类别（人名、地址留到 M4） | 待执行 |
| M2-18 | 审核效果评测脚本 | Sonnet | medium | M2-17 | 23.3；设计七、八 | tools/eval\_review.py、tools/README.md | 对指定目录下的样本走完 parse → 脱敏 → 审核（经网关），输出每个样本的要求结论统计和问题清单到 Markdown 文件；可用 `--extra-params` 临时覆盖附加请求参数（对比思考模式）；只能在开发环境运行 | 待执行 |
| M2-19 | M2 联调与验收：用模拟模型服务走通全流程；用户提供 DeepSeek 测试密钥后，用虚构样本调用真实模型试跑，对比思考与非思考模式 | Opus | high | M2-18 | 22、23、Q-12 | 按需 | A-04、T-01、T-02、T-07、T-09、T-10、T-11 通过；评测结果汇报给用户 | 待执行 |

### M3 解析补全与预览（验收：T-03）

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC | 状态 |
|-|-|-|-|-|-|-|
| M3-00 | 里程碑细化 | Opus | medium | M2-19 | 22 | 待细化 |
| M3-01 | 虚构样本：文字版 PDF、扫描版 PDF（用 Pillow 生成图片再转 PDF）、JPG、PNG、XLSX 报价单 | Haiku | medium | M3-00 | 23 | 待细化 |
| M3-02 | PDF 文字版解析：段落块、表格、bbox、去除表格区域内重复的段落块、页数上限 | Sonnet | high | M3-01 | 8.5、8.7、Q-16 | 待细化 |
| M3-03 | OCR：集成 RapidOCR、用 pypdfium2 渲染扫描页、合并文本块、低置信标记、OCR 页数上限、同一时间只跑 1 个 OCR 任务、模型文件打包进镜像 | Sonnet | high | M3-02 | 8.5、8.7、21 | 待细化 |
| M3-04 | XLSX 解析：两次读取（缓存值和公式）、合并单元格、隐藏工作表、公式无缓存值时警告、单元格数上限 | Sonnet | medium | M3-03 | 8.4、8.7 | 待细化 |
| M3-05 | 原文预览：节点接口和原件接口；DocPreview.vue（按节点渲染、用 pdfjs 叠加高亮、图片用 img 叠加高亮、点击问题定位） | Sonnet | high | M3-04 | 6.3 右栏、19.3、Q-17 | 待细化 |
| M3-06 | M3 联调与验收 | Opus | medium | M3-05 | 23 | 待细化 |

### M4 脱敏完善（验收：A-03、T-06、T-08）

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC | 状态 |
|-|-|-|-|-|-|-|
| M4-00 | 里程碑细化 | Opus | high | M3-06 | 22 | 待细化 |
| M4-01 | 手动补充：同一文字全文替换，重新生成外发文本，保持代号一致 | Opus | high | M4-00 | 6.2 第 5 项、19.4 | 待细化 |
| M4-02 | 人名与地址识别（9.3 第 6、7 步）；国家和企业类型判断 | Sonnet | high | M4-01 | 9.3、9.4 | 待细化 |
| M4-03 | 疑似遗漏：识别规则和处理接口 | Sonnet | medium | M4-02 | 9.5、19.4 | 待细化 |
| M4-04 | 金额比例和报价明细表汇总（用 Decimal 计算） | Sonnet | high | M4-03 | 9.6、9.7、Q-18 | 待细化 |
| M4-05 | 已记住的名称：写入、匹配、命中计数 | Sonnet | medium | M4-04 | 9.10、18.12 | 待细化 |
| M4-06 | 前端第二步完整版：处理疑似遗漏、在原文中选中文字设为敏感信息 | Sonnet | medium | M4-05 | 6.2 第二步 | 待细化 |
| M4-07 | 扩充脱敏回归测试集；验证 A-03；增加日志安全测试（日志中不出现原文、名称、金额、密钥）；M4 验收 | Opus | high | M4-06 | 17.7、23 | 待细化 |

### M5 AI 审核完善（验收：A-02、A-05、A-07、A-08、T-12、T-13）

| 编号 | 任务 | 模型 | 强度 | 依赖 | SPEC | 状态 |
|-|-|-|-|-|-|-|
| M5-00 | 里程碑细化 | Opus | high | M4-07 | 22 | 待细化 |
| M5-01 | 分段审核与结论合并 | Opus | xhigh | M5-00 | 11.5 | 待细化 |
| M5-02 | 覆盖说明 coverage.py | Sonnet | medium | M5-01 | 11.6 | 待细化 |
| M5-03 | 重新审核：修改立场、来源和补充说明，接口和前端弹窗 | Sonnet | medium | M5-02 | 7.4、19.3 | 待细化 |
| M5-04 | 调用上限和失败提示：前端显示各种失败原因 | Sonnet | low | M5-03 | 7.3、7.5 | 待细化 |
| M5-05 | 结果页完整版：顶部信息栏、轮次切换、覆盖说明卡片、筛选、处理状态和备注、点击问题定位 | Sonnet | high | M5-04 | 6.3 | 待细化 |
| M5-06 | 审核要求抽屉和 GET /requirements 接口 | Haiku | medium | M5-05 | 6.5、19.5 | 待细化 |
| M5-07 | 禁用词扫描测试：界面和报告模板中不出现“审核通过”“无风险” | Haiku | low | M5-06 | A-08 | 待细化 |
| M5-08 | M5 联调与验收 | Opus | medium | M5-07 | 23 | 待细化 |

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
| Opus 5.5 | 28 | 15 个是里程碑细化和验收，13 个是安全核心和难点 |
| Sonnet 5.5 | 43 | 大部分编码工作 |
| Haiku 5.5 | 7 | 资源文件、占位页、样本、简单测试 |

按顺序执行，整个项目大约要换 23 次模型，平均每个里程碑约 3 次。每个 Mx-00 和验收任务都会停下来等用户确认。
