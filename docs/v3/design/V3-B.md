# V3-B 多模型

2026-10-30 · Opus 5.5 细化（V3-00）· **用户已确认（2026-10-30，含 V3Q-01 三项）**

范围见 [PLAN.md](../PLAN.md)；执行规则见 [TASKS-V3.md](../TASKS-V3.md) 第一节。依据为 V3.0 初稿第五节，本文件把它落到表、接口与任务卡。

## 〇、结论与共同约定

1. **外发不变**：外发内容、出口复检、`local_only`、提示词与模型无关；每个模型都只经 `backend/app/llm/gateway.py` 调用，顺序仍是“调用上限检查 → 出口复检 → 发送 → 记录 llm\_calls”。
2. **按建议采用 V3Q-01**：普通用户可以在审核与助手中选模型（只列出管理员允许的）；审核默认、助手默认分开设置；最多 10 个模型（含停用）。
3. **“可用于智能审核”⇒ 已通过结构化输出测试**：这是唯一的硬约束，由保存接口保证（2.3），不另设状态。
4. **已用过的模型不能删除**，只能停用；默认模型不能停用。
5. **普通用户只看到显示名称和说明**，看不到接口地址、密钥、单价、上限。
6. **测试**：按 CLAUDE.md，只写第六节列出的代表性用例；V3-B-01 测试先行。

## 一、数据

### 1.1 新表 `llm_models`

| 字段 | 类型 | 说明 |
|-|-|-|
| id | serial | 主键 |
| display\_name | varchar(40) | 非空，唯一；界面显示 |
| description | varchar(200) | 可空；给用户看的用途说明（如“长文件、速度快”） |
| base\_url | varchar(500) | 非空 |
| api\_key\_enc | bytea | 非空，主密钥加密 |
| model\_name | varchar(100) | 非空 |
| temperature | numeric(3,2) | 非空，默认 0.1 |
| max\_tokens | int | 非空，默认 32000 |
| timeout\_s | int | 非空，默认 180 |
| json\_mode | boolean | 非空，默认 true |
| extra\_params | jsonb | 非空，默认 {} |
| chunk\_chars | int | 非空，默认 60000 |
| price\_in\_per\_m / price\_out\_per\_m | numeric(12,4) | 非空，默认 0 |
| monthly\_call\_cap | int | 非空，默认 0（不限） |
| enabled | boolean | 非空，默认 true |
| for\_review / for\_assist | boolean | 非空 |
| structured\_ok | boolean | 非空，默认 false；最近一次结构化输出测试是否通过 |
| structured\_tested\_at | timestamptz | 可空 |
| sort\_order | int | 非空，默认 0 |
| created\_at / updated\_at | timestamptz | 非空 |

`settings` 中保留 `model.user_daily_cap`（跨模型合计），新增 `model.default_review_id`、`model.default_assist_id`；其余 `model.*` 键由迁移移入本表后删除。

### 1.2 其他表增加字段

| 表 | 字段 | 说明 |
|-|-|-|
| llm\_calls | model\_id int，可空，不设外键 | 测试未保存的新模型时为空 |
| review\_runs | model\_id int，可空，外键 llm\_models；model\_label varchar(40)，可空 | 本轮所选模型与当时的显示名称；`model_name` 照旧记实际模型名称 |
| assistant\_messages | model\_id int，可空，外键 llm\_models；model\_label varchar(40)，可空 | 只在助手回复行写入 |

### 1.3 迁移

1. 建表、加字段。
2. 现有 `model.base_url`、`model.name`、`model.api_key_enc` 都非空时，生成一行：显示名称取 `model.name`（截到 40 字），其余字段照抄；`enabled`、`for_review`、`for_assist`、`structured_ok` 均为 true（已在生产使用）；两个默认指向它；已有 `llm_calls`、`review_runs` 的 `model_id` 全部回填为它的 id。未配置时不生成，两个默认为空。
3. 删除已迁移的 `model.*` 键（保留 `model.user_daily_cap`）。
4. 密钥：设置表中是 base64 文本，迁入 bytea 时解码原密文即可，不解密、不重新加密。
5. `security/rotate.py`：确认 `llm_models.api_key_enc` 被主密钥更换覆盖；删除对 `settings` 中 `model.api_key_enc` 的专门处理。

## 二、后端

### 2.1 网关（`llm/gateway.py`、`llm/usage.py`）

* `_load_config(db)` 改为 `load_model(db, model_id) -> _Config`：模型不存在或已停用时抛 `GatewayError("MODEL_UNAVAILABLE")`，文字“所选模型已停用，请换一个模型”。`_Config` 增加 `id`、`chunk_chars`、两个单价。
* `call_model(...)`、`stream_model(...)` 增加必填参数 `model_id: int`；`llm_calls.model_id` 随记录写入。
* 上限：每月上限按 `llm_calls.model_id` 分别统计（`monthly_call_cap` 为该模型的上限）；个人每日上限仍跨模型合计。`cap_reached(db, user_id, purpose, model_id)`、`remaining_calls(db, user_id, model_id)` 相应改动。
* `monthly_usage(db, model_id=None)`：不传时为全部模型合计，按各自单价分别计算费用后相加（Decimal，ROUND\_HALF\_UP）。
* 测试连接：`test_config(db, cfg, user_id, *, structured: bool) -> ModelResult`，用传入的配置（可以是尚未保存的），`purpose="test"`，不含业务数据，不受调用上限限制。结构化测试的消息固定为：要求只输出 `{"ok": true, "items": [{"n": 1}, {"n": 2}]}`，按该模型的 `json_mode` 发送；返回内容去掉代码围栏后能解析为该结构即通过。

### 2.2 业务调用

| 场景 | 用哪个模型 |
|-|-|
| 新建审核、重新审核 | 请求中的 `model_id`；不传时用审核默认。必须启用且 `for_review`，否则 422 `MODEL_NOT_ALLOWED`。写入 `review_runs.model_id`、`model_label` |
| 审核执行（job） | `run.model_id`；分段长度用该模型的 `chunk_chars`。执行时模型已停用：本轮失败，原因“所选模型已停用，请重新审核并换一个模型” |
| 追问 | 该轮次的模型；该模型已停用或不再 `for_review` 时改用审核默认模型。回复照常记录实际模型 |
| 智能助手 | 发送请求中的 `model_id`；不传时用本会话最近一条助手回复的模型，再没有则用助手默认。必须启用且 `for_assist`；模板为“比价”时还必须 `structured_ok`，否则 422 `MODEL_NOT_ALLOWED`。写入助手回复行的 `model_id`、`model_label`；“重试”沿用原回复的模型 |
| 测试连接 | 见 2.1 |

### 2.3 模型管理接口（系统管理员，`admin/model.py` 重写）

| 接口 | 说明 |
|-|-|
| `GET /admin/models` | 全部模型：除密钥外的全部字段、密钥末 4 位、本月调用次数与估算费用、是否审核默认 / 助手默认、是否已被调用过（决定能否删除）；另返回 `user_daily_cap` 与全部模型的本月合计 |
| `POST /admin/models` | 新增。总数已有 10 个时 409 `MODEL_LIMIT`。保存前自动做连接测试与结构化测试，结果写入 `structured_ok`、`structured_tested_at` 并随响应返回；`for_review=true` 而结构化测试未通过时 422 `STRUCTURED_REQUIRED`，不保存 |
| `PUT /admin/models/{id}` | 修改。密钥留空表示不改。改了连接相关字段（地址、密钥、模型名称、JSON 输出、附加参数）时同 POST 自动测试与校验；只改显示名称、说明、单价、上限、用途、排序时不测试（勾选 `for_review` 仍要求 `structured_ok`）。取消 `for_review` / `for_assist` 时，若它是对应默认则 409 `DEFAULT_IN_USE` |
| `POST /admin/models/{id}/test` | 连接测试 + 结构化测试，更新 `structured_ok`；返回两项结果（错误详情只给管理员，已去密钥，沿用现有 `detail`）。结构化测试未通过且该模型 `for_review` 时，响应提示“该模型不能继续用于智能审核，请修正后重新测试”，但不自动改动用途 |
| `POST /admin/models/{id}/default` | `{purpose: "review" \| "assist"}`；模型须启用且有对应用途 |
| `POST /admin/models/{id}/enabled` | `{enabled: bool}`；默认模型不能停用（409 `DEFAULT_IN_USE`） |
| `DELETE /admin/models/{id}` | 只在 `llm_calls` 中从未出现该 id 时允许，否则 409 `MODEL_IN_USE`（“已被调用过的模型只能停用”） |
| `PUT /admin/settings/user-daily-cap` | `{value: int ≥ 0}` |

原 `GET/PUT /admin/settings/model`、`POST /admin/settings/model/test` 删除；`GET /admin/usage` 返回合计与按模型的明细。每次修改写审计日志（不含密钥）。

### 2.4 普通用户接口

`GET /models?purpose=review|assist`：启用且有对应用途的模型，按 `sort_order`、`id` 排列，每项只含 `id`、`display_name`、`description`、`is_default`、`structured_ok`。

## 三、前端

### 3.1 设置 → 模型（`views/settings/Model.vue` 重写）

* 顶部 `SettingSection`“全部模型”：个人每日调用上限（输入 + 保存）、本月合计（调用次数、估算费用）。
* 下方卡片网格（`grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))`，间距 16px）。每张卡片：显示名称（15px / 600）、`模型名称 · 接口主机`（次要色，主机只取 URL 的 host）、标签（审核默认、助手默认、仅审核、仅助手、已停用、结构化未通过）、本月“调用 N 次 · 约 ¥X / 上限 M 次”。卡片底部：编辑、测试连接；“⋯”菜单：设为审核默认、设为助手默认、停用 / 启用、删除（未调用过才显示）。停用的卡片降低不透明度。
* 末尾“新增模型”虚线卡片（总数满 10 个时不可点并写明原因）。
* 编辑与新增用右侧抽屉（560px）：显示名称、说明、可用于智能审核、可用于智能助手、接口地址、API 密钥（只显示末 4 位，留空不改）、模型名称、temperature、最大输出、超时、JSON 输出、附加参数、分段长度、单价、每月上限、排序。保存时按钮显示“正在测试并保存…”；返回后在抽屉顶部显示两项测试结果；422 时显示原因并保留表单。
* 抽屉顶部固定提示：“每增加一个模型就是一个新的外发目的地，接口地址须在公司出口放行。”

### 3.2 选择模型

* **新建审核第一步**（`ReviewNew.vue`）：在立场、来源之后加“审核模型”下拉（显示名称，选项下方次要色说明），默认选审核默认。只有 1 个可用模型时显示为只读文字。
* **重新审核**（`ReRunDialog.vue`）：同样的下拉，默认为上一轮的模型（不可用时为审核默认），下方一句“换一个模型重新审核，可以对照两轮结果”。
* **审核结果**：工具条元信息中的模型显示为“显示名称（模型名称）”；报告的“模型”同此（SPEC 14 第 1 项）。
* **智能助手**（`Composer.vue`）：输入框下方一行加模型下拉（紧凑尺寸），默认按 2.2 规则；选“比价”模板时只列出 `structured_ok` 的模型，当前模型不符合时自动切到助手默认并提示一次。每条助手回复下方次要色小字显示 `model_label`。
* 外发记录列表的“模型”列不变（实际模型名称）。

## 四、规格变更（V3-B-06 合并）

* **1.7**：从“当前版本不做”删除“多模型切换界面”。
* **6.2 第一步**：增加“审核模型”；**6.3**：元信息显示所选模型；**7.4**：重新审核可换模型。
* **11.1**：按本轮所选模型调用，分段长度取该模型设置；所选模型执行时已停用则本轮失败。**13**：追问用该轮模型，不可用时改用审核默认。**13A 第 2 条**：可选模型，比价只能用通过结构化测试的模型。
* **14 第 1 项**：模型写“显示名称（模型名称）”。
* **15.2**：改写为第三节的卡片式管理、默认模型、结构化测试、每模型上限与个人每日合计上限、最多 10 个、已调用过只能停用。
* **17.4**：每个模型的接口地址都是外发目的地；任何模型调用都经网关。
* **18**：新增 18.x `llm_models`；18.14 `review_runs`、18.18 `llm_calls`、`assistant_messages` 增加字段（1.2）；18.20 settings 中模型键的变化。
* **19.7**：模型管理接口按 2.3 改写；新增 `GET /models`。
* **23.2**：增加 T-62（按所选模型调用并分别计上限）、T-63（普通用户看不到模型连接信息）、T-64（未通过结构化测试不能用于审核）。

## 五、任务拆分

| 编号 | 任务 | 模型 | 强度 | 依赖 |
|-|-|-|-|-|
| V3-B-01 | 表与迁移、网关按模型调用、上限与用量、主密钥更换覆盖（第一节、2.1） | GPT-6.1 Sol（测试先行） | xhigh | V3-A-09 |
| V3-B-02 | 模型管理与列表接口、审核 / 追问 / 助手按模型（2.2～2.4） | GPT-6.1 Sol | high | V3-B-01 |
| V3-B-03 | 前端：模型卡片页（3.1） | GPT-6.1 Sol | medium | V3-B-02 |
| V3-B-04 | 前端：审核、重新审核、助手的模型选择与显示，报告中的模型（3.2） | GPT-6.1 Sol | medium | V3-B-03 |
| V3-B-05 | 集中测试：整仓全量一次；浏览器检查卡片页（新增、编辑、测试、默认、停用、删除限制）、三处选择、普通用户看不到连接信息；用模拟服务起两个“模型”各跑一次审核与助手，核对 llm\_calls 的 model\_id 与上限；写 `docs/v3/reports/V3-B-test.md` | GPT-6.1 Sol | medium | V3-B-04 |
| V3-B-06 | 审核与合并规格（第四节） | Opus | high | V3-B-05 |

**文件范围**

* V3-B-01：`backend/app/db/models.py`、`backend/app/db/alembic/versions/`（一个新迁移）、`backend/app/llm/gateway.py`、`backend/app/llm/usage.py`、`backend/app/admin/settings_store.py`（删除迁出的默认值）、`backend/app/security/rotate.py`、`backend/app/cli.py`（set-model 适配，V3Q-10）、第六节 1～4 的测试文件及 `backend/tests/test_gateway.py` 中两项 set-model 测试。
* V3-B-02：`backend/app/admin/model.py`、`backend/app/models_api.py`（新，`GET /models`）、`backend/app/main.py`、`backend/app/review/`（router、service、job、validate 中取模型与写入轮次处）、`backend/app/chat/service.py`、`backend/app/assistant/`（schema、service、job、prompt 中取模型处）、`backend/app/export/report_docx.py`（模型一行）、第六节 5～8 的测试文件。
* V3-B-03：`frontend/src/views/settings/Model.vue`、`frontend/src/api/admin.ts`（模型部分）、`frontend/src/components/ModelEditDrawer.vue`（新）。
* V3-B-04：`frontend/src/api/models.ts`（新）、`frontend/src/views/ReviewNew.vue`、`frontend/src/components/ReRunDialog.vue`、`frontend/src/components/ResultToolbar.vue`、`frontend/src/components/assistant/Composer.vue`、`MessageItem.vue`、`frontend/src/api/tasks.ts`、`runs.ts`、`assistant.ts`（请求加 `model_id`）。

## 六、测试用例（代表性，照写）

1. 迁移：有旧配置时生成 1 个模型、两个默认指向它、旧键删除、`model.user_daily_cap` 保留、旧 `llm_calls` 回填 `model_id`；无旧配置时不生成。
2. 网关按所选模型的地址、密钥、模型名称发送（模拟传输层断言请求），`llm_calls.model_id` 正确；出口复检命中时照常拦截。
3. 模型 A 达到每月上限后 A 返回 `CAP_REACHED`，B 仍可调用；个人每日上限跨 A、B 合计。
4. 主密钥更换后 `llm_models` 的密钥可用新密钥解密（在现有 rotate 测试中加断言）。
5. 普通用户 `GET /models` 不含 `base_url`、密钥、单价；访问 `/admin/models` 返回 403。
6. 新增或修改为 `for_review=true` 而结构化测试失败：422 `STRUCTURED_REQUIRED`，数据库无变化。
7. 删除被调用过的模型 409；停用默认模型 409。
8. 审核选择已停用的模型 422；排队中的轮次在执行前模型被停用 → 本轮失败并给出原因；追问在该轮模型停用后改用审核默认模型。
