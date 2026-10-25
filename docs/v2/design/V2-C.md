# V2-C 系统与数据

2026-10-23 · Opus 5.5（V2-C-00） · 用户已确认（2026-10-23）

范围见 [PLAN.md](../PLAN.md) 第三节 C1～C7；执行规则见 [TASKS-V2.md](../TASKS-V2.md) 第一节。本文件是 V2-C 各任务之间的接口契约，任务卡自包含。规格变更汇总在 [SPEC-V2.md](../SPEC-V2.md) C 部分。

## 〇、共同约定

1. **开工条件**：V2-B 已通过批次审核（V2-B-09），追加的 V2-B-10 已通过审核（V2-B-11）。M8 的部署脚本（install.sh、upgrade.sh、backup\_now.sh、restore.sh、lib.sh）已在 V1.0 中提交。
2. **测试**：每节测试用例逐条写成测试，不得删改；单个任务只跑列出的测试文件。涉及数据删除（V2-C-02）与加密（V2-C-07）的任务为测试先行，审核时重点核对。
3. **迁移编号**：V2-B 实际用到 0013（0012_ner_source、0013_ner_node_results，V2Q-49）。本批 V2-C-02 用 0014，V2-C-03 用 0015（V2Q-64），V2-C-06 顺延为 0016（V2Q-53、V2Q-64）。
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

**文件范围**：`backend/app/backup/retention.py`、`backend/app/admin/settings_store.py`、`backend/app/admin/data.py`、`backend/app/tasks/service.py`（删除任务时清空外发记录文件名；活动时间）、`backend/app/review/job.py`、`backend/app/review/service.py`（只改 create\_run）、`backend/app/chat/service.py`、`backend/app/db/jobs.py`（只改 finalize\_failed 的 review 分支）——这四个文件只更新活动时间（V2Q-61）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0014_retention_layers.py`（新）、`backend/app/admin/alerts.py`、对应测试（test\_retention.py、test\_admin\_data.py、test\_admin\_alerts.py、test\_migrations.py、test\_tasks\_api.py、test\_runs\_api.py、test\_review\_job.py、test\_chat.py、test\_jobs.py）。

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

tasks 增加 `last_activity_at timestamptz 非空`：创建任务、确认发送、审核轮次结束、追问成功、重新审核（PATCH）时更新为当前时间。只打开查看不算。迁移回填 `coalesce(last_completed_at, updated_at, created_at)`。

更新入口（V2Q-61，只有下表这些地方写 `last_activity_at`，均与该操作的状态变化在同一事务中，值取 `utcnow()`；操作被拒绝或失败时不写；不改网关、权限、预检、状态机和其他行为）：

| 活动 | 入口 | 写入时机 |
|-|-|-|
| 创建任务 | tasks/service.py `create_task` | 建任务行时（迁移后的列非空，新行必须带值） |
| 确认发送 | review/service.py `create_run` | 预检通过、建轮次并入队、任务置 reviewing 的同一事务；前面任何 409（LOCAL\_ONLY、TASK\_STATE\_CONFLICT、SUSPECTS\_PENDING、OUR\_PARTY\_REQUIRED、OUTBOUND\_BLOCKED）都不写 |
| 审核轮次结束 | review/job.py `_save`（completed / partial）与 `_finish`（blocked / failed） | 写轮次终态的同一事务；任务或轮次已被删除（`_reload` 返回 None）时不写 |
| 审核轮次结束（处理函数之外） | db/jobs.py `finalize_failed` 的 review 分支 | 只在任务由 reviewing 改为 reviewed 时写；处理函数已收尾、任务不在 reviewing 时不写；parse、backup 分支不写 |
| 追问成功 | chat/service.py `send` | 保存问答两条消息的同一事务；脱敏预检拦截、网关失败（含上限）不写 |
| 重新审核 | tasks/service.py `patch_task` | 通过全部检查、任务回到 awaiting\_confirm 的同一事务；409 / 422 不写 |

解析完成、解析失败、打开查看、导出报告、处理问题状态、删除都不写。原件层与结果层都按 `last_activity_at` 计算（原件层原来按 last\_completed\_at，改为活动时间后，追问会延长原件的保留）。

### 2.3 清理任务（每天 03:30，沿用现有 retention job）

五项都为 0 时不入队；任一项非 0 就入队，为 0 的那一类跳过。`retention.review_pending=true` 期间照常按当前保存的值执行（升级后新增四项为 0 即不清理，见 2.6）。

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

V1.0 只有 `retention.original_days`（默认 0 即永久，管理员可改为 0～3650 的任意天数，只有改过才有设置行）。升级不得让任何新规则在升级当天删除 V1.0 不会删除的数据（第〇节第 4 条，V2Q-19、V2Q-62）。

迁移 0014 按下面的规则写设置：

1. **判断旧库**：`users` 表非空即为已有数据的库（V1.0 安装流程先迁移、再创建管理员，新安装执行迁移时 users 必为空；只看 tasks 会漏掉任务已全部删除、但仍有外发记录和审计日志的库）。
2. **旧库**：
   * `retention.original_days`：已有设置行的**原值保留，不改写、不取整**（管理员在 V1.0 中明确选过，V1.0 已在按它清理；回填后的活动时间对已审核任务等于 last\_completed\_at，清理范围与升级前相同）。没有设置行时写 0。
   * `retention.result_days`、`outbound_days`、`audit_days`、`unfinished_days`：没有设置行时写 0（V1.0 中这几类数据都永久保存）；已有行时保留原值（V1.0 不会产生这些行，此条只为重跑与恢复时不覆盖）。
   * 写入 `retention.review_pending=true`。
3. **新安装**（users 为空）：不写任何设置，使用 2.1 的默认值。
4. **降级**：删除 `last_activity_at` 列，删除 `retention.result_days`、`outbound_days`、`audit_days`、`unfinished_days`、`review_pending` 五行；**不动 `retention.original_days` 行**（V1.0 认识它，迁移写的 0 与 V1.0 默认值相同，管理员在 V2 中改过的值 V1.0 也照此执行）。

提醒与确认：`review_pending=true` 时管理员登录后显示提醒“数据保留已改为分层设置，新增的保留项暂为永久，请在设置 → 数据中确认”（code `RETENTION_REVIEW`）。PUT /admin/settings/retention 校验通过即清除 review\_pending（五项与原值相同也清除，并照常写审计）；校验失败不清除。

非档位旧值：`original_days` 为 30 / 90 / 180 / 365 / 0 以外的值（如 45）时，GET 照实返回，清理照它执行；PUT 时五项都必须是可选值，原样提交 45 返回 422（管理员须改选一个档位）。设置页如何显示这个值由 V2-C-03 处理（下拉框中额外列出“45 天（旧版设置）”，提交时由后端 422 提示改选）。

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
| 11 | 升级：已有用户与任务、无 retention.\* 行的库执行 0014 | 五项均为 0，提醒 RETENTION\_REVIEW 出现；原值保存一次后消失；清理任务不删除任何数据 |
| 12 | 新安装（users 为空）执行 0014 | 不写设置，默认值为 2.1 |
| 13 | 单次超过 500 个任务 | 本次处理 500 个，其余下次 |
| 14 | 迁移 0014 升级 / 降级 | 降级删除 last\_activity\_at 与 2.6 第 4 条的五行；original\_days 行保留；模型与迁移一致 |
| 15 | A-06 | 清理只按时间，不看 owner；接口仍只返回本人任务 |
| 16 | 升级：旧库已保存 original\_days=30；另有 last\_completed\_at 31 天前与 400 天前的已审核任务、31 天前的待确认任务、200 天前的外发记录、400 天前的审计日志 | 迁移后 original\_days=30、其余四项 0、review\_pending=true；执行清理只删两个已审核任务的原件与原文（与 V1.0 相同），任务、待确认任务、外发正文、审计全部保留；降级后 original\_days 仍为 30 |
| 17 | 升级：旧库 original\_days=45 | 迁移后仍为 45，GET 返回 45，清理按 45 天；PUT 原样提交 45 → 422 且 review\_pending 仍为 true；改选 30 后保存成功、提醒消失 |
| 18 | 升级：tasks 为空，但 users、外发记录、审计日志非空 | 按旧库处理：五项为 0、review\_pending=true；清理不删任何数据 |
| 19 | 确认发送成功；以及分别因 SUSPECTS\_PENDING、OUTBOUND\_BLOCKED、TASK\_STATE\_CONFLICT 被拒 | 成功时 last\_activity\_at 更新为本次时间；被拒时不变（test\_runs\_api.py） |
| 20 | 审核轮次以 completed、partial、blocked、failed 结束 | 四种都更新 last\_activity\_at（test\_review\_job.py） |
| 21 | review job 心跳超时用完次数，经 finalize\_failed 收尾 | 任务 reviewing → reviewed 时更新；任务已不在 reviewing 时不变；parse job 最终失败不更新（test\_jobs.py） |
| 22 | 创建任务、追问成功、重新审核（PATCH）；追问被预检拦截或网关失败、PATCH 返回 409；打开详情、列表、导出报告 | 前三者更新；其余都不变（test\_tasks\_api.py、test\_chat.py） |

## 三、到期提示与存储用量（V2-C-03）

**文件范围**：`backend/app/tasks/service.py`（列表与详情返回到期时间）、`backend/app/tasks/schemas.py`、`backend/app/admin/data.py`（系统状态；备份记录的剩余空间）、`backend/app/backup/service.py`（只在备份成功时记录统计，V2Q-64）、`backend/app/db/models.py`（只加 backup\_records 三列）、`backend/app/db/alembic/versions/0015_backup_stats.py`（新）、`frontend/src/views/History.vue`、`frontend/src/views/ReviewResult.vue`、`frontend/src/views/settings/Data.vue`、`frontend/src/api/tasks.ts`、`frontend/src/api/admin.ts`、对应测试（test\_tasks\_api.py、test\_admin\_data.py、test\_backup.py、test\_migrations.py）。不改 `deploy/docker-compose.yml`：app 不挂载备份目录（V2Q-64）。

1. `GET /tasks`、`GET /tasks/{id}` 增加 `original_expires_at`、`task_expires_at`（按 2.1、2.2 计算；永久为 null；原件已删除时 original\_expires\_at 为 null）。
2. 审核历史增加一列“保留至”（显示任务删除日期，永久显示“永久”）；结果页在任务删除前 7 天内或原件删除前 7 天内显示一行提示“本任务将于 {日期} 删除，如需留存请导出报告”“原件将于 {日期} 删除，之后不能重新审核和追问”。
3. 系统状态（`GET /admin/system/status`）增加存储用量，数据来源与计算见 3.1、3.2。
4. 设置 → 数据 → 系统状态显示上述各项；预计可用天数 < 30 时警告色；为 null 时显示“—”，并按 `estimate_reason` 显示一行灰色说明（3.2）；备份三项显示“（{统计时间} 备份时统计）”，没有统计时显示“—”。
5. 设置 → 数据的“数据保留”分区改为五项（2.1 的名称与可选值，下拉框选 30 / 90 / 180 / 365 天 / 永久），一个“保存”按钮整体提交；后端 422 时显示其提示（RETENTION\_ORDER 显示“原件与原文的保留期不能长于审核结果”）。当前值不是可选值时（V1.0 遗留，见 2.6），下拉框额外列出“{n} 天（旧版设置）”并选中，提示“请改选一个档位后保存”。保存成功后重新拉取提醒（RETENTION\_REVIEW 随之消失）。`api/admin.ts` 的 getRetention / saveRetention 改为五项。（V2Q-62 审阅时发现此表单原未分配任务，归入本任务。）

### 3.1 数据来源（V2Q-64）

app 容器不挂载备份目录（备份里有已按保留期删除、但尚未轮换掉的数据，面向网络的 app 不应能读到）。凡是要看备份目录的数据，都由 worker 在备份时测量，写进该次备份记录；数据库体积的历史也借每天的备份记录保存，不另建采样表、不加定时任务。

**备份时记录**（`backup/service.py`，只在 status=success 的分支；迁移 0015 给 `backup_records` 加三列，均为可空 BIGINT，旧记录为 NULL，降级删除这三列）：

| 列 | 含义 | 测量时机与方法 |
|-|-|-|
| `db_bytes` | 数据库体积 | pg\_dump 完成后，同一会话 `SELECT pg_database_size(current_database())` |
| `dir_bytes` | 备份目录占用 | 轮换完成后，备份目录**顶层**普通文件的大小之和（`os.scandir`，不跟随符号链接，不进入子目录，工作目录 `.work` 是目录、自然不计） |
| `dir_free_bytes` | 备份目录所在磁盘的剩余空间 | 轮换完成后 `shutil.disk_usage(备份目录).free` |

* 每项单独测量，某一项出错（OSError、数据库错误）时该列写 NULL，日志只记错误类型，**备份仍记为成功**；不为测量重试。
* 失败的备份（含“备份文件已生成，但删除旧备份失败”）不写这三列。
* 定时与立即备份都记录；同一天多次备份都算样本。

**app 实时测量**（`admin/data.py`）：

| 项 | 来源 |
|-|-|
| 原件占用 `files_bytes` | 遍历 FILES\_DIR（`os.walk`，不跟随符号链接）的普通文件大小之和；根目录不可读时为 null；单个文件 stat 失败时跳过 |
| 数据库占用 `db_bytes` | 实时 `pg_database_size(current_database())` |
| 数据盘剩余 `data_free_bytes` | 已有，`disk_usage(FILES_DIR).free` |

**取自最近一次成功备份记录**（按 finished\_at 最新、status=success；没有这样的记录或其列为 NULL 时对应项为 null）：`backup_dir_bytes` ← dir\_bytes，`latest_backup_bytes` ← size\_bytes，`backup_free_bytes` ← dir\_free\_bytes，`backup_stats_at` ← finished\_at。

**顺带修正**：`GET /admin/backup/records` 的 `free_bytes` 现在在 app 容器里对 BACKUP\_DIR 求剩余空间，app 没有挂载备份目录，实际量到的是容器自身的文件系统，不是备份盘。改为取最近一次成功备份的 dir\_free\_bytes，并增加 `free_measured_at`（该记录的 finished\_at）；没有时两者为 null。`dir` 字段不变。Data.vue 备份分区的剩余空间旁显示“（{时间} 备份时统计）”；为 null 时显示“—（尚无成功备份的统计）”，不再显示“备份目录不可用”。

### 3.2 预计可用天数

```
样本 S = status=success 且 db_bytes 非空、finished_at ≥ now − 30 天 的备份记录，按 finished_at 升序
E = S 的第一条，L = S 的最后一条
跨度 span = L.finished_at − E.finished_at
若 S 少于 2 条或 span < 7 天          → null，estimate_reason = "insufficient_history"
数据库日均增量 db_daily = max(0, L.db_bytes − E.db_bytes) × 86400 ÷ span 秒数
原件日均新增 files_daily = Σ task_files.size_bytes（created_at ∈ [E.finished_at, now]）× 86400 ÷ (now − E.finished_at) 秒数
每日占用 daily = (files_daily + db_daily) × (backup.keep + 1)
若 data_free_bytes 为 null             → null，estimate_reason = "unavailable"
若 daily = 0                          → null，estimate_reason = "no_growth"
否则 estimated_days = data_free_bytes ÷ daily（整除，向下取整），estimate_reason = null
```

* 全部用整数运算（字节数为整数，除法向下取整），不用浮点。
* “不足 30 天”：只要 30 天内最早与最晚样本相隔 ≥ 7 天就按实际跨度折算日均，不按 30 天摊薄（摊薄会高估可用天数）。新安装、从 V1.0 升级（旧记录列为 NULL）或备份连续失败时，前 7 天显示“—”。
* 负增长（清理后数据库变小）按 0 计；原件新增按仍存在的 task\_files 行统计，已删除任务的文件不计。
* 公式假定备份目录与数据盘同盘；分盘时把备份占用也算在数据盘上，结果偏保守（可用天数偏少），不另行判断。
* 界面说明文字：insufficient\_history “备份统计不足 7 天，暂无法估算”；no\_growth “近 30 天没有新增数据”；unavailable “无法读取数据盘剩余空间”。

**缓存**：存储用量部分（files\_bytes、db\_bytes、data\_free\_bytes、备份四项、estimated\_days、estimate\_reason）在 app 进程内存中缓存 10 分钟，返回 `storage_measured_at`（本次测量时间）；version、queued\_jobs 每次实时查询。app 有 2 个 uvicorn 进程，各自缓存，可接受。不写库、不提供手动刷新。

### 3.3 测试用例

| # | 场景 | 期望 |
|-|-|-|
| 1 | 原件 30、结果 365，最后活动 2026-01-01 | original\_expires\_at=2026-01-31，task\_expires\_at=2027-01-01（按 Asia/Shanghai 日期显示） |
| 2 | 结果层永久 | task\_expires\_at 为 null，历史列表显示“永久” |
| 3 | 距删除 5 天 | 结果页显示删除提示 |
| 4 | 系统状态 | 各项占用为非负整数；近 30 天无新增时可用天数为 null，estimate\_reason=no\_growth |
| 5 | 10 分钟内重复请求 | 不重复遍历目录、不重复查询数据库体积与备份记录（用计数桩验证）；queued\_jobs 仍实时；超过 10 分钟重新测量 |
| 6 | 没有任何成功备份，或只有 V1.0 留下的旧记录（三列为 NULL） | 备份三项与 backup\_stats\_at 为 null；estimated\_days 为 null，estimate\_reason=insufficient\_history |
| 7 | 30 天内两条样本相隔 10 天，db\_bytes 增加 10 MiB；now 取后一条样本的时间，这 10 天内新增原件 20 MiB；keep=7；剩余 900 MiB | db\_daily=1 MiB、files\_daily=2 MiB、daily=24 MiB、estimated\_days=37 |
| 8 | 样本相隔 6 天 | null，insufficient\_history |
| 9 | 31 天前的样本 + 30 天内仅 1 条 | 31 天前的不计入；null，insufficient\_history |
| 10 | 后一条样本 db\_bytes 比前一条小，且期间无原件新增 | db\_daily=0；null，no\_growth |
| 11 | 只有失败的备份记录带有数据（测试直接造） | 不作为样本 |
| 12 | FILES\_DIR 不可读 | files\_bytes 为 null，接口 200，其余各项正常 |
| 13 | 数据盘剩余空间读不到 | estimated\_days 为 null，estimate\_reason=unavailable |
| 14 | 一次成功备份 | 记录的 db\_bytes > 0；dir\_bytes 等于备份目录顶层文件大小之和（含本次备份与 .sha256，不含子目录内文件）；dir\_free\_bytes 非空 |
| 15 | 成功备份时 disk\_usage 抛 OSError（桩） | 备份 status=success，dir\_free\_bytes 为 NULL，其余两列正常；日志只记错误类型 |
| 16 | 备份失败、轮换失败 | 三列均为 NULL |
| 17 | `GET /admin/backup/records` | free\_bytes、free\_measured\_at 取自最近一次成功备份；没有时为 null；不再在 app 中调用 disk\_usage(BACKUP\_DIR)（桩断言未调用） |
| 18 | 迁移 0015 升级 / 降级 | 升级后旧记录三列为 NULL；降级删除三列；模型与迁移一致 |

## 四、个人每日限额与重复文件提示（V2-C-04）

**文件范围**：`backend/app/llm/gateway.py`（只改调用上限检查与 MESSAGES）、`backend/app/llm/usage.py`、`backend/app/admin/model.py`（设置项）、`backend/app/admin/settings_store.py`、`backend/app/review/service.py`（只在 create\_run 中加上限预检，V2Q-65）、`backend/app/chat/service.py`（只在 send 中加上限预检、在 \_ERRORS 中加 USER\_CAP\_REACHED，V2Q-65）、`backend/app/tasks/router.py`、`backend/app/tasks/service.py`、`frontend/src/views/settings/Model.vue`、`frontend/src/views/ReviewNew.vue`、`frontend/src/api/tasks.ts`、`frontend/src/api/runs.ts`（只在 RUN\_ERROR\_MESSAGES 中加两条）、对应测试（test\_gateway.py、test\_admin\_model.py、test\_runs\_api.py、test\_chat.py、test\_review\_job.py、test\_tasks\_api.py）。C-02 对 review/service.py、chat/service.py 的授权仅限活动时间，本任务的授权仅限下面写明的预检，两处都不改权限、出口复检、状态机和活动时间的写法。

### 4.1 个人每日限额

* 设置 `model.user_daily_cap`（默认 0 = 不限，范围 0～10000），在模型设置页“每月调用上限”下方。
* 网关第一步“调用上限检查”同时检查：本月全部 sent 次数 < 每月上限；**本人今天**（Asia/Shanghai 自然日）sent 次数 < 个人每日上限。purpose=test 不计、不受限。超限时不发送、不写 llm\_calls，错误码 `USER_CAP_REACHED`，“已达今日个人调用上限，请明天再试或联系管理员”。审核中途达到限额时，与每月上限相同：后续分段失败，轮次 partial / failed。
* 确认发送与追问发送前先做同样的检查，超限时直接 409（不建轮次、不写问答），避免用户等到审核中途才失败。

**共用检查**（V2Q-65）：`usage.cap_reached(db, user_id, purpose) -> str | None`，返回 `"CAP_REACHED"`、`"USER_CAP_REACHED"` 或 None。先查每月（与 V1.0 相同：所有 purpose 都受限，计数含 test），再查个人（purpose=test 或 user\_id 为空时跳过；计数只算该 user\_id、status=sent、purpose≠test、created\_at ≥ 本地当天 0 点）。两项上限为 0 时不查。网关第一步、确认发送、追问三处都调用它，不各写一份。只读，不加锁、不预占名额：预检通过后仍可能在网关处超限，届时按网关的结果处理。

**预检入口**：

| 入口 | 位置 | 超限时的返回 |
|-|-|-|
| 确认发送（含重新审核，都经 `POST /tasks/{id}/runs`） | `review/service.py` create\_run：在现有状态、疑似遗漏、我方、文件类型检查**之后**，build\_context 与出口预检**之前** | CAP\_REACHED → 409 `CAP_REACHED`“已达本月调用上限，请联系管理员”；USER\_CAP\_REACHED → 409 `USER_CAP_REACHED`（文字同上） |
| 追问（`POST /tasks/{id}/chat`） | `chat/service.py` send：在 \_require（可追问条件）**之后**，\_redact\_and\_record **之前** | CAP\_REACHED → 409 `CHAT_CAP_REACHED`（沿用 V1.0）；USER\_CAP\_REACHED → 409 `USER_CAP_REACHED` |

* 被预检拒绝时：不建轮次、不入队、任务状态不变、不写已记住的名称（learn\_confirmed\_names）、不写 run\_create 审计；不写 chat\_messages、不写追问的脱敏记录；不写 llm\_calls、不调用模型；不更新 last\_activity\_at（与 2.2 “被拒不写”一致）。
* create\_run 的每月上限预检是 V1.0 没有的：V1.0 在月度额度用完后仍能建轮次，到 worker 才失败。按 4.1 第三条“同样的检查”一并前移。
* 追问在预检之后网关仍返回 USER\_CAP\_REACHED 时（并发竞态），`_ERRORS` 映射为 409 USER\_CAP\_REACHED，不落到 502 CHAT\_FAILED。
* 前端：`RUN_ERROR_MESSAGES` 加 `CAP_REACHED`、`USER_CAP_REACHED` 两条（文字同上）；ChatPanel 已显示后端 message，不改。
* 审核分段的外发记录 user\_id 为 run.created\_by（即任务所有者），个人计数据此统计，不改。

### 4.2 重复文件提示

* `POST /tasks` 增加表单字段 `allow_duplicate`（默认 false）。主文件的 SHA-256 与**本人**未删除的任务中某个主文件相同、且 allow\_duplicate 为 false 时，返回 409 `DUPLICATE_FILE`，body 含最近一个这样的任务的 `{task_id, title, created_at}`，不创建任务。只比较本人的任务，不泄露他人信息。
* 前端收到 409 时弹窗：“你在 {时间} 审核过同一文件”，按钮“打开上次结果”（跳转该任务）与“继续新建”（带 allow\_duplicate=true 重新提交）。

### 4.3 测试用例

| # | 场景 | 期望 |
|-|-|-|
| 1 | 个人上限 2，今天已 sent 2 次 | 第 3 次网关不发送，USER\_CAP\_REACHED，无 llm\_calls 新行 |
| 2 | 昨天 sent 5 次、今天 0 次 | 可发送 |
| 3 | 测试连接 | 不受个人上限影响 |
| 4 | 确认发送时已超个人上限 | 409 USER\_CAP\_REACHED；无新 review\_runs、jobs；任务仍为 awaiting\_confirm；last\_activity\_at 不变；已记住的名称未写入；无 run\_create 审计；网关桩未被调用 |
| 5 | 上传与本人旧任务主文件相同的文件 | 409 DUPLICATE\_FILE，返回旧任务信息 |
| 6 | 他人上传过同一文件 | 正常创建（不提示） |
| 7 | allow\_duplicate=true | 正常创建 |
| 8 | 确认发送时每月上限已满 | 409 CAP\_REACHED，其余同第 4 条 |
| 9 | 任务不是 awaiting\_confirm 且已超个人上限 | 409 TASK\_STATE\_CONFLICT（状态检查在前） |
| 10 | 追问时已超个人上限 | 409 USER\_CAP\_REACHED；无 chat\_messages、llm\_calls 新行；网关桩未被调用；last\_activity\_at 不变 |
| 11 | 追问预检通过，网关返回 USER\_CAP\_REACHED（桩） | 409 USER\_CAP\_REACHED，不是 502 |
| 12 | 追问时每月上限已满 | 409 CHAT\_CAP\_REACHED（V1.0 断言保留） |
| 13 | 他人今天 sent 3 次、本人今天 purpose=test 的 sent 3 次，个人上限 2 | 本人确认发送与追问都可进行 |
| 14 | 两段的审核，个人上限 = 今天已用 + 1 | 第一段 sent，第二段失败，轮次 partial |
| 15 | 个人上限 0 | 不检查个人计数（大量 sent 也可发送） |

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

**文件范围**：`backend/app/db/models.py`、`backend/app/db/alembic/versions/0016_admin_roles.py`（新）、`backend/app/security/deps.py`、`backend/app/admin/*.py`（替换权限依赖）、`backend/app/auth/router.py`（提醒按角色）、`backend/app/admin/users.py`、`backend/app/cli.py`（只改创建管理员的角色值）、`backend/app/redaction/engine.py` 与 `backend/app/redaction/router.py`（“以后自动替换”）、`frontend/src/router/index.ts`、`frontend/src/layouts/MainLayout.vue`、`frontend/src/views/settings/SettingsLayout.vue`、`frontend/src/views/settings/Users.vue`、`frontend/src/components/ManualRedactDialog.vue`、`frontend/src/components/SuspectList.vue`、`frontend/src/stores/auth.ts`、对应测试。

### 6.1 角色

`users.role` 取值由 user / admin 改为：`user`（普通用户）、`sys_admin`（系统管理员）、`biz_admin`（业务管理员）、`admin`（两者兼任）。迁移 0016（down\_revision=0015）：原 admin 保持 admin（升级后能力不变）。

| 设置页与接口 | sys\_admin | biz\_admin | admin |
|-|-|-|-|
| 账号 `/admin/users*` | ✓ | | ✓ |
| 模型 `/admin/settings/model*`、`/admin/usage` | ✓ | | ✓ |
| 数据：备份、保留期、外发记录、系统状态、审计日志 | ✓ | | ✓ |
| 审核要求：`/admin/company-requirements*`、`/admin/company-profile`、`/admin/requirement-versions` | | ✓ | ✓ |
| 脱敏：`/admin/settings/redaction`、`/admin/terms*`、`/admin/our-parties*` | | ✓ | ✓ |

* `deps.require_sys_admin`、`deps.require_biz_admin` 取代 `require_admin`；无权限 403（错误码不变）。
* 管理员提醒：备份、磁盘、调用上限、RETENTION\_REVIEW 给系统管理员；PROMPT\_TERM\_CONFLICT 给系统管理员和业务管理员（它会让所有审核被拦截）。
* 提醒条“本次收起”（V2Q-52）：MainLayout 每条 `el-alert` 改为可关闭，关闭按钮文字“本次收起”（`close-text`），其他样式不变。`stores/auth.ts` 增加只在内存中的已收起集合，键为“code + 提醒文字”；`MainLayout` 只显示不在集合中的提醒；退出登录、重新登录（auth 状态清空）时清空集合；页面刷新自然恢复。不得使用 localStorage、sessionStorage、Cookie，不改 `/auth/me` 与服务端。所有提醒都收起后不留空白提醒区。
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
| 4 | 迁移 0016 升级 | 原 admin 仍为 admin；降级时 sys\_admin、biz\_admin 改回 admin（有权限的都保留为管理员） |
| 5 | 停用最后一个 sys\_admin（另有 biz\_admin） | 409 |
| 6 | 任一管理员访问他人任务、轮次、问题、对话 | 404（A-06） |
| 7 | 普通用户勾选“以后自动替换”保存“示例简称” | redaction\_terms 新增 custom 词条，created\_by 为该用户；下一个任务自动替换 |
| 8 | 勾选的文字出现在固定提示词中 | 422 TERM\_IN\_PROMPT，本次替换不保存 |
| 9 | /auth/me | 返回 role；提醒按 6.1 分配 |
| 10 | 前端 | build 通过；浏览器检查在 V2-C-08 |
| 11 | 提醒条“本次收起”（V2Q-52，前端） | build 通过；浏览器检查在 V2-C-08：收起后在智能审核、审核历史、设置之间切换仍不显示；刷新后重新显示；退出再登录重新显示；同一 code 文字变化后重新显示；收起一条时其他提醒仍显示；源码中无 localStorage / sessionStorage / document.cookie |

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
2. 浏览器清单（四种宽度）：管理员提醒条“本次收起”（6.3 第 11 条）；数据保留五项设置（含旧版非档位值的显示与 422 提示、保存后提醒消失）；审核历史“保留至”列与结果页到期提示；系统状态的占用与可用天数；审计日志分区；个人限额设置与超限提示；重复文件弹窗；三种管理员角色看到的设置导航与提醒；“以后自动替换”勾选。
3. 在测试服务器（用户已授权的专用测试机）上演练：
   * 用 v1.0.0 的库执行升级迁移两次：一次未保存过原件保留期，确认五项全部为永久、提醒出现、清理不删数据；一次在 V1.0 中先把原件保留期存为 30 天，确认升级后仍为 30、新增四项为永久、清理只删 V1.0 同样会删的原件（2.6）；
   * 把测试数据的活动时间回拨后执行清理，四层结果与 2.7 一致；
   * 执行 rotate\_key.sh 一次，之后上传、审核、追问、导出、恢复演练（用更换前的备份 + 旧主密钥）均正常。
4. 8 个审核并发（模拟模型）时 worker 无连接池超时，记录内存。

## 九、批次审核（V2-C-09，Opus）

核对测试用例未被删改；数据删除与密钥更换的实现逐行审阅；A-06；升级不误删（2.6）；审阅 V2-C-08 报告；汇报用户，确认后进入 V2-D-00。

## 十、新增待确认问题

见 docs/v2/questions/V2-C.md（V2Q-17～V2Q-22）；后续增补 V2Q-52（提醒条本次收起）、V2Q-53（迁移编号顺延）、V2Q-61～63（C-02 入口、升级保护、清理计数）、V2Q-64（存储统计来源，第三节）、V2Q-65（C-04 上限预检入口，第四节）。
