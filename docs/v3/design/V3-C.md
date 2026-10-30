# V3-C 批注版导出

2026-10-30 · Opus 5.5 细化（V3-00）· **用户已确认（2026-10-30，含 V3Q-04）**

范围见 [PLAN.md](../PLAN.md)；执行规则见 [TASKS-V3.md](../TASKS-V3.md) 第一节。依据为 V3.0 初稿第四节（D7、D8）。V3-D 修订版复用本批的定位层，等本批使用后再细化。

## 〇、结论与共同约定

1. **原件一字不改**：批注版是新文件，正文与原件逐字一致；生成后在服务器内存中校验，**校验不通过不输出**；服务器不保存批注版。
2. **模型不接触文件**：批注内容全部来自已入库的问题清单，在本机还原为真实名称后写入。
3. **范围**：原件为 .docx 的文件，以及旧格式 DOC、WPS、RTF（用解析时 converter 转换得到的 .docx 生成，见 3.0；输出为 .docx）。PDF、扫描件、Excel、XLS / ET 只出审核报告。原件已按保留期删除的不提供。
4. **哪些问题**（V3Q-04）：所选轮次中处理状态不是“已忽略”的模型问题，加上本任务的本地校验结果（有位置的挂在对应位置，没有位置的挂在正文第一段开头）；先模型问题后本地校验，各按序号排列。
5. **定位只做有把握的**：能在原文中找到唯一、完整对应片段的，批注挂在该片段上；其余挂在所在段落开头并注明“未能精确定位”。不做模糊匹配。
6. **测试**：定位层测试先行，用虚构 .docx 样本；按 CLAUDE.md 不写穷举。

## 一、提示词约束（V3-C-01，GPT-6.1 Sol，medium）

为修订版（V3-D）准备数据，同时提高批注的精确定位率。`review/prompt.py` 中输出结构的说明改为：

* `quote`：“从 locs 第一个位置的原文中**一字不差**地摘录的连续片段，不超过 100 字，不跨段落，不加省略号；缺失型问题为 null”。
* `suggested_text`：“对 quote 这一片段的**完整替换文字**（直接替换后语句通顺）；缺失型问题写建议新增的完整条款；无法给出时为 null”。

升级提示词版本号；结果页、报告中 `suggested_text` 的标签文字不变。`cite_status` 的判断不变。更新对应的提示词快照测试。

**文件范围**：`backend/app/review/prompt.py`、`backend/tests/test_review_prompt*.py`（只改快照或断言）。

## 二、原件定位层（V3-C-02，GPT-6.1 Sol，xhigh，测试先行）

### 2.1 段落追踪

* `parsing/docx.py` 增加可选参数 `track: bool = False`（默认关闭，解析行为与 `parser_version` 不变）：开启时记录每个正文节点（`F{n}-P{k}` 与表格单元格 `F{n}-T…-R…-C…`）由哪些 `w:p` 元素组成（被并入的“段落标记已删除”段落一并记入）。页眉、页脚、脚注、批注、文本框节点只记录“非正文”。
* 新模块 `export/docx_anchor.py`：
  * `load_anchors(data: bytes, file_seq: int, stored: dict[str, str]) -> Anchors`：以 `track=True` 重新解析原件；把每个节点的文字与库中 `doc_nodes` 解密后的文字逐个比较，**任何一个不一致即抛 `AnchorMismatch`**（导出接口返回 409“原件解析结果与任务记录不一致，无法生成批注版”）。
  * `Anchors.locate(loc, quote) -> Anchor`：
    1. `loc` 不是正文节点或不存在 → 正文第一段开头，`precise=False`。
    2. `quote` 为空 → 该节点第一个 `w:p` 开头，`precise=False`。
    3. 在该节点的每个 `w:p` 中，把**直接子元素** `w:r` 里的 `w:t` 文字依次拼接（含 `w:tab`、`w:br` 的 run 视为边界，不参与匹配）；`quote` 在其中**恰好出现一次且只在一个 `w:p` 中**时，返回该段起止 run 与偏移，`precise=True`。
    4. 其他情况（找不到、多处出现、落在超链接 / 域 / 修订 / 内容控件里的 run）→ 该节点第一个 `w:p` 开头，`precise=False`。
  * `split_runs(anchor) -> list[run]`：在起止偏移处拆分 run（复制 `w:rPr`，只拆只含 `w:rPr` 与单个 `w:t` 的 run；遇到其他结构按第 4 条回退），返回覆盖 quote 的 run 列表。拆分不改变文字。

### 2.2 测试（`backend/tests/test_docx_anchor.py`，虚构样本放 `backend/tests/fixtures/annotate/`）

1. 普通段落：quote 跨两个格式不同的 run → 精确定位，拆分后段落文字不变、quote 正好被覆盖。
2. 表格单元格（单元格内两段）中的 quote → 精确定位到正确的段。
3. quote 在同一节点中出现两次 → 回退到段首。
4. quote 落在超链接内 → 回退到段首。
5. 带自动编号的条款（编号由样式生成）：quote 取自正文部分 → 精确定位。
6. loc 为页眉节点 → 正文第一段开头。
7. 库中节点文字被改动一个字 → `AnchorMismatch`。
8. 对 V2 已有的 .docx 测试样本（`backend/tests/fixtures/` 下全部 .docx）`track=True` 与默认解析的节点完全相同。

**文件范围**：`backend/app/parsing/docx.py`（只加 `track` 记录，不改已有输出）、`backend/app/export/docx_anchor.py`（新）、`backend/tests/test_docx_anchor.py`（新）、`backend/tests/fixtures/annotate/`（新，虚构样本）。

## 三、批注版生成与接口（V3-C-03，GPT-6.1 Sol，high）

### 3.0 旧格式的转换结果

* 解析旧格式 DOC、WPS、RTF 时（`tasks/pipeline.py` 调用 `legacy.convert` 之后），把转换得到的 .docx 用主密钥加密，存入原件目录（文件名为新的 32 位十六进制，与原件同一规则，因此备份、恢复、主密钥更换自动覆盖）；`task_files` 增加 `converted_storage_key varchar(64)` 可空（Alembic 迁移）。
* 与原件同生命周期：原件按保留期删除、任务删除时一并删除（`backup/retention.py` 与任务删除处）。
* V3 之前上传的旧格式文件没有转换结果，不提供批注版（409“该文件在升级前上传，没有保存转换结果，请重新上传后审核”）。
* XLS、ET 转换结果不保存（批注版不支持表格文件）。

### 3.1 生成（`export/annotated_docx.py`）

* 读原件（.docx 用原件；旧格式用 3.0 的转换结果；解密，只在内存）→ `load_anchors` → 对每条问题 `locate(locs[0], 还原后的 quote)` → `split_runs` → `python-docx` 1.2.0 的 `Document.add_comment(runs, text, author="商衡SI", initials="SI")`；回退的问题在段首第一个 run 上加批注（段落没有 run 时插入一个空 run）。
* 批注文字（全部用 `redaction/restore.py` 还原为真实名称）：

  ```
  【高 · 法律】问题标题
  问题：……
  建议：……
  建议条文：……（有 suggested_text 时）
  依据：……（有时）
  —— 商衡SI 第 N 轮 · 问题 {序号}
  ```

  回退的问题在第一行前加“【未能精确定位，原位置：{位置显示}】”。本地校验结果的批注为“【本地校验 · {等级}】{校验项名称}”＋ 还原后的说明，末行“—— 商衡SI 本地校验”。
* **逐字校验**：生成后重新打开结果，`word/document.xml` 中按文档顺序拼接的全部 `w:t`、`w:delText`、`w:instrText` 文字与原件相同；除 `document.xml`、批注相关新部件、`[Content_Types].xml`、`document.xml.rels` 外，其余部件逐字节相同。不通过时抛错，接口返回 500“生成批注版时校验未通过，未输出文件”，日志只记任务编号与原因代码。

### 3.2 接口（`export/router.py`）

`GET /runs/{rid}/annotated?file_seq=N`

* 按现有 `/runs/{rid}/report` 的方式校验 owner（非本人 404）。
* 条件：轮次已完成或部分完成；该文件为 .docx，或为 DOC / WPS / RTF 且有转换结果；原件未删除；本轮有可批注的问题。不满足时 409，文字分别为“本轮尚未完成”“该文件格式不支持批注版，只能导出审核报告”“原件已按保留期删除，不能生成批注版”“本轮没有可批注的问题”。
* 返回文件：`{原文件名去扩展名}_商衡SI批注_{yyyyMMdd}.docx`（日期按 Asia/Shanghai）；响应头 `X-SI-Total`、`X-SI-Unanchored`（只有数字）。
* 写审计日志 `export_annotated`（轮次、文件序号、总数、未精确定位数），不含文件名与内容。

### 3.3 测试（`backend/tests/test_annotated_export.py`）

1. 一份含 3 条问题（1 条精确、1 条回退、1 条已忽略）和 1 条本地校验结果的虚构任务：返回的文件有 3 条批注、作者“商衡SI”、批注中为真实名称（不含代号）；正文文字与原件相同；`X-SI-Unanchored` 为 1。
2. 他人的轮次 404；PDF 文件 409。
3. 旧格式：用 `backend/tests/fixtures/legacy/` 中已有的 .doc 样本（模拟转换结果即可，不调用 converter）走 3.0 保存后能生成批注版；原件按保留期删除后转换结果同时删除。

**文件范围**：`backend/app/export/annotated_docx.py`（新）、`backend/app/export/router.py`、`backend/app/tasks/pipeline.py`（保存转换结果）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/`（一个新迁移）、`backend/app/backup/retention.py` 与任务删除处（一并删除转换结果）、`backend/app/tasks/service.py`（文件列表加 `annotatable`）、`backend/tests/test_annotated_export.py`（新）。

## 四、导出入口（V3-C-04，GPT-6.1 Sol，medium）

* 审核结果工具条的“导出报告”改为下拉按钮“导出”：
  * 审核报告（现有）；
  * 每个可生成批注版的文件一项“批注版：{文件名}”（文件名过长省略；任务详情接口为每个文件返回 `annotatable: bool`）；不可生成的文件不列出，下拉底部一行次要色说明“批注版支持 Word 与 WPS 文字文件；PDF、图片、表格只能导出审核报告”。
* 点击批注版先弹出确认：“批注版含内部审核意见，请勿直接发给对方。”（按钮“导出批注版”“取消”）；之后用 `utils/save.ts` 的 `saveBlob` 保存（有工作文件夹时写入文件夹）。
* 完成后提示：“已生成批注版，共 N 条批注”；`X-SI-Unanchored` > 0 时改为持续显示的 `el-alert`（可关闭）：“共 N 条批注，其中 M 条未能精确定位，已挂在所在段落开头。”

**文件范围**：`frontend/src/components/ResultToolbar.vue`、`frontend/src/api/report.ts`、`frontend/src/api/tasks.ts`（类型加 `annotatable`）、`frontend/src/views/ReviewResult.vue`（提示显示处，如需要）。

## 五、集中测试（V3-C-05，GPT-6.1 Sol，high）

1. 整仓全量一次；前端 build。
2. 浏览器：导出菜单、确认弹窗、保存与取消、未精确定位提示。
3. 用虚构样本在本机候选环境（模拟模型）各导出一份批注版，交用户用 Word 打开目视核对：批注位置、批注文字、正文未变。
4. 经用户同意，用 Z-03 的真实样本在本机用真实模型审核一次（新提示词），报告只写数字：问题总数、精确定位数、回退数及原因分布（找不到 / 多处 / 结构不支持）、quote 与原文一字不差的比例。这些数字决定 V3-D 是否值得做、怎么做。
5. 写 `docs/v3/reports/V3-C-test.md`（写短），停下交 Opus。

## 六、审核与合并（V3-C-06，Opus，high）

审阅 C-01～C-05；合并第七节；根据第五节第 4 条的数字给出 V3-D 细化建议，交用户决定。

## 七、规格变更（V3-C-06 合并）

* **1.7**：“带批注或修订的 Word 原文导出”改为“带修订的 Word 原文导出（V3-D）”。
* **8.8**：DOC、WPS、RTF 的转换结果加密保存，与原件同保留期，用于批注版。
* **18.6** task\_files：增加 `converted_storage_key`。
* **11.3**：`quote`、`suggested_text` 的约束按第一节。
* **14**：新增 14.1“批注版”：范围（第〇节第 3、4 条）、文件名、批注格式、定位与回退规则、逐字校验、不保存、审计。
* **17.8 / 17.3**：批注版只在内存生成，不落盘。
* **19.5**：增加 `GET /runs/{rid}/annotated`。
* **23.2**：增加 T-65（批注版正文与原件逐字一致、批注为真实名称）、T-66（他人轮次 404、非 .docx 409）。

## 八、任务拆分

| 编号 | 任务 | 模型 | 强度 | 依赖 |
|-|-|-|-|-|
| V3-C-01 | 提示词约束（第一节） | GPT-6.1 Sol | medium | V3-B-06（或用户调整顺序后的 V3-A-09） |
| V3-C-02 | 原件定位层（第二节） | GPT-6.1 Sol（测试先行） | xhigh | V3-C-01 |
| V3-C-03 | 批注版生成与接口（第三节） | GPT-6.1 Sol | high | V3-C-02 |
| V3-C-04 | 导出入口（第四节） | GPT-6.1 Sol | medium | V3-C-03 |
| V3-C-05 | 集中测试与真实样本定位率（第五节） | GPT-6.1 Sol | high | V3-C-04 |
| V3-C-06 | 审核、合并规格、V3-D 建议（第六、七节） | Opus | high | V3-C-05 |
