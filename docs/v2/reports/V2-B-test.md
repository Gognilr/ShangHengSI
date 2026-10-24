# V2-B 集中测试记录

2026-10-24 开始，2026-10-25 更新 · GPT-6.1 Sol · V2-B-08 · **本轮授权修复及本机自动化通过；集中验收仍未完成**。

初轮实现基线：main，B06 收尾 `b60b201`，B07 初稿 `ca93837`、收尾 `10d3aa3`。首轮只记录集中验证结果；后续按用户授权的修复见第0节。模型阈值、冻结样本与标注、设计文档未改。B09 尚未开始。

## 0. 按 544bd3d 及 V2Q-47 授权继续修复的当前结果（续作至2026-10-25）

本节是后续验证，以此节为当前状态；第一至五节保留首轮失败与诊断历史。用户已确认 V2Q-43～48，明确不包括 V2Q-42 或正式服务器容量测试。实现提交 `b35fe05`、`d9e9311`、`f8e0418`。

- V2Q-43：停用词只追加“双方”一行，仍是整段相等过滤；含“双”的虚构姓名张双宁保持可识别/列疑似。原七项分段审核测试没有修改，已在定向组通过。
- V2Q-44：只对林虚与“通知王建国，”接受完整 PERSON / ner 替换；自动替换分支另查出口无姓名，其余旧姓名、参数和原断言保留，不模拟空模型。
- V2Q-45：先固定三个冻结地址的精确起止区间及项目含我方主体的完整替换/逐字还原；补楼栋房间空格、保守英文门牌道路/套间楼层组合。项目内部项让位于整段 PROJECT，主体在其他位置仍为我方代号。冻结原文及标注未改。
- V2Q-46：三个组件的 done 事件携带 add/revoke 操作类型，父组件只显示一条对应提示，服务端完全不改。

| 本轮检查 | 当前结果 | 证据 |
|-|-|-|
| 测试先行 | 初稿6 failed/9 passed；其中5项复现产品问题，另1项为新增夹具解包错误，已修正；旧全量已独立复现七项 SUSPECTS_PENDING | `b08-repair-red.log/.exit` |
| 旧定向组合 | 274 passed/1 failed；唯一失败为上述新增夹具错误，七项旧分段与两项获授权姓名用例均通过 | `b08-repair-targeted.log/.exit` |
| 修正后的新增回归完整组 | **15 passed、1 warning，25.76秒，exit 0** | `b08-repair-closeout-focused.log/.exit` |
| 地址/engine定向 | 151 passed、1 deselected（此组未接数据库，明确不含long_contract），exit 0 | `b08-repair-engine.log/.exit` |
| 前端 build | **exit 0**，仅既有大chunk/构建耗时提示 | `b08-repair-build.log/.exit` |
| b35fe05修复后整仓全量 | **2561 passed/1 failed，exit1**；唯一原始NER输入对拍失败，保留该次失败回执 | `b08-full-repair.log/.exit` |
| f8e0418最终整仓全量 | **2567 passed、1 warning，1124.59秒，exit0**；Q47/Q48均已包含，无跳过/排除 | `b08-full-final.log/.exit` |
| 最终断网OCR/NER/T20 | **56 passed、3 warnings，37.56秒，exit0**；额外为只读pytest缓存warnings | `b08-offline-final.log/.exit` |
| 修复后类别召回 | **全部类别漏掉0，exit 0**；golden统计与原表一致；叙述ORG 16=替换3+疑似13，PERSON 16=13+3，ADDR 8=8+0；合并ADDR 21处全部替换 | `b08_recall_repair.py`、`b08-recall-repair.json/.log/.exit` |

机构名从4处自动替换/12处疑似变为3/13，是完整地址掩码改变后模型上下文的变化；完整覆盖仍为16/16，不改阈值或标注。

浏览器新建虚构任务 `28e8a023-1b3a-46fe-a59c-923d608ed97f`，项目行已为“项目名称：项目_1”（201上传后真实worker解析）。原文弹窗新增项目仅一条“已设为敏感信息”，撤销仅一条“已撤销，脱敏结果已更新”；对照区新增与撤销也分别只有对应单条提示，均POST 200。证据：`b08-repair-upload.receipt.json`、`b08-repair-toasts.receipt.json`、`b08-repair-compare-add.receipt.json`、`b08-repair-compare-revoke.receipt.json`。

四宽检查在每个目标尺寸稳定后重新选择文字，十类浮层均在视口内，页面无横向溢出：1280×800（top440/bottom642.80）、1366×768（408/610.80）、1440×900（540/742.80）、1920×1080（720/922.80），见 `b08-repair-picker-static-widths-final.receipt.json`。早期宽度探测没有等待resize生效，首项实际为1440；不能按1280记通过，以最终回执为准。另观察到浮层打开后再缩小窗口时坐标不会重新定位，可超出新视口；本次按已定窗口尺寸打开的验证通过，动态缩窗问题保留为界面观察项，未超出V2Q-46事件授权擅改样式。

Tab焦点经过控件与替换项，DOM计算样式为solid外环；七组图例仍在，浏览器pageerror为空。源码自检未引入颜色常量、v-html、console或业务存储。截图再次在字体加载完成后超时，见 `b08-repair-screenshot.receipt.json`，没有截图，不能声称逐图视觉验收完成。

**V2Q-47 已按用户工作区授权完成，提交 `d9e9311`**。先以 `b08-q47-red.log/.exit=1` 固定2 failed/2 passed；实现只改 `_redact`：用同一 `detect.find_projects`，仅完整包含的对照表/主体区间让位，部分交叉及其他类别原有优先级保留；原项目复用代号、新项目走现有code_for，new_entries仍只返回实际使用的代号。新增回归覆盖同句主体单独出现、原代号复用、逐字还原、部分交叉、金额优先级与真实预览/发送。`test_chat.py`、新增项目、NER接口、NOTE、golden、A03整组 **168 passed、1 warning、247.81秒，exit0**（`b08-q47-targeted.log/.exit`），原保护断言未改。

浏览器对新项目预览为“项目名称：[项目_Q1]；我方负责验收。”，原生确认后POST 200；对应审核/追问两条LlmCall均sent，17项检查原值（包括完整旧项目和新项目尾部）零命中，`b08-outbound-repair.log/.exit=0`。用户明确撤销的周示例仍不计入应替换清单。浏览器任务执行器中途丢失后曾取得about:blank，未发生原任务写入；按skill的diagnose/tabs恢复实际标签、只读确认两条pending仍在后才继续，没有重复上传/撤销。正常finish并停止本轮compose，保留卷和证据（`b08-repair-browser-finish.json`、`b08-repair-local-down.log/.exit=0`）。

**V2Q-48 已确认并修正，以下为前次失败与定位**：上述b35fe05基线全量实际已完整结束，**2561 passed/1 failed、1 warning、1138.92秒，exit1**；尝试停止时容器已自动删除，并没有被中止。唯一失败为 `test_reference_parity`，letter-cn分差0.396871。断网复跑同样 **55 passed/1 failed、3 warnings，25.89秒，exit1**（`b08-offline-repair.log/.exit`）；额外warnings是只读pytest缓存，不能掩盖对拍失败。根因是测试的corpus()按当前规则生成遮盖输入，地址补全后已不再是原预测的输入。

已使用06a留存的 `corpus-final.json` 原始391条断网核实，原预测按测试既有0.3口径与仓库reference_predictions逐项完全相等，输入ID集合一致；原输入的实体键全部一致、最大分差 **5.960464477539063e-08**，`b08-parity-original-input-final.log/.exit=0`。原corpus SHA256 `63e2892c2ce663229d3ffaa5d95b34cfbd764f2d08f852b472a8e6e63fcb9ab1`；原预测文件SHA256 `32c5ca68949d73bd13bc8f0d134d7f0e6cd3de08718376add39312d90bd39bcf`。诊断初稿未先按既有0.3口径筛选原预测，直接字典比较失败的两份回执亦保留，最终明确使用原口径，未改变产品阈值。

用户于2026-10-25确认仅冻结原始输入，已提交f8e0418：原样复制reference_corpus.json（156225字节，SHA与原文件一致），只改corpus()辅助函数并新增固定SHA断言，391数、实体键/分数断言、1e-4误差线、预测JSON和其他样本/留出集均不变。修正后最终断网OCR/NER/T20 **56 passed、3 warnings、37.56秒，exit0**（b08-offline-final.log/.exit）；只读挂载的缓存warnings保留。包含Q47/Q48的最终整仓 **2567 passed、1 warning、1124.59秒，b08-full-final.exit独立落盘并回读0**，没有跳过或排除用例；唯一warning为既有Starlette/httpx弃用提示。最终命令是整仓挂载的Docker Python 3.12、8 CPU、pytest -v，b08-full-final.log/.exit保存完整回执。

本机20页资源对比使用 V2-A 末期 `9942607` 的只读源码快照与当前实现，在同一Python 3.12测试镜像、同一虚构PDF、8 CPU条件下执行，完整走解析/本地检查/加密存储/脱敏事务，每次独立临时库，完成即删。PDF为20页、360节点、10625字，SHA256 `46e6e98c36cfabb2809d3cb64ba1b0751e56e60b9fe378f34d4b30d1f9208b22`。后一次复测将全量测试固定CPU0～7、资源容器固定CPU8～15，但仍共享开发机内存和I/O，不能当成目标机容量验证。

| 本机测量 | V2-A | 当前V2-B |
|-|-:|-:|
| 20页完整解析3次秒数 | 1.7687 / 1.6466 / 1.3816 | 28.8988 / 26.9931 / 27.4825 |
| 解析中位秒数 | 1.6466 | 27.4825（增加25.8360） |
| worker处理后RSS | 175,509,504字节 | 1,804,992,512字节 |
| worker峰值RSS | 176,390,144字节 | 2,103,468,032字节 |
| 单独app进程500字预检冷请求 | 0.0147秒（不含模型） | 17.8526秒 |
| 单独app进程500字预检热请求中位 | 0.0044秒 | 1.6895秒 |

资源脚本 `b08_resource.py` / `b08_resource_isolated.py` 与 `b08-resource-*-isolated.json/.log/.exit` 留证；这些脚本退出0仅表示测量成功。20页中位增加与单进程内存数据在范围内，但本机冷/热追问高于15秒/1秒目标线，**性能不能报通过**。首次未固定CPU的并行测量更慢（冷34.45秒、热中位2.22秒）；20页三次为20.1164/20.9481/32.6015秒，其中一轮相对V2-A中位增加31.4880秒，超过30秒线。分离CPU后的三轮见表，不能据较好的中位结果声称性能稳定达标；原回执均保留。

另以独立app进程、独立worker子进程和临时库读取相同20页任务，防止把同一进程的worker模型内存记为app内存：原文nodes（360条）、签约方、脱敏明细、出口预览四个真实接口均200；V2-A读取后RSS为145,674,240字节，V2-B为171,732,992字节；V2-B首次执行500字追问预检后RSS为2,121,453,568字节（相对读取后增加1,949,720,576字节），峰值2,123,755,520字节。该次冷请求16.9232秒、热请求中位1.7602秒，同样未达到目标线。此处测的是本地预检，未假造reviewed状态、未绕过网关发送云请求。脚本 `b08_app20_resource.py`，`b08-resource-V2-A-app20.json/.log/.exit` 和 `b08-resource-V2-B-app20.json/.log/.exit` 均exit0；临时库已在finally删除。

为排除本轮全量/浏览器多模型的干扰，在它们结束并down后，以8 CPU、不固定CPU集合重做同一20页app测量：读取后RSS171,810,816字节，首次预检后2,101,006,336字节，峰值2,124,001,280字节；冷18.3146秒，热1.2777/1.2206/1.3372秒（中位1.2777），仍高于目标线。`b08_app20_resource_idle.py`、`b08-resource-V2-B-app20-idle.json/.log/.exit=0`。这仍是开发机预检，不能据此推断目标28vCPU整机结果，也不能据内存通过把耗时写成通过；未擅自调整线程、模型、阈值或增加性能修复范围。

本轮明确不包括正式服务器容量测试，没有连接或执行。**当前下一步仍为B08**：先定位并提出不牺牲识别效果的冷/热预检耗时改进方案，未经新范围授权不改NER实现；补视觉复核及动态缩窗浮层定位观察项，再由用户另行安排目标机容量验证。B08不标记完成，B09未开始，V2Q-42仍待确认。

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
