# V2-C 系统与数据

2026-10-23 · Opus 5.5（V2-C-00） · 用户已确认（2026-10-23）

范围见 [PLAN.md](../PLAN.md) 第三节 C1～C7；执行规则见 [TASKS-V2.md](../TASKS-V2.md) 第一节。本文件是 V2-C 各任务之间的接口契约，任务卡自包含。规格变更汇总在 [SPEC-V2.md](../SPEC-V2.md) C 部分。

## 〇、共同约定

1. **开工条件**：V2-B 已通过批次审核（V2-B-09）。M8 的部署脚本（install.sh、upgrade.sh、backup\_now.sh、restore.sh、lib.sh）已在 V1.0 中提交。
2. **测试**：每节测试用例逐条写成测试，不得删改；单个任务只跑列出的测试文件。涉及数据删除（V2-C-02）与加密（V2-C-07）的任务为测试先行，审核时重点核对。
3. **迁移编号**：V2-B 用到 0011。本批 V2-C-02 用 0012，V2-C-06 用 0013。
4. **升级安全**：从 v1.0.0 升级到 V2 时，任何会删除数据的新规则都不得因为“默认值变化”而在升级当天自动删除老数据（见 2.6）。
5. 日志不含原文、名称、金额、密钥；提交只暂存本任务文件。

## 一、分类并发与连接池（V2-C-01）

**文件范围**：`backend/app/worker.py`、`backend/app/db/jobs.py`、`backend/app/config.py`、`backend/app/db/session.py`、`deploy/docker-compose.yml`（只改 worker 的环境变量）、`deploy/.env.example`、对应测试（test\_worker.py、test\_jobs.py、test\_config.py）。

1. `jobs.claim(db, worker_id, types: tuple[str, ...])`：只取 `type in types` 的任务，其余不变（FOR UPDATE SKIP LOCKED、按 id 先后）。
2. worker 启动三组线程：审核组 `WORKER_REVIEW_CONCURRENCY`（默认 8）条线程只取 review；解析组 `WORKER_PARSE_CONCURRENCY`（默认 2）只取 parse；维护组 1 条线程只取 backup、retention。OCR 仍由 `ocr_slot` 互斥（同一时间 1 个）。两个变量取值 1～32，超出范围启动失败并说明。`WORKER_CONCURRENCY` 不再使用：仍被设置时启动日志警告一次“已改为 WORKER\_REVIEW\_CONCURRENCY / WORKER\_PARSE\_CONCURRENCY”。
3. 连接池：worker 的 `create_engine(pool_size=审核+解析+2, max_overflow=审核+解析+2)`（每个任务线程还有心跳线程和网关写外发记录的独立会话）；app 保持默认。`session.get_engine` 接受这两个参数，worker 启动时传入。
4. 排队位置（`GET /tasks/{id}`）按同类型排队计算，不变。
5. compose 与 .env.example：worker 增加两个变量（默认 8、2），删除 WORKER\_CONCURRENCY。

| # | 场景 | 期望 |
|-|-|-|
| 1 | 队列中先有 3 个 parse、后有 1 个 review，解析组 2 条线程都忙 | review 立即被审核组取走，不等 parse |
| 2 | `claim(types=("review",))` | 不会取到 parse / backup |
| 3 | 默认配置 | 审核组 8、解析组 2、维护组 1 条线程 |
| 4 | `WORKER_REVIEW_CONCURRENCY=0` 或 `abc` | 启动失败，提示取值范围 |
| 5 | 设置了 `WORKER_CONCURRENCY` | 启动成功，日志警告一次 |
| 6 | 8 个审核线程同时运行（模拟网关延迟 2 秒） | 无连接池超时；全部完成 |
| 7 | 停止信号 | 三组线程都在 30 秒内结束或保持 running 由心跳超时重排（Q-38 不变） |

## 二、数据保留四层（V2-C-02，测试先行）

**文件范围**：`backend/app/backup/retention.py`、`backend/app/admin/settings_store.py`、`backend/app/admin/data.py`、`backend/app/tasks/service.py`（删除任务时清空外发记录文件名；活动时间）、`backend/app/review/job.py` 与 `backend/app/chat/service.py`（只更新活动时间）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0012_retention_layers.py`（新）、`backend/app/admin/alerts.py`、对应测试。

### 2.1 设置项

| key | 含义 | 可选值（天，0 表示永久） | 新安装默认 |
|-|-|-|-|
| retention.original\_days | 原件与原文（原件文件、节点原文、补充说明原文） | 30 / 90 / 180 / 365 / 0 | 30 |
| retention.result\_days | 审核结果与对话（整个任务删除） | 30 / 90 / 180 / 365 / 0 | 365 |
| retention.outbound\_days | 外发记录正文（request\_text、response\_text） | 30 / 90 / 180 / 365 / 0 | 180 |
| retention.audit\_days | 审计日志 | 30 / 90 / 180 / 365 / 0 | 0 |
| retention.unfinished\_days | 未完成任务（解析失败、待确认）无活动后整个删除 | 30 / 90 / 180 / 365 / 0 | 30 |

保存时校验：原件档不长于结果档（0 视为最长），否则 422 `RETENTION_ORDER`。不是可选值时 422。

### 2.2 活动时间

tasks 增加 `last_activity_at timestamptz 非空`：创建任务、确认发送、审核轮次结束、追问成功、重新审核（PATCH）时更新为当前时间。只打开查看不算。迁移回填 `coalesce(last_completed_at, updated_at, created_at)`。原件层与结果层都按 `last_activity_at` 计算（原件层原来按 last\_completed\_at，改为活动时间后，追问会延长原件的保留）。

### 2.3 清理任务（每天 03:30，沿用现有 retention job）

按顺序执行，每类逐个任务加锁处理，单次最多处理 500 个任务（剩余的第二天继续），写一条审计 `retention_run`（只含各类数量）：

1. **原件层**（现有逻辑）：status 为 reviewed / completed\_local、`last_activity_at` 早于截止、仍有原件的任务：删原件、清节点原文和 user\_note\_enc。
2. **结果层**：status 为 reviewed / completed\_local、`last_activity_at` 早于截止的任务：调用与用户删除相同的删除函数整个删除（17.8）。
3. **未完成任务**：status 为 parse\_failed / awaiting\_confirm、`last_activity_at` 早于截止的任务：整个删除。parsing / reviewing 不删。
4. **外发记录正文**：created\_at 早于截止、正文未清空的 llm\_calls：request\_text、response\_text 置空，其余字段保留；外发记录详情显示“正文已按保留期清除”。
5. **审计日志**：created\_at 早于截止的 audit\_logs 删除（retention\_run 本身写在删除之后）。

### 2.4 删除任务时清空外发记录中的文件名

不论是用户删除还是保留期删除：该任务的 llm\_calls.task\_title 置空（外发记录列表显示“（任务已删除）”）。文件名常带客户名称。

### 2.5 备份

本地备份按份数轮换，清理的数据最多在“备份保留份数”天后从本地备份中消失；不做特殊处理。

### 2.6 从 v1.0.0 升级

V1.0 的 `retention.original_days` 默认 0（永久）。迁移 0012：**tasks 表非空且 settings 中没有 retention.\* 行时**，写入 `original_days=0、result_days=0、outbound_days=0、audit_days=0、unfinished_days=0`（保持升级前“全部永久”的行为），并写入 `retention.review_pending=true`；管理员登录后显示提醒“数据保留已改为分层设置，当前全部为永久，请在设置 → 数据中确认”（code `RETENTION_REVIEW`），保存一次保留设置后清除。新安装（tasks 表为空）不写，使用 2.1 的默认值。

### 2.7 测试用例

| # | 场景 | 期望 |
|-|-|-|
| 1 | 原件 30、结果 365；任务最后活动 31 天前 | 原件与原文删除，任务仍在、结果可看、报告可导出 |
| 2 | 同上，最后活动 366 天前 | 任务整个删除（原件、节点、对照表、轮次、问题、对话都无），llm\_calls 保留且 task\_title 为空 |
| 3 | 最后一次审核在 40 天前、追问在 5 天前 | 原件不删（按活动时间） |
| 4 | 待确认任务无活动 31 天 | 整个删除 |
| 5 | parsing、reviewing 任务超过期限 | 不删 |
| 6 | 外发记录 181 天前 | 正文为空，其余字段保留；详情接口返回“正文已按保留期清除” |
| 7 | 审计 30 天、日志 31 天前 | 删除；本次 retention\_run 存在 |
| 8 | 保存原件 365、结果 90 | 422 RETENTION\_ORDER |
| 9 | 保存 45 天 | 422 |
| 10 | 用户删除任务 | 该任务 llm\_calls.task\_title 为空 |
| 11 | 升级：已有任务、无保留设置的库执行 0012 | 五项均为 0，提醒 RETENTION\_REVIEW 出现；保存一次后消失；清理任务不删除任何数据 |
| 12 | 新安装执行 0012 | 不写设置，默认值为 2.1 |
| 13 | 单次超过 500 个任务 | 本次处理 500 个，其余下次 |
| 14 | 迁移 0012 升级 / 降级 | 降级删除 last\_activity\_at 与新增设置行；模型与迁移一致 |
| 15 | A-06 | 清理只按时间，不看 owner；接口仍只返回本人任务 |

## 三、到期提示与存储用量（V2-C-03）

**文件范围**：`backend/app/tasks/service.py`（列表与详情返回到期时间）、`backend/app/tasks/schemas.py`、`backend/app/admin/data.py`（系统状态）、`frontend/src/views/History.vue`、`frontend/src/views/ReviewResult.vue`、`frontend/src/views/settings/Data.vue`、`frontend/src/api/tasks.ts`、`frontend/src/api/admin.ts`、对应测试。

1. `GET /tasks`、`GET /tasks/{id}` 增加 `original_expires_at`、`task_expires_at`（按 2.1、2.2 计算；永久为 null；原件已删除时 original\_expires\_at 为 null）。
2. 审核历史增加一列“保留至”（显示任务删除日期，永久显示“永久”）；结果页在任务删除前 7 天内或原件删除前 7 天内显示一行提示“本任务将于 {日期} 删除，如需留存请导出报告”“原件将于 {日期} 删除，之后不能重新审核和追问”。
3. 系统状态（`GET /admin/system/status`）增加：原件占用（遍历 FILES\_DIR 求和）、数据库占用（pg\_database\_size）、备份目录占用与最近一次备份大小、数据盘剩余空间（已有）、**预计可用天数** = 剩余空间 ÷（近 30 天原件日均新增 × (备份保留份数 + 1) + 近 30 天数据库日均增量 × (备份保留份数 + 1)），近 30 天没有新增时显示“—”。结果缓存 10 分钟（目录遍历较慢）。
4. 设置 → 数据 → 系统状态显示上述各项；预计可用天数 < 30 时警告色。

| # | 场景 | 期望 |
|-|-|-|
| 1 | 原件 30、结果 365，最后活动 2026-01-01 | original\_expires\_at=2026-01-31，task\_expires\_at=2027-01-01（按 Asia/Shanghai 日期显示） |
| 2 | 结果层永久 | task\_expires\_at 为 null，历史列表显示“永久” |
| 3 | 距删除 5 天 | 结果页显示删除提示 |
| 4 | 系统状态 | 各项占用为非负整数；近 30 天无新增时可用天数为 null |
| 5 | 10 分钟内重复请求 | 不重复遍历目录（用计数桩验证） |

## 四、个人每日限额与重复文件提示（V2-C-04）

**文件范围**：`backend/app/llm/gateway.py`（只改调用上限检查）、`backend/app/llm/usage.py`、`backend/app/admin/model.py`（设置项）、`backend/app/admin/settings_store.py`、`backend/app/tasks/router.py`、`backend/app/tasks/service.py`、`frontend/src/views/settings/Model.vue`、`frontend/src/views/ReviewNew.vue`、`frontend/src/api/tasks.ts`、对应测试。

### 4.1 个人每日限额

* 设置 `model.user_daily_cap`（默认 0 = 不限，范围 0～10000），在模型设置页“每月调用上限”下方。
* 网关第一步“调用上限检查”同时检查：本月全部 sent 次数 < 每月上限；**本人今天**（Asia/Shanghai 自然日）sent 次数 < 个人每日上限。purpose=test 不计、不受限。超限时不发送、不写 llm\_calls，错误码 `USER_CAP_REACHED`，“已达今日个人调用上限，请明天再试或联系管理员”。审核中途达到限额时，与每月上限相同：后续分段失败，轮次 partial / failed。
* 确认发送与追问发送前先做同样的检查，超限时直接 409 USER\_CAP\_REACHED（不建轮次），避免用户等到审核中途才失败。

### 4.2 重复文件提示

* `POST /tasks` 增加表单字段 `allow_duplicate`（默认 false）。主文件的 SHA-256 与**本人**未删除的任务中某个主文件相同、且 allow\_duplicate 为 false 时，返回 409 `DUPLICATE_FILE`，body 含最近一个这样的任务的 `{task_id, title, created_at}`，不创建任务。只比较本人的任务，不泄露他人信息。
* 前端收到 409 时弹窗：“你在 {时间} 审核过同一文件”，按钮“打开上次结果”（跳转该任务）与“继续新建”（带 allow\_duplicate=true 重新提交）。

| # | 场景 | 期望 |
|-|-|-|
| 1 | 个人上限 2，今天已 sent 2 次 | 第 3 次网关不发送，USER\_CAP\_REACHED，无 llm\_calls 新行 |
| 2 | 昨天 sent 5 次、今天 0 次 | 可发送 |
| 3 | 测试连接 | 不受个人上限影响 |
| 4 | 确认发送时已超限 | 409，不建轮次 |
| 5 | 上传与本人旧任务主文件相同的文件 | 409 DUPLICATE\_FILE，返回旧任务信息 |
| 6 | 他人上传过同一文件 | 正常创建（不提示） |
| 7 | allow\_duplicate=true | 正常创建 |

## 五、审计日志页与图片警告（V2-C-05）

**文件范围**：`backend/app/admin/data.py`（审计列表接口）、`backend/app/parsing/docx.py`（警告文字）、`frontend/src/views/settings/Data.vue`、`frontend/src/api/admin.ts`、`frontend/src/utils/labels.ts`（动作名称）、对应测试。

1. `GET /admin/audit-logs`：参数 user\_id、action、from、to、page；按时间降序，每页 20 条；返回时间、用户（显示名）、动作（前端翻译为中文）、对象类型与 id、detail（原样 JSON，本来就不含敏感信息）、IP。只读，查看不写审计。
2. 设置 → 数据增加“审计日志”分区。
3. DOCX 警告 `WARN_EMBEDDED` 改为“文件中有 {N} 个图片或嵌入对象未参与 AI 审核；如有扫描附件，请另存为 PDF 或图片一并上传”，N 为识别到的图片与嵌入对象个数。

| # | 场景 | 期望 |
|-|-|-|
| 1 | 审计列表按动作、日期筛选与分页 | 结果与数据库一致 |
| 2 | 普通用户访问 | 403 |
| 3 | DOCX 含 3 张图片 | 警告含“3 个” |

## 六、设置权限拆分（V2-C-06，测试先行）

**文件范围**：`backend/app/db/models.py`、`backend/app/db/alembic/versions/0013_admin_roles.py`（新）、`backend/app/security/deps.py`、`backend/app/admin/*.py`（替换权限依赖）、`backend/app/auth/router.py`（提醒按角色）、`backend/app/admin/users.py`、`backend/app/cli.py`（只改创建管理员的角色值）、`backend/app/redaction/engine.py` 与 `backend/app/redaction/router.py`（“以后自动替换”）、`frontend/src/router/index.ts`、`frontend/src/layouts/MainLayout.vue`、`frontend/src/views/settings/SettingsLayout.vue`、`frontend/src/views/settings/Users.vue`、`frontend/src/components/ManualRedactDialog.vue`、`frontend/src/components/SuspectList.vue`、`frontend/src/stores/auth.ts`、对应测试。

### 6.1 角色

`users.role` 取值由 user / admin 改为：`user`（普通用户）、`sys_admin`（系统管理员）、`biz_admin`（业务管理员）、`admin`（两者兼任）。迁移 0013：原 admin 保持 admin（升级后能力不变）。

| 设置页与接口 | sys\_admin | biz\_admin | admin |
|-|-|-|-|
| 账号 `/admin/users*` | ✓ | | ✓ |
| 模型 `/admin/settings/model*`、`/admin/usage` | ✓ | | ✓ |
| 数据：备份、保留期、外发记录、系统状态、审计日志 | ✓ | | ✓ |
| 审核要求：`/admin/company-requirements*`、`/admin/company-profile`、`/admin/requirement-versions` | | ✓ | ✓ |
| 脱敏：`/admin/settings/redaction`、`/admin/terms*`、`/admin/our-parties*` | | ✓ | ✓ |

* `deps.require_sys_admin`、`deps.require_biz_admin` 取代 `require_admin`；无权限 403（错误码不变）。
* 管理员提醒：备份、磁盘、调用上限、RETENTION\_REVIEW 给系统管理员；PROMPT\_TERM\_CONFLICT 给系统管理员和业务管理员（它会让所有审核被拦截）。
* 至少保留一个启用的、角色为 sys\_admin 或 admin 的账号（原“最后一个管理员”规则改为此条，Q-99 ④ 的加锁方式不变）。管理员不能修改自己的角色（Q-99 ② 不变）。
* 两种管理员都不能查看他人任务（17.2 不变）。
* 前端：顶栏“设置”对非 user 角色可见；设置左侧导航只显示有权限的分页；直接访问无权限分页时跳到第一个有权限的分页；账号页角色下拉为“普通用户 / 系统管理员 / 业务管理员 / 系统与业务管理员”。

### 6.2 以后自动替换

手动补充与疑似遗漏“替换”选择“其他”类别时，可勾选“以后自动替换”：保存时（不等确认发送）把该文字写入自定义敏感词（kind=custom，category=CUSTOM，created\_by=当前用户，enabled=true）；已存在同名词条时不重复；写入前按 TERM\_IN\_PROMPT 规则检查（Q-108），命中时 422 且不保存本次替换。设置 → 脱敏的自定义敏感词列表显示记录人，业务管理员可停用、删除。

### 6.3 测试用例

| # | 场景 | 期望 |
|-|-|-|
| 1 | biz\_admin 访问账号、模型、数据接口 | 403；访问审核要求、脱敏接口正常 |
| 2 | sys\_admin 访问审核要求、脱敏接口 | 403 |
| 3 | admin | 全部可访问（与升级前相同） |
| 4 | 迁移 0013 升级 | 原 admin 仍为 admin；降级时 sys\_admin、biz\_admin 改回 admin（有权限的都保留为管理员） |
| 5 | 停用最后一个 sys\_admin（另有 biz\_admin） | 409 |
| 6 | 任一管理员访问他人任务、轮次、问题、对话 | 404（A-06） |
| 7 | 普通用户勾选“以后自动替换”保存“示例简称” | redaction\_terms 新增 custom 词条，created\_by 为该用户；下一个任务自动替换 |
| 8 | 勾选的文字出现在固定提示词中 | 422 TERM\_IN\_PROMPT，本次替换不保存 |
| 9 | /auth/me | 返回 role；提醒按 6.1 分配 |
| 10 | 前端 | build 通过；浏览器检查在 V2-C-08 |

## 七、主密钥更换（V2-C-07，测试先行）

**文件范围**：`backend/app/security/rotate.py`（新）、`backend/app/cli.py`（只追加 rotate-key、verify-key 命令）、`deploy/scripts/rotate_key.sh`（新）、`backend/tests/test_rotate_key.py`（新）、`backend/tests/test_scripts.py`（只追加）。

1. **命令**：`python -m app.cli rotate-key --old-key-file <旧> --new-key-file <新>`，在 app、worker 停止时由脚本在一次性容器中运行。
2. **范围**：所有以 `_enc` 结尾的列（按 SQLAlchemy 元数据自动列出，测试中断言列表包含 doc\_nodes.text\_enc、redaction\_maps.map\_enc、redaction\_terms.term\_enc、local\_findings.detail\_enc、findings.quote\_snapshot\_enc、tasks.user\_note\_enc、redaction\_exclusions.text\_enc）、settings 中的 `model.api_key_enc`（base64）、FILES\_DIR 中的全部原件。备份文件用备份密码加密，不在范围内。
3. **可续做**：对每个密文先用新密钥试解，成功说明已处理，跳过；否则用旧密钥解密、新密钥加密写回。数据库按表分批（每批 500 行一个事务）；原件写到同目录的临时文件、fsync 后改名替换。中断后重跑同一命令即可继续，不需要进度表。两个密钥都解不开时停止并报告位置（表名与主键，不含内容）。
4. **verify-key**：`python -m app.cli verify-key --key-file <新>` 抽查每个加密列与原件各最多 20 个，全部能用新密钥解开时返回 0。
5. **rotate\_key.sh**（沿用 lib.sh 的检查与确认方式）：检查 /data 挂载与新密钥文件（32 字节、权限 600）→ 要求输入确认文字 → 先执行一次 backup\_now.sh --wait → 停止 app、worker → 运行 rotate-key（容器命令接 `</dev/null`，Q-150）→ verify-key → 旧密钥改名为 `master_key.old-{yyyyMMdd}`（600），新密钥放到 `master_key` → 启动 app、worker → 提示把新密钥离线保管，旧密钥在旧备份全部过期前不得销毁（旧备份恢复时需要旧主密钥，restore.sh 会校验）。
6. 日志与输出不含任何密钥和明文。

| # | 场景 | 期望 |
|-|-|-|
| 1 | 含任务、对照表、词条、补充说明、模型密钥、原件的库，执行 rotate-key | 全部可用新密钥解密，旧密钥解不开；业务数据与更换前逐项相同 |
| 2 | 处理到一半中断（测试中在第 N 批抛异常），再次执行 | 完成，结果同 #1 |
| 3 | 某行两个密钥都解不开 | 停止，报告表名与主键，退出码非 0 |
| 4 | verify-key | 新密钥返回 0，旧密钥返回非 0 |
| 5 | `_enc` 列清单 | 包含第 2 条列出的全部列；以后新增 `_enc` 列自动包含 |
| 6 | 输出与日志 | 不含密钥字节和明文 |
| 7 | rotate\_key.sh（test\_scripts 中模拟 docker） | 顺序为备份 → 停止 → rotate → verify → 换文件 → 启动；任一步失败不换密钥文件 |

## 八、集中测试（V2-C-08）

**产出**：`docs/v2/reports/V2-C-test.md`（新）。

1. 整仓挂载 Docker 全量测试通过。
2. 浏览器清单（四种宽度）：审核历史“保留至”列与结果页到期提示；系统状态的占用与可用天数；审计日志分区；个人限额设置与超限提示；重复文件弹窗；三种管理员角色看到的设置导航与提醒；“以后自动替换”勾选。
3. 在测试服务器（用户已授权的专用测试机）上演练：
   * 用 v1.0.0 的库执行升级迁移，确认保留设置全部为永久、提醒出现、清理不删数据（2.6）；
   * 把测试数据的活动时间回拨后执行清理，四层结果与 2.7 一致；
   * 执行 rotate\_key.sh 一次，之后上传、审核、追问、导出、恢复演练（用更换前的备份 + 旧主密钥）均正常。
4. 8 个审核并发（模拟模型）时 worker 无连接池超时，记录内存。

## 九、批次审核（V2-C-09，Opus）

核对测试用例未被删改；数据删除与密钥更换的实现逐行审阅；A-06；升级不误删（2.6）；审阅 V2-C-08 报告；汇报用户，确认后进入 V2-D-00。

## 十、新增待确认问题

见 docs/v2/open\_questions.md V2Q-17～V2Q-22。
