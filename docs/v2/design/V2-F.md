# V2-F 脱敏减负与修复（V2.1）

2026-10-30 · Opus 5.5 细化（V2-F-00）· 用户已确认（2026-10-30，V2Q-182～V2Q-184 按建议）· **已完成**（V2-F-06 审核、规格已合并进 docs/SPEC.md，版本 V2.1，标签 `v2.1.0`）

用户在 Z-03 真实样本核对中反馈：疑似遗漏只能逐条点，一份文件几十组，人工操作太多。本批与 V2-B 遗留的 V2Q-180、V2Q-181 一起处理，完成后版本为 V2.1（标签 `v2.1.0`）。执行规则同 [TASKS-V2.md](../TASKS-V2.md) 第一节；本批问题见 [questions/V2-F.md](../questions/V2-F.md)。

## 〇、结论与共同约定

### 0.1 实测依据（Z-03 两份真实样本，只记数字）

| 指标 | 样本 1 | 样本 2 |
|-|-:|-:|
| 疑似组数 | 14 | 53 |
| 其中用户忽略 | 14 | 53（另有 5 组为真名称，已替换） |
| NER 中置信误报组 | 9 | 41 |
| 误报的 NER 分数区间 | 0.51～0.93 | 0.23～0.95 |
| 真名称的 NER 分数区间 | — | 0.62～0.94 |
| 两份样本被忽略的词重叠 | 1 组 | |

结论：

1. **不调 NER 门槛**：误报与真名称的分数区间完全重叠，抬高门槛会漏掉真名称。
2. **英文常用词过滤有效且不伤真名称**：把首字母大写规则已有的“每个词都是英文常用词（或在正文中以小写出现过）时不列”用到 NER 中置信结果上，模拟结果为样本 1 的 NER 误报 9 → 6、样本 2 为 41 → 13；两份样本中的真实机构名、人名均不受影响（唯一被过滤的已替换项是误判的通用检测角色）。疑似总组数约 14 → 11、53 → 25。
3. **剩下的靠多选忽略**：余下多为缩写（UHP、CNC 等）和带编号的标准名，逐条看一遍后多选忽略，点击次数从几十次降到几次。
4. **“记住忽略过的词”本批不做**：两份样本被忽略的词只重叠 1 组，跨文件收益未经证实，且一次误忽略会在以后所有任务中放行同一名称（V2Q-183）。F5 复测后再评估。

### 0.2 共同约定

1. **安全优先**：本批任何改动不得使出口复检命中数大于 0；高置信自动替换、签约方识别、出口复检规则不变。
2. **只修不加**：只做下表五项；不加开关、不加设置项。
3. **测试**：按 CLAUDE.md“永不过度设计、过度测试”。脱敏识别类用例写成 golden 样本标注；每个任务只跑相关测试文件；整仓全量只在 F5 跑一次。
4. **真实样本**：只在 F5 由用户提供原路径、在本机候选环境中使用，不出本机、不进仓库；报告只写数字。
5. **版本**：F6 完成时 SPEC 头部、CLAUDE.md 标题、登录页页脚改为 V2.1，打标签 `v2.1.0`。

| 项 | 内容 | 任务 |
|-|-|-|
| F1 | 零宽字符清除（V2Q-180） | V2-F-01 |
| F2 | 疑似遗漏减负规则（V2Q-182） | V2-F-02 |
| F3 | 疑似遗漏多选忽略 | V2-F-03 |
| F4 | PDF 图文混排页（V2Q-181） | V2-F-04 |
| F5 | 集中测试与真实样本复测 | V2-F-05 |
| F6 | 审核、合并规格、V2.1 版本与标签 | V2-F-06 |

## 一、零宽字符清除（V2-F-01，GPT-6.1 Sol，medium，测试先行）

从网页、微信复制进文件或输入框的名称中间夹零宽字符时，已记住的名称、签约方回扫与出口复检的真实值比对都对不上，原样外发。

* 新增 `strip_invisible(text: str) -> str`（放在 `backend/app/parsing/model.py`），删除 U+200B、U+200C、U+200D、U+2060、U+FEFF、U+00AD。
* `ParsedNode` 增加 `__post_init__`，对 text 调用该函数（各解析器一处生效，识别、复检、还原同时适用）。
* 用户直接输入的文字在入口处同样清除：新建任务与 PATCH 的 user\_note（tasks/router.py）、追问的 message（chat/service.py 的 preview 与 send）、智能助手的 message（assistant 的入口 schema 或 service）。
* 测试：golden 样本 `cases_zero_width`（.docx 解析路径用不到时可用 .txt 样本）：公司名、人名中各夹一个零宽字符，must\_replace 写不含零宽字符的名称；追问加一个用例：消息中的已记住名称夹零宽字符时被替换。

**文件范围**：`backend/app/parsing/model.py`、`backend/app/tasks/router.py`、`backend/app/chat/service.py`、`backend/app/assistant/`（只改消息入口一处）、`backend/tests/fixtures/redaction_golden/cases_zero_width.*`、`backend/tests/test_chat*.py`（只加一个用例）。

## 二、疑似遗漏减负规则（V2-F-02，GPT-6.1 Sol，high，测试先行）

1. **NER 中置信结果的常用词过滤**：NER 中置信结果（列为疑似遗漏的那部分）是英文片段、且每个词都在 `en_words()` 或本文件的小写证据（`lowercase_evidence`）中时，不列为疑似遗漏。判断复用 `suspects.py` 中首字母大写规则的同一函数，不另写词表。连字符复合词（Non-Financial）按组成词逐个判断，两条规则一致（V2Q-185）。高置信结果照常自动替换，不加此过滤。含汉字的片段不适用。
2. **中文通用角色词**：`resources/ner_stopwords.txt` 追加：卖家、买家、代理公司、代理出口公司、出口代理公司、第三方公证机构、公证机构、第三方检验机构；“公司、厂”之前的泛称表（`_GENERIC_BEFORE_COMPANY`）追加“代理、代理出口、出口代理”。再追加公共机关泛称：海关、海关 HS、海关HS、中国海关（2026-10-30 用户确认：真实样本中“海关 HS 编码：……”的“海关 HS”被 NER 列为疑似；HS 编码本身按 V2Q-39 保留不变）。
3. **测试**（golden 标注）：
   * `en_spec_suspects.yaml` 的 must\_keep 追加样本中已有的常用词术语：Bulk Density、Supplier Warehouse、Customs Warehouse、Non-Financial Penalty（以样本实际出现的写法为准）；Grain Stability、Specific Heat 不加（样本第 243 行连写表头中含非常用词 Dedusting，首字母大写规则按设计整段列为疑似，见 V2Q-185）；must\_suspect 原有三项（Velmora Refinery、Corvane Technical Office、Hardgrave Machine Method）必须仍然列出。
   * 新增中文样本 `cases_generic_roles`：“现指定某虚构公司为代理出口公司”“由代理公司承担”“卖家应”“第三方公证机构出具”“海关 HS 编码：2713129000”，must\_keep 这些通用词与该编码，must\_replace 其中的虚构公司全称。
   * 按设计有意改变的旧标注可以修改，报告中列出。

**文件范围**：`backend/app/redaction/suspects.py`、`backend/app/redaction/engine.py`（只改 NER 中置信结果转疑似处，如需要）、`backend/resources/ner_stopwords.txt`、`backend/tests/fixtures/redaction_golden/`。

## 三、疑似遗漏多选忽略（V2-F-03，GPT-6.1 Sol，medium）

* **接口**：`POST /tasks/{id}/redaction/suspects/batch`，请求 `{ids: [int], decision: "ignore"}`，1～200 个；只支持 ignore（替换要选类别，保持逐组）；任一 id 不属于本任务时整批 404、不改任何数据；成功后返回与单条决定相同的 RedactionOut。实现为对每个 id 调用现有 `decide_suspect(... decision="ignore")` 的忽略分支后统一重算一次。
* **界面**（ConfirmStep.vue 的疑似遗漏列表）：每组前加复选框，表头“全选”；列表上方“忽略所选（N 组）”按钮，点击后弹出确认：“所选 N 组将不替换，按原文发送给模型。请确认其中没有公司名、人名等敏感信息。”确认后调用批量接口。逐组的“替换 / 忽略”按钮保留。
* 测试：后端一个用例（批量忽略成功；含他人任务 id 时 404 且本任务无变化）；前端 build，遵守 docs/design/frontend.md。

**文件范围**：`backend/app/redaction/router.py`、`backend/app/redaction/engine.py`（如需加批量函数）、`backend/tests/test_redaction*.py`（只加一个用例）、`frontend/src/api/redaction.ts`、`frontend/src/components/ConfirmStep.vue`、`frontend/src/components/SuspectList.vue`（每组的行在此渲染；2026-10-30 用户确认补入范围）。

## 四、PDF 图文混排页（V2-F-04，GPT-6.1 Sol，high）

现状：去空白后可提取文字 ≥ 20 字的页面不做 OCR；若一页只有可选中的页眉、正文是图片，正文不进入审核也无提示（不泄露，影响审核完整性）。

1. **先实测**：用虚构内容生成一份两页 PDF 放入 `backend/tests/fixtures/`：第 1 页有可选中的页眉一行（≥ 20 字）+ 正文为图片；第 2 页为正常文字页。确认第 1 页正文当前未进入节点。实测与预期不符时停下报告。
2. **修复**：文字页中，图片覆盖面积 ≥ 页面面积 50% 且可提取文字 < 200 字时，对该页再做 OCR，只保留与已有文字块不重叠的 OCR 块（按 bbox 判断），追加为 ocr\_block 节点；该页计入 OCR 页数上限；警告“第 N 页为图文混排，已对图片部分做 OCR”。
3. 测试：上面的样本在 `test_parse_pdf.py` 中加一个用例：第 1 页正文出现在 OCR 节点中、页眉不重复、警告存在。

**文件范围**：`backend/app/parsing/pdf.py`、`backend/tests/test_parse_pdf.py`、`backend/tests/fixtures/`（新样本一份）。

## 五、集中测试与真实样本复测（V2-F-05，GPT-6.1 Sol，high）

1. 前端 build；整仓挂载全量一次（测试库必须是 aireview-db 镜像）。
2. 在本机用本批代码起候选环境（独立 compose 项目，模拟模型），用户提供 Z-03 的两份真实样本原路径，重新上传并解析。报告只写数字：疑似组数按原因（与 0.1 表对比）、自动替换数、被新规则过滤的组中是否有真名称（由用户在页面目视核对）、出口复检命中（应为 0）、全文还原规则外差异（应为 0）。
3. 写 `docs/v2/reports/V2-F-test.md`，写短。完成后停下交 Opus。

## 六、审核与发布（V2-F-06，Opus，high）

审阅 F1～F5；把第七节的规格变更合并进 docs/SPEC.md；SPEC 头部、CLAUDE.md 标题、登录页页脚改为 V2.1；打标签 `v2.1.0`，经用户同意后推送。正式发布包与服务器安装仍按 V2Q-179 延后。

## 七、规格变更（F6 合并进 docs/SPEC.md）

* **6.2 第二步第 4 项**：疑似遗漏可多选后“忽略所选”，弹窗确认；替换仍逐组选择类别。
* **8.5**：文字页中图片覆盖 ≥ 50% 页面且可提取文字 < 200 字时，对图片部分补做 OCR（计入 OCR 页数），并提示。
* **9.3 开头**：解析得到的节点文字与用户输入（补充说明、追问、助手消息）先删除零宽字符（U+200B～U+200D、U+2060、U+FEFF、U+00AD）。
* **9.5**：实体识别的中置信结果为英文片段且每个词都是英文常用词或在正文中以小写出现过时，不列为疑似遗漏。
* **19.4**：增加 `POST /tasks/{id}/redaction/suspects/batch`。
* **23.2**：增加 T-57（零宽字符）、T-58（疑似减负）、T-59（多选忽略）、T-60（图文混排页 OCR）。

## 八、任务拆分

| 编号 | 任务 | 模型 | 强度 | 依赖 |
|-|-|-|-|-|
| V2-F-00 | 细化（本文件、V2Q-182～184） | Opus | high | v2.0.0 |
| V2-F-01 | 零宽字符清除（第一节） | GPT-6.1 Sol（测试先行） | medium | V2-F-00 用户确认 |
| V2-F-02 | 疑似遗漏减负规则（第二节） | GPT-6.1 Sol（测试先行） | high | V2-F-01 |
| V2-F-03 | 疑似遗漏多选忽略（第三节） | GPT-6.1 Sol | medium | V2-F-02 |
| V2-F-04 | PDF 图文混排页（第四节） | GPT-6.1 Sol | high | V2-F-03 |
| V2-F-05 | 集中测试与真实样本复测（第五节） | GPT-6.1 Sol | high | V2-F-04 |
| V2-F-06 | 审核、合并规格、V2.1 版本与标签（第六节） | Opus | high | V2-F-05 |
