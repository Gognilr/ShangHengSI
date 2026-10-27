# V2-D 智能助手

2026-10-27 · Opus 5.5 细化（V2-D-00）· 用户已确认（2026-10-27，含 V2Q-115～V2Q-128；CLAUDE.md 已按第十二节修改）

范围见 [PLAN.md](../PLAN.md) 第三节 V2-D；执行规则见 [TASKS-V2.md](../TASKS-V2.md) 第一节。本文件是 V2-D 各任务的接口契约，任务卡自包含。规格变更汇总在 [SPEC-V2.md](../SPEC-V2.md) D 部分。界面按 V2-U 修订后的 [frontend.md](../../design/frontend.md) 实现。

## 〇、结论与共同约定

一句话：在“智能审核”之外增加一个**带脱敏与还原的文件会话**。用户把文件（或粘贴的文字）放进会话，确认一次脱敏后自由提问，或用预置模板完成翻译、摘要、起草回复、单证核对、比价；模型只看到代号，回复在本地还原后边生成边显示，可导出 Word / Excel。

| 项 | 内容 | 任务 |
|-|-|-|
| D1 | 会话的数据与接口：会话就是 `kind='assistant'` 的任务，复用文件、解析、脱敏、对照表、出口复检、保留与删除；新增消息表；文件可分批加入 | V2-D-01 |
| D2 | 会话用的中性代号 `[[ORG_1]]`：不分语言、每个对象唯一可还原；分批加入文件时已有代号不变 | V2-D-02 |
| D3 | 对话：组装外发内容（代号说明、文件、历史、问题）、问题脱敏与确认、worker 逐段生成、本地逐段还原、停止与失败 | V2-D-03 |
| D4 | 五个模板：翻译（分段、对照）、摘要、起草回复、单证核对、比价（模型只对齐代号，金额在本地还原计算） | V2-D-04 |
| D5 | 知识库（E8）与“参考我的历史合同” | V2-D-05 |
| D6 | 导出 Word / Excel、保留期、删除、审计、外发记录 | V2-D-06 |
| D7 | 前端：顶栏“智能助手”、三栏页面、模板、确认脱敏、导出 | V2-D-07 |
| D8 | 前端：设置“知识库”页与外发记录用途 | V2-D-08 |

**顺序**：D-01 → D-02 → D-03 → D-04 → D-05 → D-06 → D-07 → D-08 → D-09（集中测试）→ D-10（Opus 审核）。D-02 改脱敏引擎，单独一个任务、测试先行，D-03 起都依赖它。

**不做**：会话共享与部门（PLAN“不在 V2”）；带批注的 Word；.eml；图片外发；联网搜索；会话中的本地校验（L-01～L-12 只属于智能审核）；模型自动切换；手机适配。会话里不出现“审核通过”“已安全”之类判断。

### 共同约定

1. **开工条件**：V2-U-09 已通过（2026-10-27）；本文件、V2Q-115～V2Q-128 与第十二节修改稿经用户确认，CLAUDE.md 已由 Opus 按 12.1 修改。
2. **安全不变量**（每个任务都适用，测试 T-47 第 1～6 条贯穿全批）：
   * 发给模型的只有：脱敏后的文件正文、脱敏后的用户消息与历史消息、代号说明（只有类别、角色、国家，没有真实值）、模板说明、经脱敏与逐段预检的知识库片段和历史条款。不发文件名、图片、原文件。
   * 每次模型调用都经 `gateway`：上限 → 出口复检 → 发送 → 记录 `llm_calls`（purpose=`assist`）。
   * 消息、片段、模型回复在数据库中只存代号形式；真实值只在对照表（加密）里；还原只在返回给本人时进行。
   * 所有会话接口按 owner\_id 校验，非本人一律 404；管理员也不能看他人会话。
   * 日志只写 id、数量、耗时、状态码；不写消息、文件名、片段、会话标题。
3. **复用优先**：会话复用 `tasks` 及其子表（第二节）。审核的行为、外发文本、提示词、报告**一字不变**；现有测试只能追加不能修改（D-02 对引擎的改动以“默认参数下输出不变”为前提，由脱敏回归集与 A-03 保证）。
4. **迁移编号**：V2-E 用到 0022。本批 D-01 用 0023、D-05 用 0024；不同时按“最大编号加一”顺延并在批次问题文件中记录。
5. **测试**：每节测试用例逐条写成测试，不得删改，可以多写。D-02、D-03、D-05 测试先行。改了脱敏引擎的任务（D-02）另跑 `test_redaction_golden.py` 与 `test_a03_outbound.py`。模型一律用 `httpx.MockTransport` 替身（含流式返回），向量引擎用替身。前端任务跑 build 与整仓挂载的 `test_frontend_style.py`、`test_forbidden_words.py`。
6. **外网**：仍只有 gateway.py 与 lawsync/client.py 可以导入网络库（T-20）；流式发送写在 gateway.py 内。
7. **金额**：比价等本地计算一律 Decimal（ROUND\_HALF\_UP）。
8. **界面**：按 frontend.md（V2-U 修订版）；颜色只用变量；骨架屏只在 V2Q-105 两处；模型回复的 Markdown **不得用 v-html**（第九节 9.4）。
9. **提交**：只暂存本任务文件；提交说明以任务编号开头，用英语或日语。

## 一、使用场景（V2-D-00 整理）

用户是生产型、贸易型企业的业务、单证、采购、法务人员。公司将屏蔽公共 AI 网站，本系统是唯一获准的通道，所以场景以“日常拿公共 AI 做的事”为准，再按“对方名称、金额、账号不能出去”收紧。

| # | 场景 | 输入 | 期望输出 | 设计要点 |
|-|-|-|-|-|
| 1 | 翻译外文合同、信用证条款、产品说明 | 1 个文件，目标语言 | 译文；中外对照；可导出 Word | 文件常超过单次输出上限 → 分段；名称、金额按原写法还原；代号不能被“翻译掉”→ 中性代号（D2） |
| 2 | 读懂对方来函 / 长邮件并起草回复 | 粘贴的邮件正文，或文件 | 回复草稿，语气可选 | 粘贴文字与文件同样脱敏、确认 |
| 3 | 长文件摘要（招标文件、框架协议、制度） | 1～3 个文件 | 要点、义务、期限、风险提示 | 只发确认过的文件 |
| 4 | 单证一致性核对（发票、装箱单、提单、信用证） | 2～6 个文件 | 不一致项表格；可导出 Excel | 同一值在各文件中是同一代号，模型凭“代号是否相同”判断一致，不需要看到真实值 |
| 5 | 多家报价比价 | 2～6 份报价单 | 比价表（最低价、差额百分比、总价）+ 条件差异说明 | 价格被替换为代号，模型无法比大小 → 模型只做“对齐”，数字在本地还原后计算（D4） |
| 6 | 按公司制度、产品资料回答 | 问题（可无文件） | 引用知识库的答复 | 知识库片段同样脱敏、逐段预检（D5） |
| 7 | “我们以前的合同里类似条款怎么写” | 一个条款或问题 | 本人历史合同中的相似条款与比较 | 只检索本人任务；来源任务的对照表先替换（D5） |
| 8 | 自由问答（解释术语、改写一段话） | 粘贴文字 | 回答 | 无文件也能用；每条消息都脱敏确认 |

## 二、会话的数据与接口（V2-D-01）

**文件范围**：`backend/app/db/models.py`、`backend/app/db/alembic/versions/0023_assistant.py`（新）、`backend/app/assistant/`（新包：`__init__.py`、`router.py`、`service.py`、`schemas.py`）、`backend/app/main.py`（只注册路由）、`backend/app/tasks/pipeline.py`（只加“只解析新文件”分支）、`backend/app/tasks/service.py`（只让列表、详情、删除按 kind 区分）、`backend/app/tasks/router.py`（只加 kind 过滤）、`backend/app/backup/retention.py`（第七节第 4 条的条件）、对应测试（`test_assistant_sessions.py` 新；`test_migrations.py`、`test_tasks_api.py`、`test_retention.py` 只追加）。

### 2.1 会话 = 任务（V2Q-115）

`tasks` 增加 `kind varchar(10) not null default 'review'`（CHECK review / assistant）。会话用到的列：

| 列 | 会话中的含义 |
|-|-|
| title | 会话标题。新建时为“新会话”；第一次加入文件后若仍是“新会话”，改为第一个文件名；用户可改名（≤ 60 字）。**不从消息内容生成标题**（消息可能含真实名称） |
| stance / source | 新建时 `other` / `incoming`；第一个文件确认前可改（影响我方识别与代号说明中的角色） |
| policy | 固定 `ai`（会话没有“不外发”） |
| status | `active`（可对话）、`parsing`、`parse_failed`、`awaiting_confirm`；**新增枚举值 `active`**，审核任务不会用到 |
| last\_activity\_at | 发消息、加文件、确认时更新；保留期按它计算 |

`task_files` 增加 `confirmed_at timestamptz null`：会话确认时写入；审核任务也在确认发送（create\_run）时写入，便于统一判断（只写不读，不影响审核）。

审核侧所有入口按 kind 隔离：`GET /tasks` 只列 `kind='review'`；`GET/PATCH/DELETE /tasks/{id}`、`/runs`、`/chat`、`/redaction*`（第 2.4 条例外）、`/report` 对 `kind='assistant'` 返回 404，反之会话接口对审核任务返回 404。`owned_task` 依赖加一个 `kind` 参数，默认 `review`。

### 2.2 消息表 `assistant_messages`（迁移 0023）

| 列 | 类型 | 说明 |
|-|-|-|
| id | bigint pk | |
| task\_id | uuid fk tasks on delete cascade | |
| role | varchar(10) | user / assistant |
| template | varchar(20) null | translate / summary / reply / crosscheck / compare；自由问答为空 |
| params | jsonb not null default '{}' | 模板参数（目标语言、所选文件 id、语气等），**不含正文** |
| content\_redacted | text not null default '' | 代号形式；生成中不断追加 |
| status | varchar(10) | user 消息恒为 `done`；assistant：`queued` / `streaming` / `done` / `failed` / `stopped` |
| error\_code | varchar(40) null | 失败原因代码（第 4.7 条） |
| extras | jsonb not null default '{}' | 附带信息计数：`{kb_sent, kb_dropped, history_sent, history_dropped, files:[file_id], segments_total, segments_done}`；比价与翻译的结构化结果见第五节 |
| tokens\_in / tokens\_out | int not null default 0 | 多次调用时累加 |
| created\_at / updated\_at | timestamptz | |

索引 `(task_id, id)`。同一会话同一时间最多一条 `queued/streaming` 的 assistant 消息（部分唯一索引 `WHERE status IN ('queued','streaming')`）。

### 2.3 接口（全部需登录，按 owner 校验，非本人或 kind 不符 404）

| 方法与路径 | 说明 |
|-|-|
| `POST /assistant/sessions` | 新建空会话，返回会话详情；审计 `assistant_session_create` |
| `GET /assistant/sessions?q=&page=` | 本人会话列表，按 last\_activity\_at 降序，每页 30；字段 id、title、status、file\_count、last\_activity\_at、original\_deleted（是否已过原件保留期） |
| `GET /assistant/sessions/{id}` | 详情：会话字段、files（同 TaskDetail.files，另有 confirmed\_at）、`can_send` 与 `send_reason`（第 4.1 条）、`code_count` |
| `PATCH /assistant/sessions/{id}` | 改 title；status 为 active 且没有已确认文件时可改 stance / source |
| `DELETE /assistant/sessions/{id}` | 与删除任务相同（原件、子表、排队 job、外发记录中的标题清理）；审计 `assistant_session_delete` |
| `POST /assistant/sessions/{id}/files` | multipart，一次 1～5 个文件；检查与新建任务相同（扩展名、内容识别、大小、OCR 预算按会话累计）；会话文件合计 ≤ 10 个（`MAX_FILES`）；status 必须是 active，否则 409 `SESSION_BUSY`；写入后 status=parsing，入队 `parse`（payload 带 `file_ids`） |
| `DELETE /assistant/sessions/{id}/files/{fid}` | 只能移除 **未确认** 的文件（confirmed\_at 为空），否则 409 `FILE_CONFIRMED`；删除原件、节点、该文件的替换项，重算脱敏（2.4）；没有其他未确认文件时 status 回到 active |
| `GET /assistant/sessions/{id}/files/{fid}/nodes`、`.../raw` | 预览，与任务接口相同（复用实现） |

### 2.4 解析只做新文件

`parse_task` 在 `task.kind == 'assistant'` 且 payload 有 `file_ids` 时：只删除并重建这些文件的 doc\_nodes，其他文件的节点不动；OCR 预算从已有文件的页数累计；不跑 `run_checks`（V2Q-125）；脱敏调用 `redact_task(db, task, style='neutral', pin=True)`（第三节）；成功后 status=awaiting\_confirm；失败时 status=parse\_failed，error\_message 写明文件名和原因（同审核）。parse\_failed 的会话只能“移除失败的文件”或删除会话；移除后回到 active。

会话的脱敏确认页复用现有 `/tasks/{id}/parties`、`/tasks/{id}/redaction*` 接口：这几个接口对 `kind='assistant'` 也开放（仍校验 owner 与 `awaiting_confirm` 状态）。确认用会话自己的接口（第 4.2 条）。

### 2.5 测试用例（T-47）

| # | 场景 | 期望 |
|-|-|-|
| 1 | 他人的会话：详情、列表、改名、删除、加文件、消息、导出、预览 | 一律 404；列表不出现 |
| 2 | 用会话 id 调审核接口（/tasks/{id}、/runs、/chat、/report），用任务 id 调会话接口 | 404 |
| 3 | `GET /tasks` | 不含会话 |
| 4 | 新建会话 | title=新会话，status=active，stance=other，policy=ai；审计一条 |
| 5 | 加 2 个文件 | status=parsing，job payload 含两个 file\_id；解析成功后 awaiting\_confirm；第一次加文件后标题为第一个文件名 |
| 6 | 已有 1 个已确认文件，再加 1 个 | 只重建新文件的节点（旧文件节点 id 不变）；不产生 local\_findings |
| 7 | 合计第 11 个文件；parsing 中再加文件 | 422 TOO\_MANY\_FILES；409 SESSION\_BUSY |
| 8 | 移除已确认文件；移除未确认文件 | 409 FILE\_CONFIRMED；成功，原件删除，status 回 active |
| 9 | 新文件解析失败 | parse\_failed 与文件名原因；移除后回到 active，旧文件与消息不受影响 |
| 10 | 删除会话 | 消息、文件、对照表级联删除；原件文件删除；审计一条 |
| 11 | 迁移 0023 | 列、表、约束、索引的创建与回退；旧任务 kind=review |
| 12 | 日志 | 全部会话接口的日志不含标题、文件名、消息（用虚构值断言） |

## 三、中性代号与稳定编号（V2-D-02，测试先行）

**文件范围**：`backend/app/redaction/placeholders.py`、`backend/app/redaction/engine.py`、`backend/app/chat/service.py`（只把 `_redact` 的代号分配改为可注入）、对应测试（`test_neutral_codes.py` 新；`test_redaction_golden.py`、`test_a03_outbound.py` 只运行不修改）。

### 3.1 为什么（V2Q-116）

审核用的代号（我方、客户\_A、[金额\_1]、[电话]、[地址：阿联酋]）带中文词，翻译成英文时模型会把“客户\_A”译成 “Customer\_A”，还原就失败；而且 [电话]、[地址：国家] 多个对象共用一个代号，翻译和核对时还原会张冠李戴。会话改用**不分语言、每个对象各自编号**的代号；审核不变。

### 3.2 格式（`style='neutral'`）

一律 `[[` + 类别 + `_` + 序号 + `]]`，序号从 1 起、按首次出现顺序，同一会话同一对象始终同一代号：

| 类别 | 代号 | 类别 | 代号 |
|-|-|-|-|
| 我方 | `[[OUR]]`（我方的全部写法共用，唯一一个不带序号的） | 账号 | `[[ACCT_n]]` |
| 其他企业（客户、供应商、银行等） | `[[ORG_n]]` | SWIFT / 银行代码 | `[[SWIFT_n]]` |
| 人名 | `[[PER_n]]` | 金额 | `[[AMT_n]]` |
| 项目 | `[[PRJ_n]]` | 单价 | `[[PRICE_n]]` |
| 编号 | `[[REF_n]]` | 数量（开启数量脱敏时） | `[[QTY_n]]` |
| 电话 | `[[TEL_n]]` | 自定义敏感词 | `[[TERM_n]]` |
| 邮箱 | `[[EMAIL_n]]` | 联系方式（IM） | `[[IM_n]]` |
| 网址 | `[[URL_n]]` | 证件号 | `[[ID_n]]` |
| 邮编 | `[[ZIP_n]]` | 地址 | `[[ADDR_n]]`（国家写在代号说明里） |

* 原来共用代号的类别（电话、邮箱、网址、证件号、邮编、地址、IM）在会话中按“规范化后的真实值”逐个编号，对照表中每个代号只对应一个值，**全部可唯一还原**。
* 同一对象的其他写法（简称、大小写）仍写为 `代号#序号`，只用于出口复检，与审核相同。
* 比例文字（9.6）、报价汇总行等审核专用的插入在会话中**不生成**（`ratio=False`），会话的外发正文只有替换，没有附加说明。
* 问题与片段中新出现的对象（原追问的 `[公司_Q1]`）在会话中同样用中性格式、接着本会话的序号分配（`[[ORG_7]]`），写入同一张对照表。

### 3.3 稳定编号（`pin=True`）

分批加文件、改签约方、处理疑似项都会让 `compute()` 重算。会话中已经出现在消息里的代号必须保持不变：

* `compute(..., style='neutral', pinned: dict[str, str])`：pinned 为当前对照表 `{代号: 真实值}`（不含 `#` 键）。分配代号时，规范化后的真实值与类别都相同的对象沿用 pinned 中的代号；新对象的序号从“该类别在 pinned 中的最大序号 + 1”开始；pinned 中的代号在本次重算中没有再出现的，**仍保留在对照表里**（历史消息要靠它还原，出口复检也要用它的真实值）。
* `[[OUR]]` 固定；某对象从“其他企业”改为“我方”时，旧的 `[[ORG_n]]` 条目保留（历史还原），新文本中改用 `[[OUR]]`。
* 默认参数（`style='review'`、`pinned=None`、`ratio=True`）下 `compute()` 的全部输出与现在逐字相同——脱敏回归集与 A-03 不改一字地通过。

### 3.4 实现约定

* `placeholders.py` 增加 `NeutralAllocator`（与 `Allocator` 同接口：`org(key, role)`、`numbered(category, key)`、`fixed(category, key)`、`address(country, key)`、`our()`，以及 `seed(pinned)`）；engine 中原来直接写 `FIXED[...]`、`address_code(...)`、`OUR_CODE` 的地方改为经 allocator 取代号。
* `redact_task(db, task, style='review', pin=False)`：会话调用时 `style='neutral', pin=True`，pinned 从当前对照表读取（第一次为空）。第二步的重算入口（签约方修改、疑似决定、手动补充、撤销）按 `task.kind` 自动选择同样参数。
* `chat._redact` 的代号分配改为传入函数：审核追问仍用 `chat_code`（Q 前缀，不变）；会话用 `NeutralAllocator` 续号。
* 新增 `placeholders.NEUTRAL = re.compile(r"\[\[(?:OUR|[A-Z]+_\d+)\]\]")`，供还原与流式截断（4.5）使用。

### 3.5 测试用例（T-48）

| # | 场景 | 期望 |
|-|-|-|
| 1 | 默认参数跑脱敏回归集与 A-03 | 与现在逐字相同（两文件不改动） |
| 2 | 虚构合同，neutral | 我方→`[[OUR]]`；买方→`[[ORG_1]]`；两个不同电话→`[[TEL_1]]`、`[[TEL_2]]`；两个不同地址→`[[ADDR_1]]`、`[[ADDR_2]]`；外发正文中没有比例文字 |
| 3 | neutral 的对照表 | 每个非 `#` 代号对应一个值；`restore(外发正文, 对照表)` 等于原文（逐节点断言） |
| 4 | 同一金额出现在两个文件 | 同一个 `[[AMT_n]]`；不同金额不同代号 |
| 5 | 先确认文件 A，再加文件 B（B 中有 A 的买方和一个新公司） | A 的全部代号不变；买方沿用 `[[ORG_1]]`；新公司为 A 中最大 ORG 序号 + 1 |
| 6 | pinned 中的对象在重算后不再出现（移除未确认文件后） | 代号与真实值仍在对照表；出口复检仍拦截该真实值 |
| 7 | 把 `[[ORG_1]]` 对应的企业改设为我方 | 新外发正文用 `[[OUR]]`；`[[ORG_1]]` 条目保留 |
| 8 | 会话中提问出现对照表没有的公司名 | 得到下一个 `[[ORG_n]]`，写入对照表；同一问题中重复出现只分配一次 |
| 9 | 审核追问 | 仍是 `[公司_Q1]`，与现在相同 |
| 10 | 代号正则 | `NEUTRAL` 匹配全部格式；不匹配 `[公司_Q1]`、`[金额_1]`、`[[ORG_]]` |

## 四、对话（V2-D-03，测试先行）

**文件范围**：`backend/app/assistant/service.py`、`backend/app/assistant/prompt.py`（新）、`backend/app/assistant/job.py`（新）、`backend/app/assistant/stream.py`（新，增量还原）、`backend/app/assistant/router.py`、`backend/app/assistant/schemas.py`、`backend/app/llm/gateway.py`（加 `stream_model` 与 purpose `assist`）、`backend/app/llm/usage.py`（只加“剩余次数”查询）、`backend/app/redaction/recheck.py`（只加 `extra_task_ids`）、`backend/app/worker.py`（加 assist 组）、`backend/app/config.py`（加并发设置）、`backend/resources/prompts/assistant.md`（新）、`backend/app/db/alembic/versions/0023_assistant.py`（llm\_calls.purpose 的 CHECK 加 `assist`，与 D-01 同一迁移；若 D-01 已提交则在本任务追加 0023a——按共同约定第 4 条顺延）、对应测试（`test_assistant_chat.py`、`test_assistant_stream.py`、`test_gateway_stream.py` 新；`test_gateway.py`、`test_recheck.py` 只追加）。

### 4.1 能不能发送（`can_send` / `send_reason`）

依次判断，第一个不满足的作为原因：

1. 有文件已过原件保留期 → “原件已按保留期限删除，本会话只能查看”（之后任何发送 409 `SESSION_READONLY`）；
2. status 为 parsing → “文件正在解析，完成后可以继续”；parse\_failed → “有文件解析失败，请移除后继续”；awaiting\_confirm → “请先确认新加入文件的脱敏”；
3. 已有 queued / streaming 的回复 → “上一条回复尚未完成”；
4. 模型未配置、本月或个人当日上限已到 → 网关同样的文字。

没有文件的会话（status=active）可以发送。

### 4.2 确认脱敏 `POST /assistant/sessions/{id}/confirm`

与审核的确认发送使用同一把任务行锁，重读状态：只在 awaiting\_confirm 时允许；没有未处理的疑似遗漏；我方 0 或 1 个（会话不要求必须有我方，V2Q-117）；对全部文件的外发正文跑出口复检（同 create\_run 的预检），命中则 409 `OUTBOUND_BLOCKED`（只带类别和位置）。通过后：未确认文件写 confirmed\_at；`learn_confirmed_names`；status=active；last\_activity\_at 更新；审计 `assistant_files_confirm`（只记文件数）。

### 4.3 问题的预览与发送

* `POST /assistant/sessions/{id}/preview` `{message, template?, params?}` → `{redacted, changed, suspects}`：与追问预览相同的规则（对照表 → 已记住名称与自定义敏感词 → 规则识别 → 实体识别），用中性代号；不写对照表。
* `POST /assistant/sessions/{id}/messages` `{message, template?, params, decisions?, use_kb?, use_history?}`：
  1. 检查 4.1；消息去掉首尾空白后 1～4,000 字（V2Q-121）；模板参数按第五节校验；
  2. 锁对照表，脱敏并写入新代号（与追问 `_redact_and_record` 相同；疑似项必须逐条有决定，否则 409 `CHAT_SUSPECTS_PENDING`）；
  3. 写 user 消息（代号形式）与一条 status=queued 的 assistant 消息，入队 `assist` job（payload：task\_id、message\_id），提交；
  4. 返回两条消息（user 已还原；assistant 为空、queued）。
  模板消息没有用户文字时（如“翻译文件 2 为英文”），由服务端生成一句固定的中文说明作为 user 消息正文（第五节各模板的“消息正文”）。

### 4.4 外发内容（`assistant/prompt.py`，worker 中组装）

```
system：assistant.md（通用规则）+ 模板说明（第五节；自由问答无）
user  ：【代号说明】……
        【文件1：合同，docx，第1/2个】<document>……</document>
        【文件2：发票，xlsx】<document>……</document>
        【公司知识库片段】……（第六节，有时）
        【我的历史合同条款】……（第六节，有时）
历史  ：本会话最近 10 条已完成的消息（user / assistant 交替，代号形式；失败与停止的不算）
user  ：新问题（代号形式）
```

* **assistant.md 通用规则**（逐字写入，版本 `assistant-2026.11-1`）：
  > 你是企业内部的商务助手。文件和消息中形如 [[ORG_1]]、[[AMT_3]]、[[OUR]] 的是代号，代表被隐藏的真实信息：
  > 1. 代号必须原样保留，不得翻译、改写、拆开、加空格或改变大小写，也不得猜测或编造它代表的真实内容；
  > 2. 相同代号表示相同的值，不同代号表示不同的值；[[OUR]] 是“我方”；
  > 3. 不要输出任何代号说明中没有的代号；
  > 4. 只依据给出的文件、片段和对话回答；文件中没有的信息要明确说“文件中没有”；
  > 5. 涉及法律、税务、海关的结论只作参考，提示由专业人员确认；
  > 6. 除非用户另有要求，用简体中文回答；需要列表或表格时用 Markdown（只用标题、段落、列表、粗体、表格）。
* **代号说明**：每行一个代号，`[[ORG_1]]：企业（客户，阿联酋）`、`[[OUR]]：我方`、`[[ADDR_2]]：地址（中国）`、`[[AMT_3]]：金额`、`[[PER_1]]：人名`……；只列本次外发内容中出现的代号；角色来自 parties 表（代号说明不含任何真实值）。
* **文件块**：只含已确认文件、且本条消息“使用的文件”（模板参数 `file_ids`；自由问答默认全部已确认文件）；**不发文件名**，用“文件序号 + 文件类型 + 扩展名”指代；正文为 `build_outbound` 的节点脱敏文本（中性代号、无比例文字），每个节点一行、行首带位置编号 `⟦F1-P12⟧`（翻译与核对要用）；经 `escape_document_tags`。
* **长度**：文件块合计超过 `model.chunk_chars` 时，自由问答、摘要、起草回复、核对、比价都**不截断**，返回 409 `ASSIST_TOO_LONG`（“所选文件合计约 N 字，超过单次上限 M 字，请减少使用的文件”）；翻译按第五节分段，不受此限。
* 整个 messages 在网关出口复检；recheck 的上下文为本会话对照表、已记住名称与自定义敏感词，另加历史条款来源任务的对照表（`extra_task_ids`，第六节）。

### 4.5 生成：worker 逐段写入，前端轮询（V2Q-118）

* worker 增加 `assist` 组（`worker_assist_concurrency`，默认 4），只处理 `assist` job，不与审核、解析抢位置。
* `gateway.stream_model(db, purpose='assist', messages, user_id, task_id, task_title, on_delta)`：与 `call_model` 相同的“上限 → 出口复检”，然后以 `"stream": true, "stream_options": {"include_usage": true}` 发送，按 SSE 逐行解析 `choices[0].delta.content`，每收到一段调用 `on_delta(text)`；不重试；读超时为设置中的超时（两段之间超过即失败），总时长上限 600 秒；结束后写一条 llm\_calls（sent，request\_text 与完整 response\_text、token）；中途失败写 error（response\_text 为已收到的部分，截 2,000 字）。`on_delta` 返回 False 时停止接收并按 sent 记录已收到的部分（用户停止）。
* job 每累计 ≥ 200 字或距上次写入 ≥ 0.5 秒，把 `content_redacted` 写入数据库并提交，status=streaming；完成后 status=done，写 tokens。
* **停止**：`POST /assistant/sessions/{id}/messages/{mid}/stop` 把 queued 改为 stopped（job 取到后直接结束），streaming 的写入 `extras.stop_requested=true`；job 每次写入前读取，看到后让 `on_delta` 返回 False，status=stopped。
* **心跳与重启**：job 被重新入队（worker 重启）时，若消息已有内容，不再重发，直接置 failed（`ASSIST_INTERRUPTED`），避免重复计费。

### 4.6 增量还原（`assistant/stream.py`）

* `GET /assistant/sessions/{id}/messages?after_id=` 返回消息列表（已还原）；`GET /assistant/sessions/{id}/messages/{mid}?offset=n` 返回 `{status, text_from_offset, offset, error}`：服务端读取 content\_redacted，取**安全前缀**——若末尾有未闭合的 `[[`（其后不足以构成完整代号），截到 `[[` 之前；对安全前缀做 `restore`，返回从 offset（还原后文本的字符位置）起的新增部分和新 offset。前端每秒轮询一次，直到 status 为终态。
* 还原只用会话对照表中的中性代号；模型写出对照表中不存在的代号时原样显示（前端用醒目样式标出，第九节）。
* 中性代号全部以 `[[` 开头、`]]` 结尾，所以安全前缀的判断只看最后一个 `[[`，不会出现“客户\_A / 客户\_AB”式的歧义。

### 4.7 失败

error\_code 与中文：`BLOCKED`（出口复检发现未脱敏的信息，已拦截，未发送）、`CAP_REACHED`、`USER_CAP_REACHED`、`NOT_CONFIGURED`、`TIMEOUT`、`HTTP_ERROR`、`BAD_RESPONSE`（同网关文字）、`ASSIST_INTERRUPTED`（服务重启，回复中断，请重试）、`ASSIST_TOO_LONG`。失败的回复保留已生成的部分并显示原因；“重试”= 用同一条 user 消息重新入队一条新的 assistant 消息（不再次脱敏，不再写 user 消息）。

### 4.8 测试用例（T-49）

| # | 场景 | 期望 |
|-|-|-|
| 1 | 外发 messages（模型替身记录请求） | 不含文件名、真实名称、金额、电话；含【代号说明】且只有出现的代号；system 为 assistant.md 原文 |
| 2 | 未确认文件 | 不进入外发；awaiting\_confirm 时发送 409 |
| 3 | 发送含真实公司名的问题 | 外发为 `[[ORG_n]]`；回复中的 `[[ORG_n]]` 还原为该名称 |
| 4 | 疑似项未决定 | 409 CHAT\_SUSPECTS\_PENDING，不写消息、不入队 |
| 5 | 流式替身分 30 段返回，其中一段以 `[[OR` 结尾、下一段以 `G_1]] 的` 开头 | 中间轮询得到的文本从不出现 `[[`；最终文本完整还原 |
| 6 | 轮询 offset | 多次轮询拼起来与最终还原文本逐字相同 |
| 7 | 停止 | status=stopped；llm\_calls 一条 sent，response 为已收到部分 |
| 8 | 网关拦截（在文件块中注入一个真实值） | status=failed、BLOCKED；llm\_calls blocked，request\_text 为空 |
| 9 | 超时（替身两段之间等待超过设置） | failed、TIMEOUT；已生成部分保留 |
| 10 | job 重新入队且已有内容 | failed、ASSIST\_INTERRUPTED，不再调用模型 |
| 11 | 同一会话已有 streaming 时再发 | 409，“上一条回复尚未完成” |
| 12 | 文件合计超过 chunk\_chars（自由问答） | 409 ASSIST\_TOO\_LONG，不写消息 |
| 13 | 历史 | 只带最近 10 条 done 消息；failed、stopped 不带 |
| 14 | 个人每日上限 | assist 调用计入；达到后 409 USER\_CAP\_REACHED |
| 15 | llm\_calls | purpose=assist，task\_id 为会话 id，task\_title 为会话标题 |
| 16 | 确认脱敏 | 有疑似项 409；外发预检命中 409 OUTBOUND\_BLOCKED；通过后 confirmed\_at 写入、status=active、已记住名称更新 |
| 17 | 日志 | 不含问题、回复、标题（虚构值断言） |
| 18 | 重试 | 新 assistant 消息入队；user 消息不重复 |

## 五、模板（V2-D-04）

**文件范围**：`backend/app/assistant/templates.py`（新）、`backend/app/assistant/compare.py`（新）、`backend/app/assistant/translate.py`（新）、`backend/app/assistant/job.py`、`backend/app/assistant/prompt.py`、`backend/resources/prompts/assistant_templates.md`（新，各模板说明逐字）、对应测试（`test_assistant_templates.py`、`test_assistant_compare.py`、`test_assistant_translate.py` 新）。

模板只是“预置的说明 + 参数 + 输出处理”，不新增任何外发类别。参数校验失败 422。

| 模板 | 参数 | 消息正文（无用户文字时） | 说明要点（assistant\_templates.md） | 输出处理 |
|-|-|-|-|-|
| 翻译 translate | file\_id（1 个）、target（9 种：中文、英文、日文、韩文、俄文、西班牙文、法文、德文、阿拉伯文） | “把文件 n 翻译为英文” | 逐行翻译，**保留每行开头的位置编号 ⟦…⟧**；代号原样保留；不增删内容；表格单元格单独成行 | 分段（5.1），保存逐节点译文，可导出译文 / 对照 Word |
| 摘要 summary | file\_ids（1～3）、focus（可选：全部 / 义务与期限 / 金额与付款 / 风险） | “概括文件 1、2 的要点” | 先给 5 条以内的要点，再按“义务、期限、付款、违约、其他”分节；标出所在位置编号 | Markdown；导出 Word |
| 起草回复 reply | file\_ids（0～3）、tone（正式 / 简洁 / 友好坚定）、language（中文 / 英文 / 同来函） | 用户粘贴的来函即消息正文；无文字时“根据文件 1 起草回复” | 先用两三句说明对方的要求，再给可直接使用的回复草稿；不承诺文件中没有授权的事项 | Markdown；导出 Word（只导草稿部分：说明与草稿之间以 `---` 分隔，取其后） |
| 单证核对 crosscheck | file\_ids（2～6） | “核对文件 1～3 之间是否一致” | 比较品名、数量、重量、件数、金额、币种、收发货人、港口、日期、单号；“相同代号即相同的值”；输出表格：项目 \| 各文件的值（代号或原文）\| 是否一致 \| 说明；没发现不一致时明确说明 | Markdown 表格；导出 Excel |
| 比价 compare | file\_ids（2～6，每个文件一家报价） | “比较文件 1～3 的报价” | **只输出 JSON**（5.2），不要计算、不要写数字结论；另用 notes 说明付款、交期、贸易术语、有效期等条件差异 | 本地还原与计算（5.2），显示比价表，导出 Excel |

### 5.1 翻译分段

* 取所选文件全部正文节点（含表格单元格），按顺序拼成 `⟦loc⟧ 文本` 行；每段累计 ≤ `assistant.translate_segment_chars`（设置项，默认 4,000 字）；单个节点超过上限时单独成段。
* 发送前按段数检查剩余次数（`usage.remaining_calls`：本月上限与个人当日上限中较小的剩余量）；不够时 409 `ASSIST_QUOTA`（“翻译需要调用 n 次，今日剩余 m 次”）。
* 每段一次 `stream_model`（不带历史消息，只带代号说明与本段），回复依次追加到同一条 assistant 消息，`extras.segments_total / segments_done` 随之更新；任一段失败即停止，已完成的段保留，status=failed。
* 解析：按 `⟦loc⟧` 把回复切回各节点，存 `extras.translation = {loc: 译文（代号形式）}`；某段缺了部分编号时，该段整段放在第一个节点下并在 `extras.translation_unaligned` 记段号（导出对照表时该段合并显示）。
* **按目标语言还原（V2-D 计划“按目标语言还原”，V2Q-120）**：还原时对每个代号，若对照表中该对象另有写法（`代号#序号`）且其文字的书写系统与目标语言一致（拉丁 / 汉字 / 假名 / 韩文 / 西里尔 / 阿拉伯），用该写法；否则用主写法。界面在译文上方固定提示“名称、地址、金额按原文写法还原，请核对”。

### 5.2 比价：模型对齐，本地计算（V2Q-119）

价格和金额在外发正文中都是代号，模型无法比较大小；也不允许把价格发出去。所以：

1. 模型只返回 JSON（`json_mode=True`，不流式，前端显示“正在对齐报价项目……”）：
   ```json
   {"suppliers": [{"file": 1, "name": "[[ORG_2]]", "currency": "USD", "terms": "FOB"}],
    "rows": [{"item": "不锈钢管 304 Φ25", "unit": "米",
              "quotes": [{"file": 1, "qty": "1000", "unit_price": "[[PRICE_3]]", "amount": "[[AMT_5]]"}]}],
    "notes": "……"}
   ```
2. 本地校验：`unit_price`、`amount` 必须是对照表中类别为单价 / 金额的中性代号，否则该格为“无法核对”（模型写出的任何字面数字都不采用）；`file` 必须是本条消息使用的文件序号。
3. 本地还原并解析为 Decimal（复用 `checks/amounts.py` 的金额解析）；币种取自代号真实值中的币种，没有时取 supplier.currency；**只在同币种之间比较**。
4. 每行：各家单价、最低价所在文件、与最低价的差额百分比 `(p − min) / min × 100`，保留 1 位小数（ROUND\_HALF\_UP）；每家合计 = 该家全部 amount 之和（有任一格无法核对则合计显示“不完整”）。
5. 结果存 `extras.compare`（代号形式的行与计算结果的“位置”，数值不入库；每次查看时现场还原计算），notes 正常还原显示。
6. 界面显示比价表（真实值）与“条件差异”说明，表头注明“价格由本地从文件中还原计算，未发送给模型”。

### 5.3 测试用例（T-50）

| # | 场景 | 期望 |
|-|-|-|
| 1 | 各模板参数缺失或越界（翻译 2 个文件、核对 1 个文件、目标语言不在列表） | 422 |
| 2 | 翻译 9,500 字的虚构文件（段长 4,000） | 3 次调用；segments\_total=3；每段外发不含历史消息 |
| 3 | 剩余次数 2、需要 3 次 | 409 ASSIST\_QUOTA，不调用 |
| 4 | 第 2 段失败 | 第 1 段译文保留；status=failed；segments\_done=1 |
| 5 | 译文对齐 | 每个 loc 有译文；替身漏掉一个编号时该段进入 unaligned |
| 6 | 按目标语言还原 | 对照表有“星河公司 / Xinghe Co.”两种写法：目标英文还原为 Xinghe Co.，目标日文还原为主写法 |
| 7 | 比价替身返回合法 JSON（两家、同币种 USD） | 最低价、差额百分比（Decimal，1 位）、合计正确；外发与 llm\_calls 中无任何价格真实值 |
| 8 | 比价 JSON 中 unit\_price 写成 "12.5" 或不存在的代号 | 该格“无法核对”，合计“不完整” |
| 9 | 两家币种不同 | 不计算差额，标“币种不同” |
| 10 | 比价 JSON 无法解析 | failed、BAD\_RESPONSE |
| 11 | 起草回复导出 | 只含 `---` 之后的草稿 |
| 12 | 模板说明 | system 中为 assistant\_templates.md 对应段的原文 |

## 六、知识库与“参考我的历史合同”（V2-D-05，测试先行，E8）

**文件范围**：`backend/app/db/models.py`、`backend/app/db/alembic/versions/0024_kb.py`（新）、`backend/app/kb/`（新包：`service.py`、`router.py`、`job.py`、`retrieve.py`）、`backend/app/assistant/history.py`（新）、`backend/app/assistant/job.py`、`backend/app/assistant/prompt.py`、`backend/app/admin/settings_store.py`（只加默认值）、`backend/app/main.py`（只注册路由）、对应测试（`test_kb.py`、`test_assistant_history.py` 新；`test_migrations.py` 只追加）。

### 6.1 知识库（业务管理员，全公司范围）

* **表（迁移 0024）**：`kb_docs`（id uuid、name\_enc、status parsing / ready / failed、error\_message、enabled bool、chunk\_count、content\_hash、created\_by、created\_at、updated\_at）；`kb_chunks`（id bigint、doc\_id fk cascade、seq、heading\_enc null、text\_enc、embedding vector(1024) null）。名称与正文加密（同范本）；向量与原文同等对待（V2-E 共同约定第 10 条）：只在库内、不经接口返回。
* **接口**（`require_biz_admin`）：`GET /admin/kb`（列表：名称、状态、启用、片段数、上传人、时间）、`POST /admin/kb`（multipart，docx / pdf / xlsx 与旧格式，单个 ≤ 20 MB，同内容哈希重复时 409）、`PATCH /admin/kb/{id}`（enabled、改名）、`DELETE /admin/kb/{id}`、`GET /admin/kb/{id}/chunks`（预览前 200 段，给管理员核对拆分效果）；审计 `kb_upload`、`kb_update`、`kb_delete`（只记 id 与片段数）。
* **解析任务** `kb_import`（worker 解析组）：复用各解析器（旧格式经 converter；扫描页 OCR 计入同一 OCR 锁）；按段落顺序合并为约 800 字的片段（标题段落开启新片段并作为 heading；表格逐行“列名：值”展开）；逐段计算向量（复用 E5 引擎）。
* **设置**：`assistant.kb_enabled`（默认 false，业务管理员在知识库页打开）；关闭时会话中不显示“附带公司知识库”开关，也不检索。

### 6.2 检索与外发

* 用户在发送时勾选“附带公司知识库”（`use_kb`，开关开启时默认勾选）：问题原文在 worker 中计算向量（只在内存中），在 `enabled` 的 ready 文档的片段中取相似度前 5、≥ `KB_MIN_SCORE`（初值取 `LAW_MIN_SCORE` 0.56，D-09 用评测集复核，V2Q-122）；合计 ≤ 4,000 字。
* 每段：先按会话对照表替换已知真实值，再走与问题相同的脱敏（会写入新的中性代号）；**有任何疑似项（中等置信度实体、疑似遗漏）的片段整段丢弃**（片段无法让用户逐条确认）；再单独出口复检，命中即丢弃。保留的以 `【公司知识库片段】[KB1] …` 形式放入，计入 `extras.kb_sent / kb_dropped`；回复下方显示“附带公司知识库片段 n 段（m 段因含疑似敏感信息未发送）”。
* 文件名、文档名不发送。

### 6.3 参考我的历史合同

* 发送时勾选“参考我的历史合同”（`use_history`，默认不勾选）：用问题原文的向量在**本人的**审核任务（`kind='review'`、owner = 当前用户、原件未删除）的 `task_clause_vectors` 中取前 3、≥ `CLAUSE_PAIR_MIN`（0.70）的一级条款。
* 每条：读取来源任务的节点原文，**先用来源任务的对照表把真实值换成本会话的代号**（来源对象在本会话没有代号的，按类别分配新的中性代号写入会话对照表），再走问题脱敏；有疑似项的整条丢弃；出口复检时把来源任务也列入 `extra_task_ids`（来源任务对照表中的真实值同样拦截）。
* 外发 `【我的历史合同条款】[H1] 合同，2026-05（文件类型、年月，不写文件名和标题）：……`；回复下方列出来源（“来自 2026-05 的一份合同”，可点击在新标签页打开该任务结果页——本人任务，权限天然满足）。

### 6.4 测试用例（T-51）

| # | 场景 | 期望 |
|-|-|-|
| 1 | 普通用户、系统管理员调用知识库接口 | 403（仅业务管理员及兼任）；接口不返回向量 |
| 2 | 上传虚构制度 docx | 片段约 800 字、标题开启新片段；name、text 加密存储；审计一条 |
| 3 | 知识库关闭 | 不检索，不调用引擎（替身断言未调用） |
| 4 | 片段含已记住的名称 | 替换为代号；含疑似人名（中等置信度）的片段丢弃，kb\_dropped=1 |
| 5 | 片段含金额、电话 | 替换为中性代号；外发通过 |
| 6 | 禁用或删除的文档 | 不被检索 |
| 7 | 历史条款只来自本人任务 | 他人任务、会话、原件已删除的任务都不出现 |
| 8 | 来源任务的买方真实名称 | 外发中是本会话的 `[[ORG_n]]`；若被注入未替换，网关因 extra\_task\_ids 拦截 |
| 9 | 外发的历史条款标注 | 只有文件类型与年月，无文件名、标题 |
| 10 | 迁移 0024 | 表、索引的创建与回退 |
| 11 | 日志 | 不含文档名、片段、查询文本 |

## 七、导出、保留、删除、审计（V2-D-06）

**文件范围**：`backend/app/assistant/export.py`（新）、`backend/app/assistant/router.py`、`backend/app/backup/retention.py`、`backend/app/admin/data.py`（外发记录用途筛选加 assist、存储用量统计含会话）、`backend/app/audit/service.py`（只加动作名）、对应测试（`test_assistant_export.py` 新；`test_retention.py`、`test_admin_data.py` 只追加）。

1. **导出** `GET /assistant/sessions/{id}/messages/{mid}/export?format=docx|xlsx`（本人；只能导出 done 或 stopped 的 assistant 消息；审计 `assistant_export`，记 message id 与格式）：
   * docx：按 Markdown 子集（标题、段落、列表、粗体、表格）生成，复用 report\_docx 的字体与页面设置；翻译模板提供 `variant=translation`（只有译文，按节点顺序，表格单元格还原为表格）与 `variant=bilingual`（两栏表格：原文 | 译文）；起草回复只导草稿；页眉“商衡SI 智能助手 · 内容由 AI 生成，仅供参考”。
   * xlsx：消息中的第一个 Markdown 表格（核对）或比价表（含合计与差额，单元格为数值格式）；没有表格时 409 `NO_TABLE`。
   * 文件名 `智能助手-{会话标题前 20 字}-{yyyyMMddHHmm}.{ext}`（上海时间），按 RFC 5987 编码。
   * 导出内容为还原后的真实值，只在响应中生成，不落盘。
2. **保留期**（V2Q-123，复用 V2-C 四层，按 last\_activity\_at）：
   * 原件层：会话的原件与节点原文删除（同任务），会话变为只读（4.1 第 1 条），消息仍可查看与导出；
   * 结果层：会话整体删除（消息、对照表一并删除）；
   * 未完成层：从未成功确认过任何文件、也没有任何消息的会话（`kind='assistant'` 且 `NOT EXISTS assistant_messages`、status 不是 active 或没有文件）；
   * 条件中 `ELIGIBLE_STATUS` 对会话为 `active`；审核任务条件不变。
3. **外发记录**：设置“数据”页的外发记录用途增加“智能助手”（assist），详情与现有相同（只有管理员能看外发记录，这是 V1 既定规则，不变）。
4. **存储用量**：会话的原件计入现有“原件”统计，另列会话数。

测试用例（T-52）：

| # | 场景 | 期望 |
|-|-|-|
| 1 | 导出他人会话消息 | 404 |
| 2 | 核对回复导出 xlsx | 表格行列与回复一致，真实值已还原 |
| 3 | 比价导出 xlsx | 单价、合计为数值单元格；差额百分比正确 |
| 4 | 翻译导出对照 Word | 两栏行数 = 节点数；代号已按目标语言还原 |
| 5 | 无表格的回复导出 xlsx | 409 NO\_TABLE |
| 6 | streaming 中导出 | 409 |
| 7 | 保留期（原件层到期） | 原件与节点原文删除；会话只读，消息可看；发送 409 SESSION\_READONLY |
| 8 | 保留期（结果层到期） | 会话删除 |
| 9 | 未完成层 | 只有空会话与从未确认文件、无消息的会话被删 |
| 10 | 外发记录筛选 assist | 只列会话调用 |
| 11 | 审计 | 导出、创建、删除各一条，detail 不含标题与内容 |

## 八、权限、限额、日志汇总

| 项 | 规则 |
|-|-|
| 谁能用 | 全部登录用户（普通用户、两种管理员）都有“智能助手”菜单，只能看自己的会话 |
| 知识库管理 | 业务管理员（及兼任）；知识库开关同 |
| 并发 | 每个会话同时 1 条生成中的回复；每人同时最多 2 条（第 3 条起 409 `ASSIST_BUSY`）；worker assist 组 4 个 |
| 调用次数 | 每次模型调用（翻译每段一次）计入本月上限与个人当日上限 |
| 消息长度 | 每条 ≤ 4,000 字；历史带最近 10 条；文件块 ≤ chunk\_chars（翻译分段除外） |
| 日志 | 只写会话 id、消息 id、llm\_call id、段数、耗时、错误码 |

## 九、前端（V2-D-07）

**文件范围**：`frontend/src/layouts/MainLayout.vue`（只加菜单项）、`frontend/src/router/index.ts`（只加路由）、`frontend/src/views/Assistant.vue`（新）、`frontend/src/components/assistant/`（新：`SessionList.vue`、`Conversation.vue`、`MessageItem.vue`、`Composer.vue`、`TemplatePicker.vue`、`FilePanel.vue`、`CompareTable.vue`、`TranslationView.vue`）、`frontend/src/components/ConfirmStep.vue`（只加 `mode` prop）、`frontend/src/utils/markdown.ts`（新）、`frontend/src/api/assistant.ts`（新）、`frontend/src/utils/labels.ts`（只加模板、状态、错误码的中文）、`frontend/src/styles/theme.css`（只在需要时追加令牌）。

### 9.1 菜单与路由

顶栏菜单依次为“智能审核”“审核历史”“智能助手”（需改 CLAUDE.md 与 SPEC 1.6，第十二节）。路由 `/assistant`（会话列表 + 空白引导）与 `/assistant/:sessionId`。

### 9.2 三栏工作台（与审核结果页同一套工作台规则：页面不滚动，各栏各自滚动）

```
┌ 会话（264px）──────┐┌ 对话（自适应，内容最大宽 800px 居中）──────────────┐┌ 文件与脱敏（320px）────┐
│ [+ 新建会话]        ││ 会话标题（可点改名）                    [删除会话] ││ 文件（3）  [+ 加入文件] │
│ 🔍 搜索会话          ││ ──────────────────────────────────────── ││ DOC 合同.docx  已确认    │
│ 今天                ││ 你：把文件 1 翻译为英文              14:02     ││ XLS 发票.xlsx  待确认    │
│  报价比较 3 家  ●    ││ 助手：（逐段出现）……                          ││ [确认脱敏（2 个文件）]   │
│ 昨天                ││   附带知识库 2 段 · 复制 · 导出 Word · 导出 Excel ││ ────────────────── │
│  …                  ││                                              ││ 我方立场：销售 ▾（确认前）│
│                     ││ ┌ 模板：翻译 摘要 起草回复 单证核对 比价 ───┐ ││ 本会话代号 18 个          │
│                     ││ │ [参数行：文件 ▾ 目标语言 ▾]                │ ││ [查看外发文本]           │
│                     ││ │ 输入问题或粘贴来函（Ctrl+Enter 发送）      │ ││ .hint 发给模型的只有脱敏 │
│                     ││ │ □附带公司知识库 □参考我的历史合同   [发送] │ ││ 后的文字，回复在本机还原 │
└────────────────────┘└──────────────────────────────────────────────┘└────────────────────────┘
```

* 会话列表：按今天 / 昨天 / 更早分组；生成中的会话显示圆点；悬停显示“删除”；首次加载用 `v-loading`（骨架屏只在 V2Q-105 两处）。
* 空会话：中间显示五个模板卡片（名称 + 一句用途），点击即选中模板；不加插画。
* 发送：点“发送”先调预览，**总是**弹出“确认发送内容”对话框（与追问相同：显示脱敏后的问题、疑似项逐条选择“替换 / 不替换”，确认后发送）——发送前确认是安全要求，不因会话而省略。
* 回复：生成中每秒轮询一次增量，末尾显示闪烁光标（`prefers-reduced-motion` 时不闪）与“停止”；失败显示原因与“重试”；完成后显示“复制”“导出 Word”“导出 Excel（有表格时）”。回复中出现对照表里没有的代号（`[[…]]` 原样留下的）时，以警告色小标签显示，并在回复下方提示“回复中有无法还原的代号”。
* 比价：`CompareTable` 显示本地计算的表格（最低价加粗、差额百分比 `.num`、币种不同的格标注），表头上方 `.hint`“价格由本地从文件中还原计算，未发送给模型”；下方为条件差异说明。
* 翻译：`TranslationView` 提供“译文 / 对照”切换；顶部固定提示“名称、地址、金额按原文写法还原，请核对”；进度“第 2 / 3 段”。
* 文件与脱敏：文件行复用 `.file-row`、`.file-badge`；状态标签：解析中（不确定进度条 + 已用时间，同 V2-U 解析中规则）、待确认、已确认、解析失败（带“移除”）；“确认脱敏”打开全屏对话框，内嵌 `ConfirmStep mode="assistant"`（不显示范本选项，我方 0 或 1 个即可确认，确认按钮文字为“确认脱敏”，调用会话确认接口）。
* 只读会话（原件已过保留期）：输入区换成一行说明“原件已按保留期限删除，本会话只能查看”。

### 9.3 文字

界面文字照抄本节与第四节的提示；不得出现“已安全”“审核通过”等禁用措辞（test\_forbidden\_words.py 扫描新文件）。

### 9.4 Markdown 的安全渲染（V2Q-126）

`utils/markdown.ts` 只解析：`#`～`###` 标题、段落、`-` / `1.` 列表（一层）、`**粗体**`、管道表格、`---` 分隔线；输出为结构化数组，由 `MessageItem` 用模板渲染（文本一律作为文本节点）。**不使用 v-html、不引入 Markdown 库**；链接、图片、HTML 标签原样作为文字显示。

## 十、前端：知识库设置与外发记录（V2-D-08）

**文件范围**：`frontend/src/views/settings/Knowledge.vue`（新）、`frontend/src/views/settings/SettingsLayout.vue`（导航加“知识库”，业务管理员可见，位于“公司范本”之后）、`frontend/src/router/index.ts`（只加路由 `settings/knowledge`，`adminScope: 'biz'`）、`frontend/src/api/kb.ts`（新）、`frontend/src/views/settings/Data.vue`（用途筛选加“智能助手”）、`frontend/src/utils/labels.ts`。

知识库页：PageHeader“公司知识库”+ 说明“上传公司制度、流程与产品资料。员工在智能助手中勾选后，相关片段经脱敏后随问题发送给模型。”；顶部开关“在智能助手中启用知识库”；表格（名称、状态、片段数、启用、上传人、时间、操作：预览片段 / 停用 / 删除）；上传对话框（多选文件）。数字列 `.num`。

## 十一、集中测试与批次审核（V2-D-09、V2-D-10）

**V2-D-09**（GPT-6.1 Sol，产出 `docs/v2/reports/V2-D-test.md`）：

1. 整仓挂载全量测试（测试库用 aireview-db:dev）；前端 build；converter 测试（未改动可引用最近结果）。
2. 浏览器清单（四种窗口，虚构文件、模拟模型的流式替身服务）：三栏布局与各自滚动；新建、改名、删除会话；加文件 → 解析中 → 确认脱敏 → 发送；五个模板各一次；流式显示、停止、失败重试；比价表与 Excel；翻译对照与 Word；知识库上传、启用、附带；历史条款；只读会话；他人会话 URL 显示“会话不存在或已被删除”；Tab 焦点；控制台无报错。
3. 外发核对：抽取全部 assist 的 llm\_calls.request\_text，用 A-03 的方式断言没有虚构真实值（名称、金额、电话、账号、地址）与文件名。
4. 知识库阈值复核：用 5 份虚构制度、20 个问题（10 个有对应片段、10 个无关）检查 `KB_MIN_SCORE`，无关问题不得带出片段；结论写进报告，需要调整时作为问题提给 Opus。
5. 经用户同意时，用两份虚构文件做一次真实模型试用（翻译、比价各一次），只报告代号是否被保留、还原是否正确。

**V2-D-10**（Opus）：核对测试未被删改与文件范围；逐项审阅第〇节安全不变量；抽查外发记录；浏览器复核关键截图；汇报用户，确认后进入 V2-Z。

## 十二、要改的规则（用户 2026-10-27 确认，CLAUDE.md 已修改）

### 12.1 CLAUDE.md

| 位置 | 原文 | 新文 |
|-|-|-|
| 必须遵守 · 第一条 | 顶部只有“智能审核”“审核历史”两个菜单，右上角“设置”仅管理员可见 | 顶部只有“智能审核”“审核历史”“智能助手”三个菜单（智能助手自 V2-D 起），右上角“设置”仅管理员可见 |
| 必须遵守 · 外发内容 | 发给模型的只有脱敏后的正文文本，以及本地检索得到的公开法条原文和经过脱敏的公司范本条款（V2-E），全部经过出口复检 | 发给模型的只有脱敏后的正文文本与用户消息，以及本地检索得到的公开法条原文、经过脱敏的公司范本条款（V2-E）、经过脱敏的公司知识库片段与本人历史合同条款（V2-D），全部经过出口复检 |
| 必须遵守 · 网关 | 任何模型调用不得绕过网关 | 不变（智能助手的流式发送也在 gateway.py 内） |

### 12.2 SPEC

写在 [SPEC-V2.md](../SPEC-V2.md) D 部分（1.6 菜单、新增“智能助手”章、9.2 中性代号、11 外发内容、16 保留、18 数据模型、19 接口）。

### 12.3 frontend.md

| 位置 | 新增 |
|-|-|
| 2.2 页面容器 | “智能助手页为三栏工作台（264px / 自适应 / 320px），规则同审核结果页：页面不滚动，三栏各自滚动” |
| 4 · 组件用法 | “模型回复按 utils/markdown.ts 的子集渲染，不使用 v-html” |
| 6 · 轮询 | “智能助手回复生成中每 1 秒轮询一次增量，终态停止” |

### 12.4 PLAN 与 TASKS-V2

PLAN 第三节 V2-D 段落不变（本文件即其细化）。TASKS-V2 的 V2-D 表按第十三节写入。

## 十三、任务拆分

| 编号 | 任务 | 模型 | 强度 | 依赖 |
|-|-|-|-|-|
| V2-D-01 | 会话数据与接口：kind、assistant\_messages、迁移 0023、会话 CRUD、分批加文件与只解析新文件、移除未确认文件、审核接口隔离、保留条件占位（第二节） | GPT-6.1 Sol（测试先行） | high | V2-D-00 确认 |
| V2-D-02 | 中性代号与稳定编号（第三节；跑脱敏回归集与 A-03） | GPT-6.1 Sol（测试先行） | xhigh | V2-D-01 |
| V2-D-03 | 对话：确认、预览与发送、外发组装、gateway 流式、worker assist 组、增量还原、停止与失败（第四节） | GPT-6.1 Sol（测试先行） | xhigh | V2-D-02 |
| V2-D-04 | 五个模板：翻译分段与按语言还原、比价本地计算等（第五节） | GPT-6.1 Sol | high | V2-D-03 |
| V2-D-05 | 知识库与参考历史合同（第六节，迁移 0024） | GPT-6.1 Sol（测试先行） | xhigh | V2-D-04 |
| V2-D-06 | 导出、保留期、外发记录、审计（第七节） | GPT-6.1 Sol | high | V2-D-05 |
| V2-D-07 | 前端：菜单、三栏页面、模板、确认脱敏、流式显示、导出（第九节） | GPT-6.1 Sol | high | V2-D-06 |
| V2-D-08 | 前端：知识库设置页、外发记录用途（第十节） | GPT-6.1 Sol | medium | V2-D-07 |
| V2-D-09 | 集中测试：整仓全量、浏览器清单、外发核对、知识库阈值复核、（经同意）真实模型试用，写批次测试报告（第十一节） | GPT-6.1 Sol | medium | V2-D-08 |
| V2-D-10 | 批次审核，汇报用户 | Opus | high | V2-D-09 |

## 十四、风险

| 风险 | 应对 |
|-|-|
| 模型改写代号导致还原失败 | 中性 ASCII 代号 + 系统规则 1～3；无法还原的代号醒目标出；D-09 真实模型试用专门检查 |
| 改脱敏引擎影响审核 | D-02 单独任务、测试先行；默认参数下逐字不变由回归集与 A-03 保证 |
| 分批加文件导致代号漂移、历史消息还原错误 | pinned 稳定编号；已出现过的代号永不删除（T-48 第 5～7 条） |
| 长文件翻译耗尽调用次数 | 发送前按段数检查剩余次数并明确提示 |
| 比价被模型“编数字” | 只认对照表中的单价 / 金额代号，字面数字一律不采用；计算全部在本地 |
| 知识库、历史条款带出未识别的敏感信息 | 片段脱敏后有疑似项整段丢弃；逐段出口复检；来源任务真实值列入复检 |
| 流式生成占用 worker | 独立 assist 组；每会话 1 条、每人 2 条；总时长 600 秒 |
| 会话量大增加存储与模型费用 | 计入现有保留期与调用上限；存储用量页单列会话数 |
| 公司屏蔽公共 AI 后使用量上升 | 个人每日上限与月度上限照常生效；外发记录可按用途查看 |

## 十五、新增待确认问题

见 [../questions/V2-D.md](../questions/V2-D.md)：V2Q-115～V2Q-128。
