# V2-B 集中测试记录

2026-10-24 · GPT-6.1 Sol · V2-B-08 · **进行中，未通过，不能交作批次验收通过**。

实现基线：main，B06 收尾 `b60b201`，B07 初稿 `ca93837`、收尾 `10d3aa3`。本轮仅记录集中验证结果；未修改保护用例、模型阈值、冻结样本、设计文档或批次外源码。B09 尚未开始。

## 1. 环境与证据

Windows 开发机；后端只在 Docker Python 3.12 中执行。全量使用 `aireview-backend-test:b06`，整仓挂载 `/srv`、工作目录 `/srv/backend`，测试数据库为独立临时库。实体模型为已确认的 GLiNER multi-v2.1 FP32 ONNX，非云端模型。

本轮证据根目录（下文文件名均相对此目录）：

`C:/Users/Administrator/.codex/visualizations/2026/10/24/01a16cb3-2ba5-7900-ac3d-1e2c587181ad`

退出码单独落盘并回读。工具调用成功不视为测试通过。

| 验证层 | 结果 | 证据 |
|-|-|-|
| B07 用户授权后的完整定向组 | 244 passed、1 warning，251.13 秒；exit 0 | `b07-closeout.log/.exit` |
| B07 最终前端类型检查与构建 | exit 0；保留大 chunk 警告 | `b07-final-build.log/.exit` |
| B08 整仓全量 | **2538 passed、9 failed、1 warning，1120.91 秒；exit 1** | `b08-full.log/.exit` |
| B08 断网 OCR / NER / T-20 | **56 passed、2 warnings，41.77 秒；exit 0** | `b08-offline.log/.exit` |
| B08 按类别逐出现位置召回 | **3 处地址未完整覆盖；exit 1** | `b08_recall.py`、`b08-recall.json/.log/.exit` |

断网命令为 `docker run --rm --network none --cpus 8 -v "${PWD}:/srv:ro" -w /srv/backend aireview-backend-test:b06 pytest -q tests/test_ocr.py tests/test_ner.py tests/test_no_network_imports.py`。两条 warning 分别是既有 Starlette/httpx 弃用提示和只读挂载不能写 pytest 缓存，未跳过测试。

## 2. 全量失败及定位

七项分段审核测试均在创建审核轮次时被 `SUSPECTS_PENDING` 拦截，尚未进入各自要验证的分段行为：

- `test_review_job.py::test_t12_chunked_review_success`
- `test_review_job.py::test_chunk_failure_gives_partial`
- `test_review_job.py::test_bad_output_chunk_counts_tokens`
- `test_review_job.py::test_all_chunks_fail`
- `test_review_job.py::test_block_in_second_chunk_stops_run`
- `test_review_job.py::test_cap_reached_mid_run`
- `test_review_job.py::test_progress_visible_while_running`

将其 `long_contract()` 的相同虚构文本直接送入真实 engine：20 个条款中的通用词“双方”各产生一条 `reason=ner` 疑似遗漏。诊断脚本在断网容器运行，`b08-chunk-diagnose-final.log/.exit` 为 exit 0；首次外部脚本缺 PYTHONPATH 的失败也保留。建议仅在模型停用词中追加“双方”，并增加规则不回退的测试；不要给上述七项统一模拟空 NER，也不要取消服务端疑似决定检查。见 V2Q-43，未实施。

另两项是新增高置信自动替换与旧“必须列疑似”的断言冲突：

- `test_suspects.py::test_r4_person_narrative_all_occurrences`：旧列表含“林虚”，当前已被真实 NER 自动替换。V2Q-39 只授权了 golden 内的例外，没有授权此独立测试。
- `test_v2b_derived.py::test_new_narrative_candidates[通知王建国，]`：原断言要求存在疑似，当前 PERSON / ner 完整覆盖该姓名。

建议对这两个明确姓名接受同类别 NER 完整覆盖作为更严格结果，保留原始文本及其余断言；不扩大至其他参数、不模拟空模型。需单独授权，见 V2Q-44。本轮均未改。

## 3. 按类别召回

口径：按出现位置计数，必须由正确类别替换项完整覆盖，或完整列为疑似；ORG 兼容 OUR。只替换地址的一部分不算命中。golden 的原文、标注与选型的八份叙述样本均未改。此统计是独立诊断，不替代 pytest 的保护断言。

| 样本范围 | 类别 | 应处理 | 已替换 | 列疑似 | 漏掉 |
|-|-|-:|-:|-:|-:|
| golden 全部样本 | ORG | 106 | 101 | 5 | 0 |
| golden 全部样本 | PERSON | 25 | 19 | 6 | 0 |
| golden 全部样本 | REFNO | 14 | 14 | 0 | 0 |
| golden 全部样本 | ACCOUNT | 10 | 8 | 2 | 0 |
| golden 全部样本 | AMOUNT | 39 | 39 | 0 | 0 |
| golden 全部样本 | PRICE | 11 | 11 | 0 | 0 |
| golden 全部样本 | ADDR | 13 | 13 | 0 | 0 |
| golden 全部样本 | PHONE | 13 | 13 | 0 | 0 |
| golden 全部样本 | SWIFT | 3 | 3 | 0 | 0 |
| golden 全部样本 | IDNO | 6 | 6 | 0 | 0 |
| golden 全部样本 | EMAIL | 5 | 5 | 0 | 0 |
| golden 全部样本 | IM | 3 | 3 | 0 | 0 |
| golden 全部样本 | PROJECT | 1 | 1 | 0 | 0 |
| golden 全部样本 | POSTCODE | 4 | 4 | 0 | 0 |
| golden 全部样本 | URL | 1 | 1 | 0 | 0 |
| 八份叙述样本 | ORG | 16 | 4 | 12 | 0 |
| 八份叙述样本 | PERSON | 16 | 13 | 3 | 0 |
| 八份叙述样本 | ADDR | 8 | 5 | 0 | **3** |

三处未完整覆盖的冻结地址（字符坐标为 Python 半开区间）：

1. `letter-cn`，27..54：`江苏省南京市江宁区星澜路 27 号 3 栋 502 室`。当前 ADDR 项 22..44，错误纳入前导叙述且未覆盖楼栋和房间尾部。
2. `mail-en`，111..160：`27 Cedar Quay, Suite 405, Bristol, United Kingdom`。当前没有覆盖该完整地址的替换或疑似。
3. `minutes-en`，178..223：`84 Alder Lane, Floor 2, Leeds, United Kingdom`。当前仅有 178..191 的街道部分。

这不满足 B08“漏掉数为 0”。选型时已观察到的模型地址局限不能豁免集中验收门槛。修复需最小扩展 detect.py / 对应测试，先明确英文无标签完整地址的保守识别条件，并保留城市、港口等 must_keep 保护；见 V2Q-45。

## 4. 浏览器与真实接口探测

使用 Tabbit，任务名“商衡SI V2-B 浏览器验证”。开发机独立 compose 项目 `aireview-v2b-local`，独立数据库、主密钥、文件卷，app / worker / 模拟网关在内部网络；网关只暴露本机 127.0.0.1:18088，前端 15174。账号、DOCX 和 NOTE 均为虚构内容，未使用真实业务文件；没有调用真实云端模型、没有连接正式服务器。

| 项目 | 已核实结果 | 证据 |
|-|-|-|
| 七组图例及新金额组 | 四宽均存在七个类别组，另有疑似标识 | `b08-four-widths.receipt.json` |
| 四种宽度 | 1280×800、1366×768、1440×900、1920×1080，确认页与主体设置页 clientWidth=scrollWidth，无整页横向溢出 | `b08-four-widths.receipt.json`、`b08-own-add.receipt.json` |
| 手动类别 | 弹窗十类；项目类选择、POST 200、代号刷新 | `b08-ten-categories.receipt.json`、`b08-manual-save.receipt.json` |
| 对照区人名撤销 | 悬停、二次确认、POST 成功，正文与 NOTE 同名全文恢复；无替换段默认隐藏，展开后核实 | `b08-revoke-verify.receipt.json` |
| 手动项目撤销 | 二次确认、POST 200、标记移除；但出现互相矛盾的两个成功提示 | `b08-manual-revoke.receipt.json` |
| 我方主体 | 空列表→虚构主体新增 201→显示记录人与时间→确认删除 204→恢复空列表 | `b08-own-empty.receipt.json`、`b08-own-add.receipt.json`、`b08-own-delete.receipt.json` |
| 新原因 | 实际疑似列表显示“模型识别”；字号/叙述原因映射已有源码与定向测试，尚未完成全部实际视图逐项复核 | 浏览器 DOM 与已有实现 |
| 真实接口审核 | 原生确认逐项处理后 POST /runs 201，进入审核结果页，模拟网关正常返回；无浏览器错误或异常 HTTP | `b08-send-review.receipt.json`、`b08-reviewed.receipt.json` |
| 追问模型疑似弹窗 | 两条“模型识别”原因，未决定前确认按钮禁用；逐条保留通用误报后发送 200。四宽弹窗实际边界均在视口内 | `b08-chat-ner-probe.receipt.json`、`b08-chat-send.receipt.json` |

四宽目前只有 DOM 和布局尺寸证据。截图调用超时，浏览器禁止强制前台；未绕过限制，**不能声称已完成逐图视觉检查**。

撤销提示缺陷：子组件撤销成功后 emit done，父 `ConfirmStep.vue::onManualDone` 无条件提示“已设为敏感信息”，子组件又提示“已撤销，脱敏结果已更新”。两句由 MutationObserver 实测同时出现。父组件不在 B07 文件范围，B08 也只有报告产出；需最小范围扩展后才修复，见 V2Q-46。

上传的组合 DOCX `b08_browser.docx` 实际探测结果：

- 签约方、机构全名及“澄川虚构”派生简称被替换；无币种标签金额 98000 被替换。
- 王建国、沈柏宁、唐知远等自动替换，苏映秋及研究机构等未自动替换的结果列疑似；后续原生界面按类别确认，POST 200。重算掩码改变后部分实体由自动替换变疑似，仍由服务端强制逐项处理。
- 英文称谓后的 Lena Fictive、外文 Nordic Fictive Systems AB、张经理、中国无标签地址及 fiction_demo88 均有替换；“采购方”“Dear Dr.”“人工”等通用文字被模型列疑似，作为虚构探测中的误报记录。
- 组合项目行“项目名称：江北市虚构医院信息化改造项目”当前先替换我方医院片段，项目尾部仍保留。独立 golden 项目样本通过不能证明这一重叠组合通过；需一并核实并补重叠保护，见 V2Q-45。
- 正文与 NOTE 的撤销全文生效已核实；手动项目新增与撤销均走真实接口。

已完成真实接口 → 模拟网关的审核与追问：已知任务实体在追问中直接复用代号；新增虚构人名 Mira Fiction 被脱敏；“采购方”“Dear Dr.”两条通用误报在追问中显示模型识别原因，未逐项处理前不能发送。1280×800、1366×768、1440×900、1920×1080 下实际 `.el-dialog` 宽640、顶部24、底部502.17，整页无横向溢出。源码对话框有先前同名 role 覆盖层，初次宽度探测取到的是覆盖层，后一次已明确取内部 `.el-dialog`，以 `b08-chat-send.receipt.json` 为准。

对本轮独立库的两条 `LlmCall` 做只读检查：review/chat 均为 sent，15项应替换的虚构机构、人名、账号、金额、地址原值在请求中零命中；`b08_check_outbound.py`、`b08-outbound.log/.exit`，exit 0。用户已明确撤销的“周示例”、通用文字与项目重叠未覆盖的尾部不纳入零命中清单；后者仍是未修复缺陷，不能因此声称所有应脱敏内容都已覆盖。既有 A03 定向组通过，整仓没有 A03 失败。

浏览器自动化中两次等待失败也保留回执：一次错误地等“所有疑似处理完才启用”的提交按钮，另一次循环到已忽略表的隐藏行；均先只读核实已完成的动作后继续，没有重复执行撤销或上传。最终审核与追问回执为成功，不能把自动化等待超时写成产品通过或产品失败。

## 5. 资源、目标机与后续

B06 的开发机证据：8 CPU 断网万字模型中位 8.8018 秒，单进程峰值增 1,990,025,216 字节，镜像新增层解压增 1,207,033,856 字节，见 B06 收尾记录。这些不是 B08 整机容量验收。

20 页合同 worker / app 常驻内存与解析耗时对比、追问冷启动与热请求、目标机压力场景仍未完成。172.16.11.252 每次测试需先告知时间段、时长与容器并取得同意；本轮没有申请该次授权，**没有连接或测试该服务器**，没有正式服务/数据操作。

本机隔离环境已执行 `docker compose -f <证据目录>/b08-browser-compose.yml down`，回读 `b08-local-down.exit` 为0，五个本轮容器及两条本轮网络已移除；没有使用 `-v`，保留虚构测试数据卷便于复现。前端15174会话已结束；既有15173与其他测试栈未操作。Tabbit `finish` 返回 finished=true、forcedCleanup=false，保留本轮标签供续查；无截图产物。

下一步：先取得 V2Q-43～46 的最小修复与保护断言授权，保留 V2Q-42 待确认；先写失败回归再修复并跑相关定向组，之后重跑 B08 全量、召回与浏览器未完成项。待本机验证具备通过条件，再按 V2Q-38 单独请求低峰时段整机容量测试授权。所有集中门槛通过后才标记 B08 已完成，再停交 B09 Opus 审核。
