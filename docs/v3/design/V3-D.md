# V3-D 修订版导出

2026-10-31 · Opus 5.5 细化（V3-D-00）· 依据：V3-C-06 的建议（用户 2026-10-31 按建议确认，V3Q-23）· **待用户确认**

范围见 [PLAN.md](../PLAN.md)；执行规则见 [TASKS-V3.md](../TASKS-V3.md) 第一节。复用 V3-C 的定位层（`export/docx_anchor.py`）与批注生成（`export/annotated_docx.py`），读本文件前先读 [V3-C.md](V3-C.md) 第〇、二、三节。

## 〇、结论与共同约定

1. **原件一字不改**：修订版是新文件，只在服务器内存生成，不保存；生成后校验“全部拒绝 = 原件、全部接受 = 预期”，**校验不通过不输出**。
2. **模型不接触文件**：修订文字全部来自已入库问题的 `suggested_text`，在本机还原为真实名称后写入。
3. **范围**：与批注版相同的文件（.docx，及有转换结果的 DOC、WPS、RTF），另加一条：**原件含修订记录（`task_files.has_revisions`）的不提供**（409），避免把我们的修订与原有修订混在一起。
4. **哪些问题**：所选轮次中处理状态为“已采纳”（`adopted`）且 `suggested_text` 非空的模型问题。待处理、已忽略的问题和本地校验结果不进修订版。
5. **写成修订的只有有把握的**：
   * **替换型**（`missing=false`）：还原后的 quote 用 V3-C 的规则精确定位成功 → quote 所在 run 标为删除，紧接着插入还原后的 `suggested_text`。
   * **缺失型**（`missing=true`）：第一个 loc 是正文节点 → 在该节点最后一个段落之后插入新段落（整段为插入）。
   * 其余已采纳问题**不写修订**，按 V3-C 的批注格式挂在同一份修订版中，首行加“【未能写成修订，请人工修改】”（第三节）。
6. 修订、批注作者都是“商衡SI”；修订时间为生成时刻（UTC）。不改 `settings.xml`（不打开“修订跟踪”开关）。
7. **测试**：生成层测试先行，用虚构 .docx；按 CLAUDE.md 不写穷举。

## 一、修订生成（V3-D-01，GPT-6.1 Sol，xhigh，测试先行）

### 1.1 定位层的小改动（`export/docx_anchor.py`）

* `Anchors.locate(loc, quote, skip_inserted: bool = False)`：为真时，计算“可见文字出现次数”时跳过 `w:ins` 内的文字（我们刚写入的插入文字不应让后面的 quote 变成“多处出现”）。默认 False，V3-C 行为不变。
* 其余不改。已写入的 `w:del` / `w:ins` 不是直接子 run，后续 quote 跨过它们时自然找不到，按第 5 条转批注。

### 1.2 生成（新模块 `export/revised_docx.py`）

前置检查与读原件、`load_anchors`、还原映射全部同 `annotated_docx.build_annotated`；为此允许把 `annotated_docx.py` 中的条件检查、读原件、批注文字拼装、`_save` 抽成共用函数（`_save` 增加校验函数参数），**行为不变，`test_annotated_export.py` 原样通过**。

按问题序号依次处理每条已采纳问题，先写**全部修订**，再加**全部批注**（避免批注范围标记打断后面的修订）：

1. **还原并检查建议条文**：`text = restore(suggested_text, mapping)`。以下情况不写修订、转批注：
   * 还原后仍含代号形式的文字（方括号代号 `[…]`、`［…］`、`【…：…】`，或 `placeholders.py` 中按对象编号的各类格式，如“客户_C”“人员_3”；“我方”不算）——模型编造了对照表里没有的代号；
   * 替换型的 text 与还原后的 quote 相同，或 text 含换行；
2. **替换型**：`anchor = locate(locs[0], restore(quote_model), skip_inserted=True)`；不精确 → 转批注。精确时 `split_runs`，覆盖范围内除 `w:r` 外还有其他元素（书签等）→ 转批注。否则：
   * 用一个 `w:del`（`w:id`、`w:author="商衡SI"`、`w:date`）包住覆盖的 run，其中 `w:t` 改为 `w:delText`（保留 `xml:space`）；
   * 紧接其后插入一个 `w:ins`，内含一个 run：`w:rPr` 复制覆盖范围第一个 run 的 `w:rPr`，`w:t` 为 text（`xml:space="preserve"`）。
3. **缺失型**：`locs[0]` 不是正文节点或不存在 → 转批注。否则在该节点最后一个 `w:p` 之后，按 text 的每个非空行各插入一个新段落：
   * `w:pPr` 深复制自该段（含样式与编号，接受后编号自然顺延），并在 `w:pPr/w:rPr` 中加 `w:ins` 标记（段落标记为插入）；
   * 段内一个 `w:ins` 包住一个 run，`w:rPr` 复制自该段第一个只含 `w:rPr` 与 `w:t` 的 run（没有则不加）。
   * 同一节点有多条缺失型时，按序号依次排在前一条插入段之后。
4. `w:id`：从文档中现有最大的修订 / 批注 `w:id` 之后递增，保证唯一。
5. **批注**（转批注的问题）：文字同 V3-C 3.1 模型问题的格式，首行前加“【未能写成修订，请人工修改】”；替换型挂在 locate 返回段落的段首（不精确时）或 quote 上（精确但因第 1 条转批注时），缺失型挂在 loc 节点第一段段首，loc 无效时挂正文第一段开头。
6. 写入修订数为 0 时 409“已采纳的问题都无法写成修订，请改用批注版”。

### 1.3 校验（`revised_docx.validate`，替代 V3-C 的正文文字比较；部件规则不变）

生成后重新打开结果，对 `word/document.xml`：

* **全部拒绝视图**：去掉所有 `w:ins` 内的内容和段落标记为插入的整段，`w:delText` 当作 `w:t`；按 V3-C 3.1 的规则拼接 `w:t`、`w:delText`、`w:instrText` → 必须与原件相同。
* **全部接受视图**：去掉所有 `w:del` 内的内容；按正文段落取文字列表 → 必须等于预期列表。预期列表由原件段落文字列表得出：每处替换把对应段落中 quote 的那一次出现换为 text；插入的段落放到其锚定段落之后。
* 其余部件：`document.xml`、`comments.xml`、`[Content_Types].xml`、`document.xml.rels` 以外逐字节相同（同 V3-C）。
* 不通过：500“生成修订版时校验未通过，未输出文件”，日志只记任务编号与原因代码。

### 1.4 接口（`export/router.py`）

`GET /runs/{rid}/revised?file_seq=N`

* owner 校验与 409 条件同批注版（文字相同），另加：原件含修订记录 → 409“原件含修订记录，请在 Word 中接受或拒绝全部修订后重新上传”；本轮没有已采纳且有建议条文的问题 → 409“本轮没有已采纳且有建议条文的问题”；第 1.2 节第 6 条。
* 文件名：`{原文件名去扩展名}_商衡SI修订_{yyyyMMdd}.docx`（Asia/Shanghai）；响应头 `X-SI-Revised`（写成修订的问题数）、`X-SI-Commented`（转批注数），只有数字。
* 审计 `export_revised`（轮次、文件序号、修订数、转批注数），不含文件名与内容。

### 1.5 测试（`backend/tests/test_revised_export.py`，虚构样本 `backend/tests/fixtures/annotate/revise.docx`，用现有 `make_fixture.py` 增加生成）

1. 一份虚构任务：已采纳 4 条（替换型可精确定位、缺失型、quote 找不到、建议条文含对照表没有的代号）、待处理 1 条、已忽略 1 条：结果有 2 条修订、2 条批注；修订作者“商衡SI”，插入文字为真实名称；拒绝视图与原件相同、接受视图与预期相同；`X-SI-Revised`=2、`X-SI-Commented`=2。
2. 同一段内两条已采纳问题的 quote 重叠 → 第二条转批注。
3. 他人轮次 404；原件含修订记录 409；没有已采纳问题 409。
4. 生成结果被改动一处正文 → `validate` 报错（同 V3-C 的校验失败用例写法）。

**文件范围**：`backend/app/export/revised_docx.py`（新）、`backend/app/export/annotated_docx.py`（只抽共用函数）、`backend/app/export/docx_anchor.py`（只加 `skip_inserted`）、`backend/app/export/router.py`、`backend/tests/test_revised_export.py`（新）、`backend/tests/fixtures/annotate/make_fixture.py` 与 `revise.docx`（新）。

## 二、导出入口（V3-D-02，GPT-6.1 Sol，medium）

* “导出”下拉在每个文件的“批注版：{文件名}”之后增加“修订版：{文件名}”，只列 `annotatable` 为真且 `has_revisions` 为假的文件（不改后端字段）。底部说明改为“批注版、修订版支持 Word 与 WPS 文字文件，修订版只写已采纳的问题；PDF、图片、表格只能导出审核报告”。
* 点击先确认：“修订版按已采纳的问题改写条款，请在 Word 中逐条核对后接受或拒绝；文件中如有批注，发给对方前请先删除。”（按钮“导出修订版”“取消”），之后用 `saveBlob` 保存。
* 完成提示：“已生成修订版，共 N 处修订”；`X-SI-Commented` > 0 时改为持续显示、可关闭的 `el-alert`：“共 N 处修订；另有 M 条已采纳的问题未能写成修订，已作为批注附上，请人工修改。”接口 409 时显示后端文字。

**文件范围**：`frontend/src/components/ResultToolbar.vue`、`frontend/src/api/report.ts`、`frontend/src/views/ReviewResult.vue`。

## 三、集中测试（V3-D-03，GPT-6.1 Sol，high）

1. 整仓全量一次；前端 build。
2. 浏览器：导出菜单、确认弹窗、保存与取消、转批注提醒、409 提示。
3. 用虚构样本在本机候选环境（模拟模型）导出一份修订版，交用户用 Word 打开核对：修订位置与文字、“全部接受”后的文字、“全部拒绝”后与原件一致、编号顺延正常。
4. 经用户同意，用 V3-C-05 的样本 1 在本机候选环境中由用户把部分问题标为“已采纳”后导出一次，报告只写数字：已采纳数、写成修订数（替换 / 插入）、转批注数及原因分布。
5. 写 `docs/v3/reports/V3-D-test.md`（写短），停下交 Opus。

## 四、审核与合并（V3-D-04，Opus，high）

审阅 D-01～D-03；合并第五节；V3-D 完成后进入 V3-Z-00。

## 五、规格变更（V3-D-04 合并）

* **1.7、24.1**：删除“带修订的 Word 原文导出（V3-D）”。
* **14**：新增 14.2“修订版”：范围（第〇节第 3、4 条）、写成修订的规则与转批注、文件名、校验、不保存、审计。
* **19.5**：增加 `GET /runs/{rid}/revised`。
* **17 审计清单**：增加修订版导出（export\_revised）。
* **23.2**：增加 T-67（修订版全部拒绝等于原件、全部接受等于预期，插入文字为真实名称，只写已采纳问题）、T-68（他人轮次 404、原件含修订 409、无法写成修订的转批注且计数正确）。

## 六、任务拆分

| 编号 | 任务 | 模型 | 强度 | 依赖 |
|-|-|-|-|-|
| V3-D-01 | 修订生成、校验与接口（第一节） | GPT-6.1 Sol（测试先行） | xhigh | V3-D-00 确认 |
| V3-D-02 | 导出入口（第二节） | GPT-6.1 Sol | medium | V3-D-01 |
| V3-D-03 | 集中测试（第三节） | GPT-6.1 Sol | high | V3-D-02 |
| V3-D-04 | 审核与合并规格（第四、五节） | Opus | high | V3-D-03 |
